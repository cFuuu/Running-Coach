"""總覽「AI 分析」按鈕的執行層：在背景啟動 Claude Code 無互動 session（`claude -p`），
接本專案的 running-coach MCP server 查資料並寫出分析，串流回傳事件。

為什麼不走 `coach_chat`（Anthropic API）：Fu 不必另設 `ANTHROPIC_API_KEY`，
直接用本機已登入的 Claude Code（2026-10-14 Fu 決定）。兩條路徑並存：聊天面板
仍走 API，分析報告走 Claude Code。

⚠️ 安全邊界（與聊天面板相同的原則：分析只開放唯讀工具，D.1）
- `--tools ""`：關掉 Claude Code 全部內建工具（Bash、讀寫檔案、網路）
- `--allowedTools`：只預先核准 `READ_ONLY_TOOL_NAMES`
- `--disallowedTools`：寫入型工具（`WRITE_TOOL_NAMES`）明確列入禁止，連提供都不提供
- `--permission-mode dontAsk`：任何未核准的工具一律直接拒絕，不會卡在等人按允許
- `--strict-mcp-config`：只載入本專案的 `.mcp.json`，不吃使用者全域的其他 MCP server
- `--no-session-persistence`：不在 Claude Code 端留 session 紀錄（報告由本專案自己存）
2026-10-14 實測：要求它呼叫 add_context_note，結果被 dontAsk 拒絕、資料庫未被寫入。

本模組不 import fastapi，事件解析與指令組裝都是純函式，可用 unittest 直接測；
真正啟動子程序的部分以可注入的 `runner` 參數隔開，測試不需要真的跑 claude。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.main.python.mcp.server import READ_ONLY_TOOL_NAMES, WRITE_TOOL_NAMES, coach

# .mcp.json 裡本專案 MCP server 的名稱；Claude Code 的工具全名是
# mcp__<server>__<tool>。與 .mcp.json 的 key 不一致時工具會全部對不上
# （init 事件裡看得到實際載入的工具清單）。
MCP_SERVER_NAME = "running-coach"

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_MCP_CONFIG = PROJECT_ROOT / ".mcp.json"
DEFAULT_TIMEOUT_SEC = 300

# 單一工具結果送回前端時的字元上限：只為了在面板上顯示「查到了什麼」，
# 完整內容 Claude 自己看得到，前端不需要整包 JSON。
TOOL_RESULT_PREVIEW_CHARS = 1200

# 接在 coach() 行為約束層之後，只描述這個介面的事實，不新增、不放寬任何約束
# （約束只有一份，在 mcp/server.py；與 coach_chat._PANEL_CONTEXT_NOTE 同一原則）。
_ANALYSIS_CONTEXT_NOTE = """\
## 這個介面
你是由學員本機 dashboard 的「AI 分析」按鈕啟動的一次性分析，輸出會直接顯示成
一份分析報告，學員無法在這裡追問。

- **只有唯讀查詢工具。** 同步 Garmin、產課表、記軟性輸入、寫外部課表一律不可用；
  需要這些動作時，在報告裡明說請學員改用 Claude Code。
- 先用工具查齊需要的事實再下判斷；只查回答所需的工具（通常 4～6 個就夠），
  不要逐場細查每一筆活動，除非某場明顯異常。
- **這是簡易分析，學員要的是一眼看完的重點**（2026-10-15 學員回饋：長報告太複雜；
  第二版約 900 字仍嫌長）。依下面固定格式輸出，**全文不超過 250 字**，每點**一句、
  不超過 30 字**，用台灣繁體中文：

  **目前狀態**：一句結論。
  **注意**：最多 3 點。
  **接下來幾天**：最多 3 點，具體到哪天練什麼。
  **依據**：一行，最多 3 項關鍵數值，用「；」分隔。

  - 低信賴度的數值只在後面加「（低信賴）」，**不要解釋為什麼低**（學員想知道可以點工具明細）。
  - 無法評估的項目併進「注意」一句帶過，不另起段落。
  - 不要長段落、不要表格、不要重述課表全文或公式。
  此格式已涵蓋回答的組織方式（結論先行、可評／不可評、就地標註不確定性、事實清單），
  只是把篇幅壓到最小。
