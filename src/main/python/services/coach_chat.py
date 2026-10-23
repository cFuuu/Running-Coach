"""AI 教練聊天的對話迴圈——dashboard 右側面板的後端（DASHBOARD_TASKS.md D.3）。

這一層做四件事，其餘一概不做：
1. 組系統提示：`mcp.server.coach()` **原文** ＋ 面板脈絡補充（`_PANEL_CONTEXT_NOTE`）。
2. 把 MCP 的**唯讀** tools 轉成 Anthropic 的 tool schema 餵給模型。
3. 跑 tool_use → tool_result → 續問的迴圈，工具在同一個 process 內直接呼叫 MCP。
4. 控制送進模型的體積（單一工具結果截尾、舊工具結果省略、歷史裁切）。

⚠️ **行為約束層一字不改。** D.0 決策 1 推翻的只有「不自己呼叫 Anthropic API」，
「不自建 App」的精神仍在：`coach()` 與 MCP 工具定義直接重用，Claude Code／
Desktop 的 MCP 路徑照舊保留。`_PANEL_CONTEXT_NOTE` 接在 `coach()` 之後，比照
PLAN.md §7.6 的風格層擴充點——它只補「這個介面有什麼／沒有什麼」，不碰任何約束。

⚠️ **聊天只開放唯讀工具**（`mcp.server.READ_ONLY_TOOL_NAMES`）。
`sync_garmin_data`／`generate_training_plan`／`add_context_note`／
`save_external_workouts` 不進聊天——「呈現不等於決策、頁面無寫入路徑」的硬約束
延伸到這裡。模型若仍點名寫入工具，本模組**不執行**，回一則 is_error 說明。

⚠️ **憑證**：`ANTHROPIC_API_KEY` 由 `anthropic` SDK 自己從 `os.environ` 讀，
本模組**不持有、不傳遞、不記錄**它（ChatConfig 只留一個布林值表示有沒有設）。
錯誤訊息只帶例外類別與 HTTP 狀態碼，不 dump request/response 原文（CLAUDE.md §🔐 3）。
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, NamedTuple, Protocol

from mcp.server.context import ServerRequestContext
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from src.main.python.mcp.server import READ_ONLY_TOOL_NAMES, AppContext, coach, mcp
from src.main.python.services import coach_chat_store

# --- 設定（全部走 os.environ，沿用 garmin_auth.py 讀 GARMINTOKENS 的做法）---
#
# 專案沒有 dotenv 載入器，由使用者的 shell 設定；預設值見 DASHBOARD_TASKS.md D.3
# 的設定表與 .env.example。

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 8192
DEFAULT_HISTORY_MAX_CHARS = 120_000
DEFAULT_TOOL_RESULT_MAX_CHARS = 60_000

# 一回合內最多跑幾輪 tool_use。**這是安全上限、不是可調參數**：模型若卡在
# 「查完再查」的迴圈，沒有這道閘就會無限往 API 送。真實對話遠用不到 16 輪；
# 撞到它代表出事了，該回合以 error 結束、不落地。
MAX_TOOL_ROUNDS = 16

# 送模型時保留完整內容的 tool_result 回合數（其餘換成短佔位，見 §體積控制）。
KEEP_RECENT_TOOL_RESULT_ROUNDS = 2

TRUNCATED_SUFFIX = "\n…（已截斷）"
ELIDED_TOOL_RESULT = "（前次查詢結果已省略）"
EMPTY_TOOL_RESULT = "null"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"環境變數 {name} 必須是整數，收到：{raw!r}") from exc


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ChatConfig:
    """聊天端點的執行期設定。

    ⚠️ 刻意**不含** API key：`AsyncAnthropic()` 會自己從 `ANTHROPIC_API_KEY`
    讀，金鑰因此完全不進本專案的資料結構，也就不可能被某個 repr／log 帶出去。
    這裡只留 `api_key_present` 決定聊天是否啟用。
    """

    api_key_present: bool
    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    history_max_chars: int = DEFAULT_HISTORY_MAX_CHARS
    tool_result_max_chars: int = DEFAULT_TOOL_RESULT_MAX_CHARS
    allow_private_network: bool = False

    @classmethod
    def from_env(cls) -> "ChatConfig":
        return cls(
            api_key_present=bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
            model=os.environ.get("RC_COACH_MODEL", "").strip() or DEFAULT_MODEL,
            max_tokens=_env_int("RC_COACH_MAX_TOKENS", DEFAULT_MAX_TOKENS),
            history_max_chars=_env_int(
                "RC_COACH_HISTORY_MAX_CHARS", DEFAULT_HISTORY_MAX_CHARS
            ),
            tool_result_max_chars=_env_int(
                "RC_COACH_TOOL_RESULT_MAX_CHARS", DEFAULT_TOOL_RESULT_MAX_CHARS
            ),
            allow_private_network=_env_flag("RC_CHAT_ALLOW_PRIVATE_NETWORK"),
        )

    @property
    def enabled(self) -> bool:
        return self.api_key_present

    @property
    def disabled_reason(self) -> str | None:
        return None if self.api_key_present else "未設定 ANTHROPIC_API_KEY"


# --- 系統提示 ---

# 接在 coach() 的 _OUTPUT_SHAPE 之後，比照 PLAN.md §7.6 的風格層擴充點。
# **只描述這個介面的事實**（脈絡行從哪來、這裡有哪些工具），不新增、不放寬、
# 也不重述任何行為約束——約束只有一份，在 mcp/server.py。
_PANEL_CONTEXT_NOTE = """\
## 這個介面
你現在是透過學員的本機 dashboard 面板對話（不是 Claude Code）。

