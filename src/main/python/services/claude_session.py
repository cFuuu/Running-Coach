"""Claude Code 無互動 session（`claude -p`）的共用基礎——AI 教練聊天（session 模式）
與「分析近況」都從這裡啟動子程序。

為什麼獨立一個模組：聊天（coach_chat／coach_chat_session）與分析（coach_analysis）
共用同一套指令組裝、子程序執行與執行鎖；放在任一邊都會造成 coach_chat ↔
coach_analysis 循環 import（coach_analysis 需要 coach_chat 的 DEFAULT_MODEL 與備忘）。
本模組**不 import 任何聊天／分析模組**，只依賴 mcp.server 的工具分類。

⚠️ 安全邊界（PLAN.md §10：對話框只開放唯讀工具＋白名單寫入）
- `--tools ""`：關掉 Claude Code 全部內建工具（Bash、讀寫檔案、網路）
- `--allowedTools`：只預先核准 `PANEL_TOOL_NAMES`（唯讀＋§10.2 白名單）
- `--disallowedTools`：其餘寫入型工具（`WRITE_TOOL_NAMES - PANEL_WRITE_TOOL_NAMES`）明確禁止
- `--permission-mode dontAsk`：任何未核准的工具一律直接拒絕，不會卡在等人按允許
- `--mcp-config` 傳 JSON 字串：只取設定檔裡 running-coach 那一項，args 末尾加
  `--panel`，讓 MCP server 進入 panel 模式：寫入型工具依 §10 收緊，且 server
  **自己移除**白名單以外的工具——不只靠 Claude CLI 的權限判斷（2026-11-01 資安
  稽核 NV-3：專案 settings 可能另有 allow 規則）
- `read_only=True`（「分析近況」用）：再加 `--read-only`，只留唯讀工具。按鈕啟動
  的分析沒有學員發言，任何寫入都不是學員要求的（NV-4／NV-5）
- `--strict-mcp-config`：不吃使用者全域的其他 MCP server
- `--no-session-persistence`：不在 Claude Code 端留 session 紀錄（問答只存本專案資料庫）

prompt 一律從 **stdin** 送進去、不放在命令列：聊天會把對話歷史帶進 prompt，
Windows 命令列上限約 32,767 字元，放在 argv 會直接啟動失敗（2026-10-27 實測
`claude -p` 不帶 prompt 參數時讀 stdin，UTF-8 中文正常）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from src.main.python.mcp.server import (
    PANEL_FLAG,
    PANEL_TOOL_NAMES,
    READ_ONLY_FLAG,
    READ_ONLY_TOOL_NAMES,
    WRITE_TOOL_NAMES,
)

# 設定檔裡本專案 MCP server 的名稱；Claude Code 的工具全名是
# mcp__<server>__<tool>。與設定檔的 key 不一致時工具會全部對不上
# （init 事件裡看得到實際載入的工具清單）。
MCP_SERVER_NAME = "running-coach"

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_MCP_CONFIG = PROJECT_ROOT / ".mcp.json"
DEFAULT_TIMEOUT_SEC = 300

# 同一時間只跑一個 Claude Code 子程序：聊天（session 模式）與「分析近況」共用這一把。
# 子程序吃資源也吃額度，重複按按鈕或同時發問不該疊出好幾個。忙碌時呼叫端回 409。
SESSION_LOCK = threading.Lock()
BUSY_MESSAGE = "教練正在回覆另一個問題或分析中，請等它完成再試"


@dataclass(frozen=True)
class ClaudeCodeSettings:
    """啟動 Claude Code 需要的設定（不 hardcode，從環境變數讀，見 .env.example）。

    環境變數沿用「分析近況」原本的名稱（RC_ANALYSIS_*）：聊天與分析用同一個
    claude 執行檔與 MCP 設定檔，不各設一份。
    """

    claude_bin: str = "claude"
    mcp_config: Path = DEFAULT_MCP_CONFIG
    timeout_sec: int = DEFAULT_TIMEOUT_SEC

    @classmethod
    def from_env(cls) -> "ClaudeCodeSettings":
        timeout_raw = os.environ.get("RC_ANALYSIS_TIMEOUT_SEC", "").strip()
        return cls(
            claude_bin=os.environ.get("RC_ANALYSIS_CLAUDE_BIN", "").strip() or "claude",
            mcp_config=Path(
                os.environ.get("RC_ANALYSIS_MCP_CONFIG", "").strip() or DEFAULT_MCP_CONFIG
            ),
            timeout_sec=int(timeout_raw) if timeout_raw else DEFAULT_TIMEOUT_SEC,
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


def panel_mcp_config_json(mcp_config: Path, *, read_only: bool = False) -> str:
    """讀 MCP 設定檔，只留 running-coach 那一項並在 args 末尾加 `--panel`
    （read_only 時再加 `--read-only`），回傳 JSON 字串。

    改傳字串而不是檔案路徑，是為了不必另外寫一份「panel 版」設定檔（兩份會不同步）。
    設定檔格式不對時丟 ValueError，訊息只帶檔名與缺的欄位，不回顯內容。
    """
    try:
        data = json.loads(mcp_config.read_text(encoding="utf-8"))
        entry = dict(data["mcpServers"][MCP_SERVER_NAME])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(
            f"MCP 設定檔 {mcp_config} 讀不到 mcpServers.{MCP_SERVER_NAME}"
        ) from exc
    args = list(entry.get("args") or [])
    for flag in (PANEL_FLAG, READ_ONLY_FLAG) if read_only else (PANEL_FLAG,):
        if flag not in args:
            args.append(flag)
    entry["args"] = args
    return json.dumps({"mcpServers": {MCP_SERVER_NAME: entry}}, ensure_ascii=False)


def build_command(
    settings: ClaudeCodeSettings, *, system_prompt: str, model: str, read_only: bool = False
) -> list[str]:
    """組 `claude -p` 指令（聊天與分析共用）。安全邊界見模組說明，每個旗標都不可省。

    read_only：只開唯讀工具（分析用）；否則開 PANEL_TOOL_NAMES（聊天用）。
    prompt 不在指令裡，由 runner 從 stdin 送入。
    """
    tools = READ_ONLY_TOOL_NAMES if read_only else PANEL_TOOL_NAMES
    allowed = ",".join(tool_full_name(n) for n in sorted(tools))
    disallowed = ",".join(tool_full_name(n) for n in sorted(WRITE_TOOL_NAMES - tools))
    cmd = [
        settings.resolved_bin() or settings.claude_bin,
        "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--tools", "",
        "--mcp-config", panel_mcp_config_json(settings.mcp_config, read_only=read_only),
        "--strict-mcp-config",
        "--allowedTools", allowed,
        "--disallowedTools", disallowed,
        "--permission-mode", "dontAsk",
        "--no-session-persistence",
        "--append-system-prompt", system_prompt,
    ]
    if model:
        cmd += ["--model", model]
    return cmd


# --- 執行 ---

# runner(cmd, prompt, timeout_sec) → 逐行產出 stdout。測試以假的逐行輸出注入。
Runner = Callable[[list[str], str, int], Iterable[str]]


def subprocess_runner(cmd: list[str], prompt: str, timeout_sec: int) -> Iterator[str]:
    """真正啟動 claude，prompt 由 stdin 送入，逐行產出 stdout。逾時強制結束並丟 TimeoutError。

    用同步 Popen 而非 asyncio 子程序：uvicorn 在 Windows 上的事件迴圈未必
    支援 asyncio 子程序；FastAPI 會把同步產生器放進執行緒池逐段送出。
    cwd 設在專案根目錄，設定檔裡的相對路徑（output/running_coach.db）才對得上。
    stdin 由另一條執行緒寫入：prompt 可能比管線緩衝大，在主執行緒寫會與讀 stdout 互鎖。
    """
    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    def feed_stdin() -> None:
        try:
            assert proc.stdin is not None
            proc.stdin.write(prompt)
            proc.stdin.close()
        except OSError:
            pass  # 子程序提早結束時管線已斷，結束碼會在下面回報

    writer = threading.Thread(target=feed_stdin, daemon=True)
    writer.start()
    deadline = time.monotonic() + timeout_sec
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            yield line
            if time.monotonic() > deadline:
                raise TimeoutError(f"Claude Code 超過 {timeout_sec} 秒未完成，已中止")
        proc.wait(timeout=max(1, deadline - time.monotonic()))
        if proc.returncode != 0:
            err = (proc.stderr.read() if proc.stderr else "")[:500]
            raise RuntimeError(f"Claude Code 結束碼 {proc.returncode}：{err.strip() or '無錯誤訊息'}")
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"Claude Code 超過 {timeout_sec} 秒未完成，已中止") from exc
    finally:
        if proc.poll() is None:
            proc.kill()
        writer.join(timeout=1)


def parse_line(line: str) -> dict | None:
    """stream-json 的一行轉成 dict；空行、壞行回 None。"""
    line = line.strip()
    if not line:
        return None
    try:
        msg = json.loads(line)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


def result_text(content: object) -> str:
    """tool_result 的 content（字串或區塊陣列）轉成字串。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
        )
    return json.dumps(content, ensure_ascii=False)


