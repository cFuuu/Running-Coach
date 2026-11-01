"""總覽「分析近況」按鈕的執行層：在背景啟動 Claude Code 無互動 session（`claude -p`），
接本專案的 running-coach MCP server 查資料並寫出分析，串流回傳事件。

為什麼不走 Anthropic API：Fu 不必另設 `ANTHROPIC_API_KEY`，直接用本機已登入的
Claude Code（2026-10-14 Fu 決定）。指令組裝、子程序執行與執行鎖與聊天的 session
後端共用（services/claude_session.py，安全旗標的說明也在那裡）。
2026-10-14 實測：要求它呼叫 add_context_note，結果被 dontAsk 拒絕、資料庫未被寫入。

2026-10-27（N-36 決策 4）：報告不再是獨立的「分析紀錄」，而是存成對話裡的一回合
（`kind='analysis'`），學員可以在下面接著追問。`analysis_reports` 只記執行紀錄。
既有報告由 `migrate_reports_to_conversation()` 轉成一則「分析近況紀錄」對話。

本模組不 import fastapi，事件解析與指令組裝都是純函式，可用 unittest 直接測；
真正啟動子程序的部分以可注入的 `runner` 參數隔開，測試不需要真的跑 claude。
"""

from __future__ import annotations

import datetime
import os
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from src.main.python.mcp.server import coach
from src.main.python.services import claude_session, coach_chat_store
from src.main.python.services.claude_session import (
    ClaudeCodeSettings,
    Runner,
    TurnRecorder,
    result_text,
    subprocess_runner,
    tool_short_name,
)
from src.main.python.services.coach_chat import DEFAULT_MODEL, build_memory_brief

# 單一工具結果送回前端時的字元上限：只為了在面板上顯示「查到了什麼」，
# 完整內容 Claude 自己看得到，前端不需要整包 JSON。
TOOL_RESULT_PREVIEW_CHARS = 1200

# 按鈕送出的分析問題（唯一來源；前端不帶 prompt）。刻意寫明「先查哪些」，
# 讓模型呼叫工具取得含信賴度的事實，而不是憑印象作答。
# 2026-10-15 Fu 回饋：第一版產出約 3,000 字太複雜，改要「簡易分析」——格式與字數
# 上限寫在 _ANALYSIS_CONTEXT_NOTE，這裡只講要查什麼、要回答什麼。
ANALYSIS_PROMPT = (
    "請給我最近一週訓練與身體狀況的簡易分析：查今日狀態、近 7 天跑步、恢復狀態與身體數據、"
    "本週課表執行情形，然後用簡短格式告訴我目前狀態、需要注意的事、接下來幾天怎麼練。"
)
# 分析回合在對話裡的學員訊息開頭（面板據此與 kind 標示「分析近況」）
ANALYSIS_MARKER = "〔分析近況〕"
# 沒有對話可放時新開的對話標題（MM/DD 為本機日期）
ANALYSIS_CONVERSATION_TITLE = "分析近況 {month:02d}/{day:02d}"
# 既有報告轉成的那一則對話
LEGACY_CONVERSATION_TITLE = "分析近況紀錄"

