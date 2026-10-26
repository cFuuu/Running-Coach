"""AI 教練聊天紀錄（coach_conversations／coach_messages）的存取層。

承 DASHBOARD_TASKS.md D.3、D.0 決策 3（2026-09-08 Fu 拍板）。

⚠️ **硬邊界：本模組的輸出永不進事實層、規則引擎不讀。**
存的是原始對話，不是 TODO.md N-8 的「跨對話結構化記憶」——它只是其日後的
原料。readiness.py／training_load.py／session_quality.py **皆不得** import 本模組。

訊息以 Anthropic content blocks 的 JSON 陣列**原樣**存放：存下來的 messages
序列**就是**下一回合要送回 API 的 messages。刻意不拆成結構化欄位，因為任何
改寫都會讓重播失真（signature 被動過的 thinking 區塊會被 API 拒絕）。

⚠️ **一回合一交易**（append_turn）：一次提問所產生的全部訊息——user 提問、
assistant 的 tool_use、user 的 tool_result、assistant 最終回覆——必須在迴圈
成功結束後一次寫入。**絕不落地沒有對應 tool_result 的 tool_use**，否則下一
回合送 API 會 400。

分層比照 context_notes_store.py／training_plan_store.py：吃 sqlite3.Connection，
不 import fastapi。
"""

from __future__ import annotations

import datetime
import json
import re
import sqlite3
from typing import Any

# 標題取學員第一則訊息首句（。！？!? 或換行為界）的前 N 字（N-36 決策 5）。
# 純顯示用，不影響任何判讀。
TITLE_MAX_CHARS = 16
_FIRST_SENTENCE = re.compile(r"[^。！？!?\n]*[。！？!?]?")

# 每則學員訊息另加的日期區塊（N-36 決策 6）：獨立一個 text 區塊、只記日期不記時間，
# 讓教練知道每則訊息是哪天說的。兩種後端（session／API）都加；前端據此分隔日期。
DATE_BLOCK_PREFIX = "〔日期〕"
_WEEKDAYS = "一二三四五六日"

# 訊息種類：一般聊天，或「分析近況」的一回合（N-36 決策 4）
KIND_CHAT = "chat"
KIND_ANALYSIS = "analysis"

# 近期建議（N-35 ③）：分析近況的結論，看時間不看狀態。
RECENT_ADVICE_DAYS = 30
RECENT_ADVICE_MAX_ITEMS = 5
RECENT_ADVICE_MAX_CHARS = 3000
# 分析報告固定格式的「**依據**：…」那一行是當時的數值，帶進備忘只會讓教練沿用舊數字
_BASIS_LINE = re.compile(r"^\s*(?:[-*•]\s*)?\*{0,2}依據\*{0,2}\s*[:：]")


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def local_date_of(created_at: str) -> datetime.date:
    """訊息的 created_at（UTC ISO，含時區）轉成本機日期；沒有時區的視為本機時間。"""
    moment = datetime.datetime.fromisoformat(created_at)
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    return moment.date()


def date_label(day: datetime.date) -> str:
    """`2026-10-27（週二）`"""
    return f"{day.isoformat()}（週{_WEEKDAYS[day.weekday()]}）"


def date_block(day: datetime.date) -> dict[str, Any]:
    return {"type": "text", "text": f"{DATE_BLOCK_PREFIX}{date_label(day)}"}


def is_date_block(block: Any) -> bool:
    return (
        isinstance(block, dict)
        and block.get("type") == "text"
        and str(block.get("text", "")).startswith(DATE_BLOCK_PREFIX)
    )


def user_message(text: str, today: datetime.date) -> dict[str, Any]:
    """學員訊息：學員的字一個區塊，日期另一個區塊（放後面，content[0] 仍是學員的字）。"""
    return {"role": "user", "content": [{"type": "text", "text": text}, date_block(today)]}


def title_from_text(text: str) -> str:
    """首句（。！？!? 或換行為界，含結尾標點）的前 TITLE_MAX_CHARS 字。

    以換行為界順帶排除面板在末尾自動附加的〔檢視脈絡〕那一行（DASHBOARD_TASKS.md
    D.4）——那行是介面加的、不是學員打的。
    """
    match = _FIRST_SENTENCE.match(text.strip())
    return (match.group(0) if match else "").strip()[:TITLE_MAX_CHARS]