- 學員訊息末尾若出現〔檢視脈絡〕開頭的一行，那是 **dashboard 介面自動附加的**，
  不是學員打的字。它只說明學員正在看哪一頁（view／session_id／range 等），
  **本身不是數據**——需要數字仍要自己呼叫工具查。
- **本面板沒有寫入工具。** 這裡只有唯讀查詢，`add_context_note`、
  `generate_training_plan`、`save_external_workouts`、`sync_garmin_data`
  一律不存在。學員要你同步 Garmin、產課表、或把「這週出差」記下來時，
  **明說這個面板做不到，請改用 Claude Code**（那裡才有這些工具），
  不要假裝記下了、也不要改用別的工具湊合。
- 學員說今天累不累時同樣記不了（`record_daily_feel` 不在本面板），請他點
  首頁「本週訓練狀況」下方的「自評體感狀況」按鈕。
"""


def build_system_prompt() -> str:
    """行為約束層原文 ＋ 面板脈絡補充。

    ⚠️ 前段必須**逐字**等於 `mcp.server.coach()`——test_coach_chat.py 對此有斷言。
    """
    return coach() + "\n" + _PANEL_CONTEXT_NOTE


def build_system_blocks(system_text: str | None = None) -> list[dict[str, Any]]:
    """系統提示包成帶 cache_control 的單一 text 區塊（每回合都一樣，值得快取）。"""
    return [
        {
            "type": "text",
            "text": system_text if system_text is not None else build_system_prompt(),
            "cache_control": {"type": "ephemeral"},
        }
    ]


# --- 工具 ---


async def build_tool_schemas(mcp_server: Any = mcp) -> list[dict[str, Any]]:
    """把 MCP 的唯讀 tools 轉成 Anthropic 的 tool schema。

    MCP SDK 產生的 `input_schema` 已排除 `ctx` 參數、輸出即純 JSON Schema，
    可直接餵 Anthropic，不需要在這裡重寫任何欄位（重寫就會有兩份真相）。
    最後一個工具帶 cache_control，讓整份工具表落在同一個快取前綴。
    """
    schemas = [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.input_schema,
        }
        for tool in await mcp_server.list_tools()
        if tool.name in READ_ONLY_TOOL_NAMES
    ]
    if schemas:
        schemas[-1] = {**schemas[-1], "cache_control": {"type": "ephemeral"}}
    return schemas


def make_tool_context(conn: sqlite3.Connection, mcp_server: Any = mcp) -> Context:
    """組一個 in-process 的 MCP Context，讓 tools 直接在本 process 內執行。

    ⚠️ `conn` 必須是 `check_same_thread=False` 開的：MCP SDK 用
    `anyio.to_thread.run_sync` 在別的 worker thread 跑同步 tool，
    否則會 `sqlite3.ProgrammingError`（已實測重現）。

    `session=None` 是刻意的：沒有 MCP 連線，也沒有任何 server→client 請求
    （本專案的 tools 都不用 ctx.log／report_progress／elicit）。
    """
    return Context(
        mcp_server=mcp_server,
        request_context=ServerRequestContext(
            session=None,
            lifespan_context=AppContext(db=conn),
            protocol_version="in-process",
            method="tools/call",
        ),
    )


def _merge_text_blocks(content: Any) -> str:
    """把 CallToolResult.content 的所有文字區塊併成一段字。

    MCP 的回傳形狀依 tool 的回傳型別而異，**不一致**：
    `-> dict` 得一個 TextContent、`-> list[dict]` 每項一個區塊、
    空值（空 list／None）得 `content=[]`。一律合併、空的送 "null"，
    聊天層因此不需要知道任何一個 tool 的內部結構。
    """
    texts = [
        block.text
        for block in (content or [])
        if getattr(block, "type", None) == "text" and getattr(block, "text", None)
    ]
    return "\n".join(texts) if texts else EMPTY_TOOL_RESULT


async def execute_tool(
    mcp_server: Any, context: Context, name: str, arguments: dict[str, Any]
) -> tuple[str, bool]:
    """在 in-process MCP 上執行一個工具，回傳 `(文字結果, is_error)`。

    - 名稱不在唯讀清單 → **不執行**，回 is_error 並說明去哪裡才有那個工具。
    - `ToolError`（含 SDK 對未知工具與參數驗證失敗所拋的）→ is_error＋訊息。
      `UnexpectedToolError` 是它的子類，訊息只有 "Error executing tool <name>"，
      原始例外不外洩，符合 CLAUDE.md §🔐 3。
    """
    if name not in READ_ONLY_TOOL_NAMES:
        return (
            f"工具 `{name}` 在 AI 教練面板不可用：本面板只開放唯讀查詢。"
            "同步資料、產生課表、記錄軟性輸入等寫入操作請學員改用 Claude Code。",
            True,
        )

    try:
        result = await mcp_server.call_tool(name, arguments, context=context)
    except ToolError as exc:
        return (str(exc), True)

    content = getattr(result, "content", None)
    if content is None:
        return (f"工具 `{name}` 回傳了非預期的結果型別，未取得內容。", True)
    return (_merge_text_blocks(content), bool(getattr(result, "is_error", False)))


# --- 體積控制（只影響「送給模型的東西」，落地一律原樣不裁）---
#
# 真實 DB 上 get_session_detail 單次最多約 13 萬字元、中位數約 4 萬，
# get_wellness_trend 90d 約 4.7 萬。不做這層，第二輪就會撞上 context 上限。
#
# 刻意**不**對特定工具的結構（如 records.points）降採樣：那會讓聊天層認得
# 工具的內部格式，變成第二份真相。實用上若教練因截斷失準再議。


def _chars(message: dict[str, Any]) -> int:
    return len(json.dumps(message, ensure_ascii=False))


def _is_tool_result_message(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return (
        message.get("role") == "user"
        and isinstance(content, list)
        and len(content) > 0
        and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    )


def _is_plain_text_user_message(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return (
        message.get("role") == "user"
        and isinstance(content, list)
        and len(content) > 0
        and all(isinstance(b, dict) and b.get("type") == "text" for b in content)
    )


def truncate_tool_result(text: str, max_chars: int) -> str:
    """單一工具結果超過上限就截尾並標明（規則 ①）。"""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + TRUNCATED_SUFFIX


def prepare_messages_for_model(
    messages: list[dict[str, Any]], config: ChatConfig
) -> list[dict[str, Any]]:
    """把落地用的完整 messages 轉成這一輪要送 API 的版本。

    ① 單一 tool_result 超過 `tool_result_max_chars` → 截尾＋標明。
    ② 最近 KEEP_RECENT_TOOL_RESULT_ROUNDS 個 tool_result 回合以外的內容 →
       換成短佔位。**tool_use／tool_result 的成對結構保留、只換內容**——
       少了任何一邊，API 會直接 400。
    ③ 整體字元數裁到 `history_max_chars`，**只能在純文字 user 回合處切**
       （在別處切會把 tool_use 跟它的 tool_result 拆散）。

    原輸入不被修改（走 JSON round-trip 深拷貝），因為它同時是要落地的資料。
    """
    prepared: list[dict[str, Any]] = json.loads(json.dumps(messages, ensure_ascii=False))

    tool_result_indices = [i for i, m in enumerate(prepared) if _is_tool_result_message(m)]
    keep_full = set(tool_result_indices[-KEEP_RECENT_TOOL_RESULT_ROUNDS:])
    for index in tool_result_indices:
        for block in prepared[index]["content"]:
            if block.get("type") != "tool_result":
                continue
            if index in keep_full:
                block["content"] = truncate_tool_result(
                    _as_text(block.get("content")), config.tool_result_max_chars
                )
            else:
                block["content"] = ELIDED_TOOL_RESULT

    return _trim_history(prepared, config.history_max_chars)


def _as_text(value: Any) -> str:
    """tool_result 的 content 允許是字串或區塊陣列；本模組一律寫成字串，
    但保險起見把非字串轉成 JSON 再量測，避免 len() 對 list 量錯東西。
    """
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _trim_history(
    messages: list[dict[str, Any]], max_chars: int
) -> list[dict[str, Any]]:
    if max_chars <= 0 or sum(_chars(m) for m in messages) <= max_chars:
        return messages

    # 切點＝純文字 user 回合的起點；0 不算切點（切在那裡等於沒切）。
    cut_points = [
        i for i, m in enumerate(messages) if i > 0 and _is_plain_text_user_message(m)
    ]
    for start in cut_points:
        candidate = messages[start:]
        if sum(_chars(m) for m in candidate) <= max_chars:
            return candidate
    # 連「只留最後一個純文字 user 回合起」都還超過上限：那已是能切到的最小
    # 歷史，再切就會拆散 tool_use／tool_result。留著讓 API 自己抱怨，
    # 好過在這裡送出一份結構壞掉的 messages。
    return messages[cut_points[-1] :] if cut_points else messages


# --- LLM 後端 ---


class LLMBackend(Protocol):
    """對 LLM 的極小介面——只有「串流一次請求」這一件事。

    產生的事件（dict）：
    - `{"type": "text", "delta": str}`
    - `{"type": "tool_use", "id": str, "name": str, "input": dict}`
    - `{"type": "final", "content": list[dict], "stop_reason": str | None}`（最後一則）

    `content` 是 Anthropic content blocks 的 JSON 形式，**原樣**（含 thinking
    區塊與其 signature），對話迴圈直接拿去落地與重播，不做任何改寫。
    """

    def astream(
        self,
        *,
        system: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]: ...


class AnthropicBackend:
    """真正呼叫 Anthropic Messages API 的後端。

    ⚠️ 金鑰由 `AsyncAnthropic()` 自己從 `ANTHROPIC_API_KEY` 讀，本類別不接受、
    也不保存 api_key 參數（CLAUDE.md §🔐 1／3）。

    ⚠️ 串流事件的形狀取自已安裝 SDK 的原始碼（`anthropic/lib/streaming/_messages.py`
    的 `build_events`）：迭代 stream 會同時拿到原始事件與 SDK 合成的
    `text`／`content_block_stop` 等事件。此處只用後者兩種，最終內容一律取
    `get_final_message()` 的快照，不自行拼接 partial JSON。
    """

    def __init__(self, model: str, max_tokens: int, client: Any | None = None) -> None:
        """client 只給測試注入（帶 mock transport 的 AsyncAnthropic），
        正式路徑一律留空，由 SDK 自己從 ANTHROPIC_API_KEY 取金鑰。
        """
        if client is None:
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic()
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    async def astream(
        self,
        *,
        system: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        ) as stream:
            async for event in stream:
                if event.type == "text":
                    yield {"type": "text", "delta": event.text}
                elif event.type == "content_block_stop":
                    block = event.content_block
                    if block.type == "tool_use":
                        yield {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input or {},
                        }
            final = await stream.get_final_message()

        yield {
            "type": "final",
            "content": [
                block.model_dump(mode="json", exclude_none=True) for block in final.content
            ],
            "stop_reason": final.stop_reason,
        }


# --- 對話迴圈 ---


class ChatEvent(NamedTuple):
    """一則要送給前端的 SSE 事件（框架格式由 routes_chat 負責）。"""

    event: str
    data: dict[str, Any]


def _safe_error_message(exc: BaseException) -> str:
    """把例外轉成可以送到前端的字串。

    ⚠️ Anthropic SDK 的 API 例外訊息會帶 response 原文，依 CLAUDE.md §🔐 3
    不可外流，故只留類別名與 HTTP 狀態碼。專案自己拋的例外訊息本來就只有
    運動與設定相關內容，照實給，才有除錯價值。
    """
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        return f"呼叫 Anthropic API 失敗（{type(exc).__name__}，HTTP {status_code}）"
    if type(exc).__module__.split(".")[0] == "anthropic":
        return f"呼叫 Anthropic API 失敗（{type(exc).__name__}）"
    return f"{type(exc).__name__}: {exc}"


async def stream_turn(
    *,
    conn: sqlite3.Connection,
    config: ChatConfig,
    backend: LLMBackend,
    conversation_id: int,
    text: str,
    mcp_server: Any = mcp,
) -> AsyncIterator[ChatEvent]:
    """跑完一回合對話，逐步產生要串流給前端的事件。

    ⚠️ **一回合一交易**：整段成功走完才呼叫 `coach_chat_store.append_turn()`
    一次落地；發出 `error` 事件或中途斷線就完全不落地——**絕不留下沒有對應
    tool_result 的 tool_use**（下一回合送 API 會 400）。因此重試＝重送，
    不需要任何補償邏輯。
    """
    history = coach_chat_store.get_messages(conn, conversation_id)
    turn: list[dict[str, Any]] = [{"role": "user", "content": [{"type": "text", "text": text}]}]

    system = build_system_blocks()
    tools = await build_tool_schemas(mcp_server)
    tool_context = make_tool_context(conn, mcp_server)

    try:
        for round_index in range(MAX_TOOL_ROUNDS + 1):
            api_messages = prepare_messages_for_model(history + turn, config)

            final: dict[str, Any] | None = None
            async for event in backend.astream(
                system=system, tools=tools, messages=api_messages
            ):
                if event["type"] == "text":
                    yield ChatEvent("text", {"delta": event["delta"]})
                elif event["type"] == "tool_use":
                    yield ChatEvent(
                        "tool_use",
                        {"id": event["id"], "name": event["name"], "input": event["input"]},
                    )
                elif event["type"] == "final":
                    final = event

            if final is None:
                yield ChatEvent("error", {"message": "模型串流結束但沒有回傳完整訊息。"})
                return

            turn.append({"role": "assistant", "content": final["content"]})
            stop_reason = final["stop_reason"]

            if stop_reason == "max_tokens":
                yield ChatEvent(
                    "error",
                    {
                        "message": (
                            "回覆被長度上限截斷（stop_reason=max_tokens）。"
                            "本回合未儲存，可重試；若經常發生，調高 RC_COACH_MAX_TOKENS。"
                        )
                    },
                )
                return

            tool_uses = [b for b in final["content"] if b.get("type") == "tool_use"]
            if stop_reason != "tool_use" or not tool_uses:
                break

            if round_index >= MAX_TOOL_ROUNDS:
                yield ChatEvent(
                    "error",
                    {
                        "message": (
                            f"工具呼叫超過 {MAX_TOOL_ROUNDS} 輪仍未收斂，已中止。本回合未儲存。"
                        )
                    },
                )
                return

            results: list[dict[str, Any]] = []
            for block in tool_uses:
                name = block.get("name", "")
                arguments = block.get("input") or {}
                result_text, is_error = await execute_tool(
                    mcp_server, tool_context, name, arguments
                )
                # 落地存原文；送模型與送前端的都是截尾後的版本
                # （前端要顯示的是「模型實際看到的東西」）。
                yield ChatEvent(
                    "tool_result",
                    {
                        "tool_use_id": block.get("id"),
                        "name": name,
                        "is_error": is_error,
                        "content": truncate_tool_result(
                            result_text, config.tool_result_max_chars
                        ),
                    },
                )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.get("id"),
                        "content": result_text,
                        "is_error": is_error,
                    }
                )
            # 所有 tool_result 放在**同一則** user 訊息（API 要求成對且同批）。
            turn.append({"role": "user", "content": results})

        message_ids = coach_chat_store.append_turn(conn, conversation_id, turn)
    except Exception as exc:  # noqa: BLE001 - 串流已開始，錯誤只能走 error 事件回報
        conn.rollback()
        yield ChatEvent("error", {"message": _safe_error_message(exc)})
        return

    yield ChatEvent(
        "done",
        {"message_id": message_ids[-1] if message_ids else None, "stop_reason": stop_reason},
    )