"""


@dataclass(frozen=True)
class AnalysisConfig:
    """分析端點的執行期設定（不 hardcode，從環境變數讀，見 .env.example）。"""

    claude_bin: str = "claude"
    mcp_config: Path = DEFAULT_MCP_CONFIG
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    model: str | None = None
    allow_private_network: bool = False

    @classmethod
    def from_env(cls) -> "AnalysisConfig":
        timeout_raw = os.environ.get("RC_ANALYSIS_TIMEOUT_SEC", "").strip()
        return cls(
            claude_bin=os.environ.get("RC_ANALYSIS_CLAUDE_BIN", "").strip() or "claude",
            mcp_config=Path(os.environ.get("RC_ANALYSIS_MCP_CONFIG", "").strip() or DEFAULT_MCP_CONFIG),
            timeout_sec=int(timeout_raw) if timeout_raw else DEFAULT_TIMEOUT_SEC,
            model=os.environ.get("RC_ANALYSIS_MODEL", "").strip() or None,
            # 與聊天面板同一個開關：分析同樣會把個人健康資料送出本機、消耗額度
            allow_private_network=os.environ.get("RC_CHAT_ALLOW_PRIVATE_NETWORK", "")
            .strip().lower() in ("1", "true", "yes", "on"),
        )

    def resolved_bin(self) -> str | None:
        return shutil.which(self.claude_bin)

    @property
    def disabled_reason(self) -> str | None:
        if self.resolved_bin() is None:
            return f"找不到 Claude Code（{self.claude_bin}），請確認已安裝並登入"
        if not self.mcp_config.is_file():
            return f"找不到 MCP 設定檔 {self.mcp_config}"
        return None

    @property
    def enabled(self) -> bool:
        return self.disabled_reason is None


def tool_full_name(name: str) -> str:
    return f"mcp__{MCP_SERVER_NAME}__{name}"


def tool_short_name(full: str) -> str:
    prefix = f"mcp__{MCP_SERVER_NAME}__"
    return full[len(prefix):] if full.startswith(prefix) else full


def build_system_prompt() -> str:
    """行為約束層原文 ＋ 分析介面補充（前段逐字等於 mcp.server.coach()）。"""
    return coach() + "\n" + _ANALYSIS_CONTEXT_NOTE


def build_command(config: AnalysisConfig, prompt: str) -> list[str]:
    """組 `claude -p` 指令。安全邊界見模組說明，每個旗標都不可省。"""
    allowed = ",".join(tool_full_name(n) for n in sorted(READ_ONLY_TOOL_NAMES))
    disallowed = ",".join(tool_full_name(n) for n in sorted(WRITE_TOOL_NAMES))
    cmd = [
        config.resolved_bin() or config.claude_bin,
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--tools", "",
        "--mcp-config", str(config.mcp_config),
        "--strict-mcp-config",
        "--allowedTools", allowed,
        "--disallowedTools", disallowed,
        "--permission-mode", "dontAsk",
        "--no-session-persistence",
        "--append-system-prompt", build_system_prompt(),
    ]
    if config.model:
        cmd += ["--model", config.model]
    return cmd


# --- 事件 ---


@dataclass
class AnalysisEvent:
    event: str          # tool_use / tool_result / text / denied / done / error
    data: dict[str, Any] = field(default_factory=dict)


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    return json.dumps(content, ensure_ascii=False)


def parse_stream_line(line: str) -> list[AnalysisEvent]:
    """把 claude stream-json 的一行轉成本端點的事件（2026-10-14 對真實輸出驗證過格式）。

    - assistant 訊息的 tool_use → tool_use；text → text（thinking 不轉出）
    - user 訊息的 tool_result → tool_result（內容截成預覽）
    - system/permission_denied → denied（工具被拒，照實告訴前端）
    - result → done（最終報告全文、回合數、耗時）
    無法解析或不需要的行回傳空清單。
    """
    line = line.strip()
    if not line:
        return []
    try:
        msg = json.loads(line)
    except ValueError:
        return []
    kind = msg.get("type")
    events: list[AnalysisEvent] = []
    if kind in ("assistant", "user"):
        for block in (msg.get("message") or {}).get("content") or []:
            btype = block.get("type")
            if btype == "tool_use":
                events.append(AnalysisEvent("tool_use", {
                    "id": block.get("id"),
                    "name": tool_short_name(block.get("name", "")),
                    "input": block.get("input") or {},
                }))
            elif btype == "tool_result":
                text = _result_text(block.get("content"))
                events.append(AnalysisEvent("tool_result", {
                    "id": block.get("tool_use_id"),
                    "is_error": bool(block.get("is_error")),
                    "preview": text[:TOOL_RESULT_PREVIEW_CHARS],
                    "truncated": len(text) > TOOL_RESULT_PREVIEW_CHARS,
                }))
            elif btype == "text" and kind == "assistant" and block.get("text"):
                events.append(AnalysisEvent("text", {"text": block["text"]}))
    elif kind == "system" and msg.get("subtype") == "permission_denied":
        events.append(AnalysisEvent("denied", {"tool": tool_short_name(msg.get("tool_name", ""))}))
    elif kind == "result":
        events.append(AnalysisEvent("done", {
            "report": msg.get("result") or "",
            "is_error": bool(msg.get("is_error")),
            "num_turns": msg.get("num_turns"),
            "duration_ms": msg.get("duration_ms"),
            "denied_tools": [
                tool_short_name(d.get("tool_name", "")) for d in msg.get("permission_denials") or []
            ],
        }))
    return events


# --- 執行 ---

Runner = Callable[[list[str], int], Iterable[str]]


def subprocess_runner(cmd: list[str], timeout_sec: int) -> Iterator[str]:
    """真正啟動 claude，逐行產出 stdout。逾時強制結束並丟 TimeoutError。

    用同步 Popen 而非 asyncio 子程序：uvicorn 在 Windows 上的事件迴圈未必
    支援 asyncio 子程序；FastAPI 會把同步產生器放進執行緒池逐段送出。
    cwd 設在專案根目錄，.mcp.json 裡的相對路徑（output/running_coach.db）才對得上。
    """
    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    deadline = time.monotonic() + timeout_sec
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            yield line
            if time.monotonic() > deadline:
                raise TimeoutError(f"分析超過 {timeout_sec} 秒未完成，已中止")
        proc.wait(timeout=max(1, deadline - time.monotonic()))
        if proc.returncode != 0:
            err = (proc.stderr.read() if proc.stderr else "")[:500]
            raise RuntimeError(f"Claude Code 結束碼 {proc.returncode}：{err.strip() or '無錯誤訊息'}")
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"分析超過 {timeout_sec} 秒未完成，已中止") from exc
    finally:
        if proc.poll() is None:
            proc.kill()


def run_analysis(
    config: AnalysisConfig, prompt: str, runner: Runner = subprocess_runner
) -> Iterator[AnalysisEvent]:
    """執行一次分析，依序產出事件；最後一定是 done 或 error 其中之一。"""
    finished = False
    try:
        for line in runner(build_command(config, prompt), config.timeout_sec):
            for ev in parse_stream_line(line):
                if ev.event == "done":
                    finished = True
                yield ev
        if not finished:
            yield AnalysisEvent("error", {"message": "Claude Code 沒有回傳分析結果"})
    except (TimeoutError, RuntimeError, OSError) as exc:
        # 報告已完整收到後才出錯（例如子程序收尾時的結束碼）不影響結果，不再發 error
        if not finished:
            yield AnalysisEvent("error", {"message": str(exc)})
