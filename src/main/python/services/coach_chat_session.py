"""AI 教練聊天的 session 後端：每次提問在背景跑一次 Claude Code（`claude -p`）。

N-36 2026-10-27 決策 1：聊天先走 Claude Code session（不用 API key、用 Claude Code
額度），以後再改串 Anthropic API（`coach_chat.stream_turn`，程式保留）。

為什麼不放在 coach_chat.py：這裡要用 claude_session 的子程序執行與 stream-json
轉換，coach_chat 則是兩種後端共用的系統提示與備忘；分開後 coach_chat 不必知道
子程序的事，也避免與 coach_analysis 互相 import。

一回合的流程：
1. 組 prompt＝備忘＋對話歷史文字＋本次提問（見 build_prompt）。`claude -p` 每次都是
   全新 session（`--no-session-persistence`），多輪對話靠後端把前文帶進 prompt。
2. 子程序的 stream-json 逐行轉成 Anthropic content blocks（claude_session.TurnRecorder）
   並即時轉成 SSE 事件，形狀與 API 路徑一致：
   `text{delta}`、`tool_use{id,name,input}`、`tool_result{tool_use_id,name,is_error,content}`、
   `done{message_id,stop_reason}`、`error{message}`。
   另加一種**非終止**事件 `denied{tool}`：Claude Code 以 dontAsk 拒絕了某個工具
   （例如模型想呼叫白名單以外的寫入工具）。不轉成 error，因為回合仍會正常完成
   （被拒的工具另有一則 is_error 的 tool_result），只是讓前端能標示「這個動作被擋下」。
   與「分析近況」的 `denied{tool}` 同一形狀。
3. 成功才一次落地（`coach_chat_store.append_turn`，一回合一交易）；失敗不落地。
   落地的學員訊息是學員原話＋〔日期〕區塊，不含備忘與歷史文字。

執行鎖（claude_session.SESSION_LOCK）由路由層取得與釋放，本模組不碰。
"""

from __future__ import annotations

import datetime
import sqlite3
from collections.abc import Iterator
from typing import Any

from src.main.python.services import claude_session, coach_chat, coach_chat_store
from src.main.python.services.claude_session import Runner, subprocess_runner
from src.main.python.services.coach_chat import ChatConfig, ChatEvent

HISTORY_HEADING = (
    "## 先前的對話（由舊到新。教練查過的工具只列名稱，當時查到的數字不在這裡，需要時重新查）"
)
CURRENT_HEADING = "## 學員這次的訊息"
STUDENT = "學員"
COACH = "教練"


def _rounds(rows: list[dict[str, Any]]) -> list[str]:
    """把落地的訊息依回合轉成文字：一回合＝一則學員訊息到下一則之間的全部內容。

    每則都標日期（訊息的 created_at 轉本機日期）；教練那則附「查了哪些工具」，
    不帶工具回傳——數字要重查。學員訊息裡的〔日期〕區塊略過（已經標在行首）。
    """
    rounds: list[dict[str, Any]] = []
    for row in rows:
        content = row["content"]
        day = coach_chat_store.local_date_of(row["created_at"])
        if row["role"] == "user" and not coach_chat_store.is_tool_result_content(content):
            rounds.append({"day": day, "student": coach_chat_store.text_of(content),
                           "coach_day": None, "coach": [], "tools": []})
        elif row["role"] == "assistant" and rounds:
            current = rounds[-1]
            current["coach_day"] = day
            text = coach_chat_store.text_of(content)
            if text:
                current["coach"].append(text)
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name") or ""
                    if name and name not in current["tools"]:
                        current["tools"].append(name)

    rendered = []
    for r in rounds:
        lines = [f"〔{coach_chat_store.date_label(r['day'])}〕{STUDENT}：{r['student']}"]
        if r["coach"] or r["tools"]:
            tools = f"（查了：{'、'.join(r['tools'])}）" if r["tools"] else ""
            day = r["coach_day"] or r["day"]
            lines.append(
                f"〔{coach_chat_store.date_label(day)}〕{COACH}{tools}：" + "\n".join(r["coach"])
            )
        rendered.append("\n".join(lines))
    return rendered


def build_history_text(rows: list[dict[str, Any]], max_chars: int) -> str:
    """對話歷史文字；超過 max_chars 時從最舊的**整回合**丟，並註明省略了幾回合。"""
    rounds = _rounds(rows)
    dropped = 0
    while rounds and max_chars > 0 and sum(len(r) + 2 for r in rounds) > max_chars:
        rounds.pop(0)
        dropped += 1
    if not rounds and not dropped:
        return ""
    parts = [HISTORY_HEADING]
    if dropped:
        parts.append(f"（更早的 {dropped} 回合太長，已省略）")
    parts.extend(rounds)
    return "\n\n".join(parts)