# 接在 coach() 行為約束層之後，只描述這個介面的事實，不新增、不放寬任何約束
# （約束只有一份，在 mcp/server.py；與 coach_chat._PANEL_CONTEXT_NOTE 同一原則）。
_ANALYSIS_CONTEXT_NOTE = """\
## 這個介面
你是由學員本機 dashboard 的「分析近況」按鈕啟動的一次分析。報告會放進學員目前的
對話（標「分析近況」），學員可以在下面接著追問。

- 〔日期〕開頭的一行是介面自動加的今天日期；〔備忘〕開頭的段落是系統整理的追蹤事項
  與近期建議，近期建議是**當時**的判斷，數字要重新查。
- 這是按鈕啟動的，學員這次沒有說任何話：**不要記體感、不要建立或結案追蹤事項**
  （覺得該追蹤的事在報告裡提出，等學員在對話裡同意）。同步 Garmin、產課表、
  記情境筆記、寫外部課表一律不可用；需要這些動作時，在報告裡明說請學員改用 Claude Code。
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
    """分析端點的執行期設定（不 hardcode，從環境變數讀，見 .env.example）。

    claude 執行檔、MCP 設定檔、逾時與聊天的 session 後端共用（ClaudeCodeSettings）。
    """

    claude: ClaudeCodeSettings = field(default_factory=ClaudeCodeSettings)
    # 預設與聊天同一個模型（Sonnet 系列，`claude -p --model sonnet` 自動用最新版），
    # 不沿用 Claude Code 的全域設定（會隨學員在 Claude Code 切換模型而變，Dashboard 上看不出來）
    model: str = DEFAULT_MODEL
    allow_private_network: bool = False

    @classmethod
    def from_env(cls) -> "AnalysisConfig":
        return cls(
            claude=ClaudeCodeSettings.from_env(),
            model=os.environ.get("RC_ANALYSIS_MODEL", "").strip() or DEFAULT_MODEL,
            # 與聊天面板同一個開關：分析同樣會把個人健康資料送出本機、消耗額度
            allow_private_network=os.environ.get("RC_CHAT_ALLOW_PRIVATE_NETWORK", "")
            .strip().lower() in ("1", "true", "yes", "on"),
        )

    @property
    def disabled_reason(self) -> str | None:
        return self.claude.disabled_reason

    @property
    def enabled(self) -> bool:
        return self.disabled_reason is None


def build_system_prompt() -> str:
    """行為約束層原文 ＋ 分析介面補充（前段逐字等於 mcp.server.coach()）。"""
    return coach() + "\n" + _ANALYSIS_CONTEXT_NOTE


def build_command(config: AnalysisConfig) -> list[str]:
    """組 `claude -p` 指令（與聊天共用 claude_session.build_command，prompt 走 stdin）。

    只開唯讀工具：按鈕啟動、學員沒說話，「不要記體感、不要建立或結案追蹤事項」
    由工具層保證，不只靠上面的提示文字（2026-11-01 資安稽核 NV-4／NV-5）。
    """
    return claude_session.build_command(
        config.claude, system_prompt=build_system_prompt(), model=config.model, read_only=True
    )


def build_prompt(conn: sqlite3.Connection, athlete_id: int, today: datetime.date) -> str:
    """送進 `claude -p` 的 prompt：備忘（追蹤事項＋近期建議）＋〔日期〕＋固定問題。"""
    brief = build_memory_brief(conn, athlete_id, today)
    question = "\n".join([coach_chat_store.date_block(today)["text"], ANALYSIS_PROMPT])
    return "\n\n".join(part for part in (brief, question) if part)


def analysis_user_message(today: datetime.date, prompt: str = ANALYSIS_PROMPT) -> dict[str, Any]:
    """分析回合在對話裡的學員訊息：〔分析近況〕＋固定問題，另加〔日期〕區塊。"""
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": f"{ANALYSIS_MARKER}\n{prompt}"},
            coach_chat_store.date_block(today),
        ],
    }


def conversation_title(today: datetime.date) -> str:
    return ANALYSIS_CONVERSATION_TITLE.format(month=today.month, day=today.day)


def analysis_turn(
    recorder: TurnRecorder, report: str, today: datetime.date
) -> list[dict[str, Any]]:
    """要落地的分析回合：學員訊息＋stream 的內容（工具往返與報告）。

    stream 沒有以 assistant 文字收尾時（理論上不會），退回只存報告全文，
    確保落地的序列是 user／assistant 交替、沒有落單的 tool_use。
    """
    messages = recorder.messages
    if not messages or messages[-1]["role"] != "assistant":
        messages = [{"role": "assistant", "content": [{"type": "text", "text": report}]}]
    return [analysis_user_message(today), *messages]


# --- 事件 ---


@dataclass
class AnalysisEvent:
    event: str          # tool_use / tool_result / text / denied / done / error
    data: dict[str, Any] = field(default_factory=dict)


def _events_from(msg: dict[str, Any]) -> list[AnalysisEvent]:
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
                text = result_text(block.get("content"))
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


def parse_stream_line(line: str) -> list[AnalysisEvent]:
    """把 claude stream-json 的一行轉成本端點的事件（2026-10-14 對真實輸出驗證過格式）。

    - assistant 訊息的 tool_use → tool_use；text → text（thinking 不轉出）
    - user 訊息的 tool_result → tool_result（內容截成預覽）
    - system/permission_denied → denied（工具被拒，照實告訴前端）
    - result → done（最終報告全文、回合數、耗時）
    無法解析或不需要的行回傳空清單。
    """
    msg = claude_session.parse_line(line)
    return _events_from(msg) if msg is not None else []


# --- 執行 ---


def run_analysis(
    config: AnalysisConfig,
    prompt: str,
    runner: Runner = subprocess_runner,
    recorder: TurnRecorder | None = None,
) -> Iterator[AnalysisEvent]:
    """執行一次分析，依序產出事件；最後一定是 done 或 error 其中之一。

    recorder：給了就同時把 stream 轉成要落地的 content blocks（存進對話用）。
    """
    finished = False
    try:
        for line in runner(build_command(config), prompt, config.claude.timeout_sec):
            msg = claude_session.parse_line(line)
            if msg is None:
                continue
            if recorder is not None:
                recorder.feed(msg)
            for ev in _events_from(msg):
                if ev.event == "done":
                    finished = True
                yield ev
        if not finished:
            yield AnalysisEvent("error", {"message": "Claude Code 沒有回傳分析結果"})
    except (TimeoutError, RuntimeError, OSError, ValueError) as exc:
        # 報告已完整收到後才出錯（例如子程序收尾時的結束碼）不影響結果，不再發 error
        if not finished:
            yield AnalysisEvent("error", {"message": str(exc)})


# --- 既有報告轉成對話（N-36 決策 4）---


def _report_time_as_utc(created_at: str) -> str:
    """analysis_reports.created_at 是本機時間（無時區），轉成 coach_messages 用的 UTC ISO。"""
    moment = datetime.datetime.fromisoformat(created_at)
    if moment.tzinfo is None:
        moment = moment.astimezone()  # 視為本機時間
    return moment.astimezone(datetime.timezone.utc).isoformat()


def migrate_reports_to_conversation(conn: sqlite3.Connection) -> int:
    """把還沒轉過的既有報告轉成**一則**「分析近況紀錄」對話，回傳轉了幾份。

    - 只轉 status=done 且有報告文字的；依時間排，訊息時間用報告的 created_at。
    - 只留報告文字（工具往返當時沒存成 content blocks）：每份一回合＝
      學員訊息（〔分析近況〕＋當時的問題＋〔日期〕）＋教練的報告。學員訊息不可省：
      存下來的序列要能當 API 的 messages 重播，必須 user／assistant 交替。
    - 以 `analysis_reports.conversation_id` 標記已轉：可重跑不重複。標記與該回合的
      訊息在同一個交易寫入（append_turn 的 commit 一起提交，失敗一起 rollback）。
    - 每位學員一則；已經有同名對話就接在後面。

    app 啟動時執行（比照 mark_stale_running_as_error），沒有可轉的就什麼都不做。
    """
    rows = conn.execute(
        """
        SELECT id, athlete_id, created_at, prompt, report_text
        FROM analysis_reports
        WHERE conversation_id IS NULL AND status = 'done'
          AND report_text IS NOT NULL AND TRIM(report_text) != ''
        ORDER BY athlete_id, created_at, id
        """
    ).fetchall()
    conversation_by_athlete: dict[int, int] = {}
    for row in rows:
        athlete_id = row["athlete_id"]
        if athlete_id not in conversation_by_athlete:
            existing = conn.execute(
                "SELECT id FROM coach_conversations WHERE athlete_id = ? AND title = ? "
                "ORDER BY id LIMIT 1",
                (athlete_id, LEGACY_CONVERSATION_TITLE),
            ).fetchone()
            conversation_by_athlete[athlete_id] = (
                existing["id"]
                if existing
                else coach_chat_store.create_conversation(
                    conn, athlete_id, title=LEGACY_CONVERSATION_TITLE
                )["id"]
            )
        conversation_id = conversation_by_athlete[athlete_id]
        created_at = _report_time_as_utc(row["created_at"])
        day = coach_chat_store.local_date_of(created_at)
        conn.execute(
            "UPDATE analysis_reports SET conversation_id = ? WHERE id = ?",
            (conversation_id, row["id"]),
        )
        coach_chat_store.append_turn(
            conn,
            conversation_id,
            [
                analysis_user_message(day, row["prompt"] or ANALYSIS_PROMPT),
                {"role": "assistant", "content": [{"type": "text", "text": row["report_text"]}]},
            ],
            kind=coach_chat_store.KIND_ANALYSIS,
            created_at=created_at,
        )
    return len(rows)