class TurnRecorder:
    """把 stream-json 的 assistant／user 訊息轉成 Anthropic content blocks（要落地的那份）。

    維持「存的就是日後打 API 要送的 messages」：
    - 工具名去掉 `mcp__running-coach__` 前綴（API 路徑的工具名就是短名）
    - tool_result 的 content 一律轉成字串
    - 不存 thinking／redacted_thinking（Claude Code 的 signature 不保證能在 API 重播）
    - 連續同角色合併：Claude Code 的 stream-json 每個 content block 可能各自一行
      assistant 事件（同一則 API 回覆被拆開），API 要求 user／assistant 交替。
      user 只在兩邊都是 tool_result 時合併，避免把工具結果併進學員的提問。

    `feed()` 同時回傳這一行新增的區塊（給呼叫端轉成 SSE 事件）。
    """

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.tool_names: dict[str, str] = {}

    @staticmethod
    def _is_tool_results(blocks: list[dict]) -> bool:
        return bool(blocks) and all(b.get("type") == "tool_result" for b in blocks)

    def _append(self, role: str, blocks: list[dict]) -> None:
        if not blocks:
            return
        last = self.messages[-1] if self.messages else None
        if last is not None and last["role"] == role and (
            role == "assistant"
            or (self._is_tool_results(last["content"]) and self._is_tool_results(blocks))
        ):
            last["content"].extend(blocks)
        else:
            self.messages.append({"role": role, "content": list(blocks)})

    def feed(self, msg: dict) -> list[dict]:
        kind = msg.get("type")
        if kind not in ("assistant", "user"):
            return []
        blocks: list[dict] = []
        for block in (msg.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if kind == "assistant" and btype == "text" and block.get("text"):
                blocks.append({"type": "text", "text": block["text"]})
            elif kind == "assistant" and btype == "tool_use":
                name = tool_short_name(block.get("name", ""))
                self.tool_names[block.get("id") or ""] = name
                blocks.append({
                    "type": "tool_use",
                    "id": block.get("id"),
                    "name": name,
                    "input": block.get("input") or {},
                })
            elif kind == "user" and btype == "tool_result":
                blocks.append({
                    "type": "tool_result",
                    "tool_use_id": block.get("tool_use_id"),
                    "content": result_text(block.get("content")),
                    "is_error": bool(block.get("is_error")),
                })
        self._append(kind, blocks)
        return blocks