def build_prompt(*, brief: str, history_text: str, text: str, today: datetime.date) -> str:
    """送進 `claude -p`（stdin）的 prompt：備忘＋歷史＋本次提問（帶〔日期〕）。"""
    current = "\n".join([
        CURRENT_HEADING,
        coach_chat_store.date_block(today)["text"],
        text,
    ])
    return "\n\n".join(part for part in (brief, history_text, current) if part)


def run_turn(
    *,
    conn: sqlite3.Connection,
    config: ChatConfig,
    athlete_id: int,
    conversation_id: int,
    text: str,
    today: datetime.date,
    runner: Runner = subprocess_runner,
) -> Iterator[ChatEvent]:
    """跑一回合 session 聊天，逐步產生 SSE 事件；最後一定是 done 或 error。"""
    rows = coach_chat_store.get_message_rows(conn, conversation_id)
    prompt = build_prompt(
        brief=coach_chat.build_memory_brief(conn, athlete_id, today),
        history_text=build_history_text(rows, config.history_max_chars),
        text=text,
        today=today,
    )

    recorder = claude_session.TurnRecorder()
    result: dict[str, Any] | None = None
    try:
        cmd = claude_session.build_command(
            config.claude, system_prompt=coach_chat.build_system_prompt(), model=config.model
        )
        for line in runner(cmd, prompt, config.claude.timeout_sec):
            msg = claude_session.parse_line(line)
            if msg is None:
                continue
            kind = msg.get("type")
            if kind in ("assistant", "user"):
                yield from _events_for(recorder.feed(msg), recorder, config)
            elif kind == "system" and msg.get("subtype") == "permission_denied":
                yield ChatEvent(
                    "denied", {"tool": claude_session.tool_short_name(msg.get("tool_name", ""))}
                )
            elif kind == "result":
                result = msg
    except (TimeoutError, RuntimeError, OSError, ValueError) as exc:
        # 結果已完整收到後才出錯（例如子程序收尾時的結束碼）不影響這回合
        if result is None:
            yield ChatEvent("error", {"message": str(exc)})
            return

    if result is None:
        yield ChatEvent("error", {"message": "Claude Code 沒有回傳結果，本回合未儲存，可重試。"})
        return
    if result.get("is_error"):
        # 只帶 subtype：result 的文字可能夾帶 API 錯誤原文，不原樣外送（CLAUDE.md §🔐 3）
        yield ChatEvent("error", {
            "message": f"Claude Code 回報錯誤（{result.get('subtype') or '未知'}），本回合未儲存，可重試。"
        })
        return
    if not recorder.messages or recorder.messages[-1]["role"] != "assistant":
        yield ChatEvent("error", {"message": "教練沒有給出回覆，本回合未儲存，可重試。"})
        return

    turn = [coach_chat_store.user_message(text, today), *recorder.messages]
    try:
        message_ids = coach_chat_store.append_turn(conn, conversation_id, turn)
    except Exception as exc:  # noqa: BLE001 - 串流已開始，錯誤只能走 error 事件回報
        yield ChatEvent("error", {"message": f"儲存對話失敗（{type(exc).__name__}）"})
        return
    yield ChatEvent("done", {
        "message_id": message_ids[-1] if message_ids else None,
        "stop_reason": result.get("stop_reason") or "end_turn",
    })


def _events_for(
    blocks: list[dict[str, Any]], recorder: claude_session.TurnRecorder, config: ChatConfig
) -> Iterator[ChatEvent]:
    """新增的 content blocks 轉成與 API 路徑同形狀的 SSE 事件。"""
    for block in blocks:
        btype = block["type"]
        if btype == "text":
            yield ChatEvent("text", {"delta": block["text"]})
        elif btype == "tool_use":
            yield ChatEvent(
                "tool_use", {"id": block["id"], "name": block["name"], "input": block["input"]}
            )
        elif btype == "tool_result":
            yield ChatEvent("tool_result", {
                "tool_use_id": block["tool_use_id"],
                "name": recorder.tool_names.get(block["tool_use_id"] or "", ""),
                "is_error": block["is_error"],
                "content": coach_chat.truncate_tool_result(
                    block["content"], config.tool_result_max_chars
                ),
            })