def _title_from(content: list[dict[str, Any]]) -> str:
    """從一則 user 訊息取出可當標題的字：第一個非日期 text 區塊的首句。

    只取 text 區塊——標題要的是學員問了什麼，不是 tool_use／tool_result 往返的內容。
    """
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text" and not is_date_block(block):
            return title_from_text(block.get("text", ""))
    return ""


def create_conversation(
    conn: sqlite3.Connection, athlete_id: int, title: str | None = None
) -> dict[str, Any]:
    """開一則新對話（標題預設為 NULL，等學員第一次送出時才補）。"""
    now = _now_iso()
    cursor = conn.execute(
        """
        INSERT INTO coach_conversations (athlete_id, title, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (athlete_id, title, now, now),
    )
    conn.commit()
    return {
        "id": cursor.lastrowid,
        "title": title,
        "created_at": now,
        "updated_at": now,
    }


def set_title_if_empty(conn: sqlite3.Connection, conversation_id: int, title: str) -> None:
    """標題仍為 NULL／空字串時補上（N-36 決策 5：學員第一次送出、送模型之前就建立）。"""
    if not title:
        return
    conn.execute(
        "UPDATE coach_conversations SET title = ? "
        "WHERE id = ? AND (title IS NULL OR title = '')",
        (title, conversation_id),
    )
    conn.commit()


def conversation_athlete_id(conn: sqlite3.Connection, conversation_id: int) -> int | None:
    row = conn.execute(
        "SELECT athlete_id FROM coach_conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    return row["athlete_id"] if row else None


def latest_conversation_id(conn: sqlite3.Connection, athlete_id: int) -> int | None:
    """最近更新的對話（對話清單的第一則）；沒有任何對話時回 None。"""
    row = conn.execute(
        "SELECT id FROM coach_conversations WHERE athlete_id = ? "
        "ORDER BY updated_at DESC, id DESC LIMIT 1",
        (athlete_id,),
    ).fetchone()
    return row["id"] if row else None


def list_conversations(conn: sqlite3.Connection, athlete_id: int) -> list[dict[str, Any]]:
    """列出該學員的對話，updated_at 由新到舊（同時間以 id 新到舊）。"""
    rows = conn.execute(
        """
        SELECT id, title, created_at, updated_at
        FROM coach_conversations
        WHERE athlete_id = ?
        ORDER BY updated_at DESC, id DESC
        """,
        (athlete_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_conversation(
    conn: sqlite3.Connection, athlete_id: int, conversation_id: int
) -> dict[str, Any] | None:
    """取回一則對話（含全部訊息，seq 升冪）。查無此對話時回傳 None。

    帶 athlete_id 條件是為了避免跨學員誤讀，比照 context_notes_store.delete_note。
    """
    row = conn.execute(
        """
        SELECT id, title, created_at, updated_at
        FROM coach_conversations
        WHERE id = ? AND athlete_id = ?
        """,
        (conversation_id, athlete_id),
    ).fetchone()
    if row is None:
        return None

    conversation = dict(row)
    conversation["messages"] = [
        {
            "id": message["id"],
            "seq": message["seq"],
            "role": message["role"],
            "content": json.loads(message["content_json"]),
            "kind": message["kind"],
            "created_at": message["created_at"],
        }
        for message in conn.execute(
            """
            SELECT id, seq, role, content_json, kind, created_at
            FROM coach_messages
            WHERE conversation_id = ?
            ORDER BY seq
            """,
            (conversation_id,),
        )
    ]
    return conversation


def conversation_exists(
    conn: sqlite3.Connection, athlete_id: int, conversation_id: int
) -> bool:
    """這則對話存不存在、屬不屬於這位學員。

    給只需要判斷存在性的呼叫端用（例如送出提問前的 404 檢查）——
    用 get_conversation() 會把整串訊息讀出來丟掉，長對話就是白讀一次。
    """
    return (
        conn.execute(
            "SELECT 1 FROM coach_conversations WHERE id = ? AND athlete_id = ?",
            (conversation_id, athlete_id),
        ).fetchone()
        is not None
    )


def get_messages(conn: sqlite3.Connection, conversation_id: int) -> list[dict[str, Any]]:
    """取回一則對話要送回 Anthropic API 的 messages（`{"role", "content"}`，seq 升冪）。

    刻意不帶 id／seq／created_at：這份結構會直接進 API 的 `messages` 參數，
    多餘欄位會被拒。要顯示用的完整資料請用 get_conversation()。
    """
    return [
        {"role": row["role"], "content": json.loads(row["content_json"])}
        for row in conn.execute(
            "SELECT role, content_json FROM coach_messages "
            "WHERE conversation_id = ? ORDER BY seq",
            (conversation_id,),
        )
    ]


def get_message_rows(conn: sqlite3.Connection, conversation_id: int) -> list[dict[str, Any]]:
    """同 get_messages，另帶 kind 與 created_at——給 session 路徑把歷史轉成文字用
    （每則要標日期）。不可直接送 API（多餘欄位會被拒）。
    """
    return [
        {
            "role": row["role"],
            "content": json.loads(row["content_json"]),
            "kind": row["kind"],
            "created_at": row["created_at"],
        }
        for row in conn.execute(
            "SELECT role, content_json, kind, created_at FROM coach_messages "
            "WHERE conversation_id = ? ORDER BY seq",
            (conversation_id,),
        )
    ]


def is_tool_result_content(content: Any) -> bool:
    return (
        isinstance(content, list)
        and len(content) > 0
        and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    )


def text_of(content: Any, *, skip_date: bool = True) -> str:
    """一則訊息所有 text 區塊串起來（預設略過〔日期〕區塊）。"""
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(b.get("text", ""))
        for b in content
        if isinstance(b, dict)
        and b.get("type") == "text"
        and b.get("text")
        and not (skip_date and is_date_block(b))
    ).strip()


def strip_basis_line(text: str) -> str:
    """去掉分析報告的「**依據**：…」那一行（當時的數值，不該被當成現在的事實）。"""
    return "\n".join(line for line in text.splitlines() if not _BASIS_LINE.match(line)).strip()


def list_recent_advice(
    conn: sqlite3.Connection, athlete_id: int, today: datetime.date
) -> list[dict[str, Any]]:
    """近期建議：RECENT_ADVICE_DAYS 天內、最多 RECENT_ADVICE_MAX_ITEMS 份「分析近況」
    回合的結論（去掉依據行），合計不超過 RECENT_ADVICE_MAX_CHARS 字，新到舊。

    每份＝一個分析回合**最後一則 assistant 訊息**的文字（前面的是查工具途中的話）。
    來源是對話紀錄而不是 analysis_reports：學員刪掉對話，就不再拿它當建議。
    回傳 `[{"date": "YYYY-MM-DD", "conversation_id": int, "text": str}]`。
    """
    since = today - datetime.timedelta(days=RECENT_ADVICE_DAYS)
    rows = conn.execute(
        """
        SELECT m.conversation_id, m.role, m.content_json, m.created_at
        FROM coach_messages m
        JOIN coach_conversations c ON c.id = m.conversation_id
        WHERE c.athlete_id = ? AND m.kind = ?
        ORDER BY m.conversation_id, m.seq
        """,
        (athlete_id, KIND_ANALYSIS),
    ).fetchall()

    turns: list[dict[str, Any]] = []
    for row in rows:
        content = json.loads(row["content_json"])
        if row["role"] == "user" and not is_tool_result_content(content):
            turns.append({
                "date": local_date_of(row["created_at"]),
                "conversation_id": row["conversation_id"],
                "text": "",
            })
        elif row["role"] == "assistant" and turns and turns[-1]["conversation_id"] == row["conversation_id"]:
            text = text_of(content)
            if text:
                turns[-1]["text"] = text

    recent = [t for t in turns if since <= t["date"] <= today and t["text"]]
    recent.sort(key=lambda t: t["date"], reverse=True)

    advice: list[dict[str, Any]] = []
    total = 0
    for turn in recent[:RECENT_ADVICE_MAX_ITEMS]:
        text = strip_basis_line(turn["text"])
        if not text:
            continue
        if total + len(text) > RECENT_ADVICE_MAX_CHARS:
            if advice:
                break
            text = text[:RECENT_ADVICE_MAX_CHARS]
        total += len(text)
        advice.append({
            "date": turn["date"].isoformat(),
            "conversation_id": turn["conversation_id"],
            "text": text,
        })
    return advice


def append_turn(
    conn: sqlite3.Connection,
    conversation_id: int,
    messages: list[dict[str, Any]],
    *,
    kind: str = KIND_CHAT,
    created_at: str | None = None,
) -> list[int]:
    """把一回合產生的全部訊息**一次交易**寫入，回傳新列 id（順序同 messages）。

    messages 每項為 `{"role": "user"|"assistant", "content": [content blocks]}`。
    kind：整回合同一種（`chat`／`analysis`）。created_at：預設現在；舊報告轉成對話時
    帶報告當時的時間（對話的 updated_at 也用這個時間）。

    ⚠️ 這是「一回合一交易」的唯一入口：呼叫端必須等對話迴圈成功走完才呼叫，
    中途失敗就完全不呼叫。任何一列寫入失敗即整批 rollback——**絕不落地沒有
    對應 tool_result 的 tool_use**（那會讓下一回合送 API 直接 400）。

    順帶做兩件事，同屬這一個交易：
    - 標題仍為 NULL 時，取本批首則 user 訊息首句的前 TITLE_MAX_CHARS 字補上。
    - 更新 updated_at（對話清單依此排序）。
    """
    if not messages:
        return []
    if kind not in (KIND_CHAT, KIND_ANALYSIS):
        raise ValueError(f"kind 只能是 chat 或 analysis，收到：{kind}")

    now = created_at or _now_iso()
    row = conn.execute(
        "SELECT COALESCE(MAX(seq) + 1, 0) AS next_seq FROM coach_messages WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    next_seq = row["next_seq"]

    try:
        new_ids: list[int] = []
        for offset, message in enumerate(messages):
            role = message["role"]
            if role not in ("user", "assistant"):
                raise ValueError(f"role 只能是 user 或 assistant，收到：{role}")
            cursor = conn.execute(
                """
                INSERT INTO coach_messages
                    (conversation_id, seq, role, content_json, kind, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    next_seq + offset,
                    role,
                    json.dumps(message["content"], ensure_ascii=False),
                    kind,
                    now,
                ),
            )
            new_ids.append(cursor.lastrowid)

        _backfill_title(conn, conversation_id, messages)
        conn.execute(
            "UPDATE coach_conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return new_ids


def _backfill_title(
    conn: sqlite3.Connection, conversation_id: int, messages: list[dict[str, Any]]
) -> None:
    """標題仍為 NULL 時，用本批首則 user 訊息首句的前 TITLE_MAX_CHARS 字補上。

    正常流程在學員送出時就已由 set_title_if_empty() 建好標題，這裡是後備
    （例如直接呼叫 append_turn 的測試或轉換程式）。
    """
    row = conn.execute(
        "SELECT title FROM coach_conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    if row is None or row["title"]:
        return
    for message in messages:
        if message["role"] != "user":
            continue
        text = _title_from(message["content"])
        if text:
            conn.execute(
                "UPDATE coach_conversations SET title = ? WHERE id = ?",
                (text[:TITLE_MAX_CHARS], conversation_id),
            )
            return


def delete_conversation(
    conn: sqlite3.Connection, athlete_id: int, conversation_id: int
) -> bool:
    """刪除一則對話連同它的全部訊息，回傳是否真的刪到。

    ⚠️ **明確刪 coach_messages，不可依賴 ON DELETE CASCADE**：`PRAGMA
    foreign_keys = ON` 只在 models.db.get_connection() 設定，API 層的連線
    （routes_dashboard.make_connection_dependency）用的是裸 sqlite3.connect，
    外鍵約束在那裡是關閉的，靠 CASCADE 會留下孤兒訊息列。
    """
    owned = conn.execute(
        "SELECT 1 FROM coach_conversations WHERE id = ? AND athlete_id = ?",
        (conversation_id, athlete_id),
    ).fetchone()
    if owned is None:
        return False

    try:
        conn.execute("DELETE FROM coach_messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute(
            "DELETE FROM coach_conversations WHERE id = ? AND athlete_id = ?",
            (conversation_id, athlete_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return True
