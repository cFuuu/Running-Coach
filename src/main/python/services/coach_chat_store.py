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

另有一種**唯讀**對話：source='claude_code'，由 coach_transcript_import.py 從
Claude Code session 紀錄匯入（N-33）。它只留教練回合的文字與工具名稱／參數，
**不是**可重送 API 的 messages，呼叫端不得對它 append_turn。
"""

from __future__ import annotations

import datetime
import json
import sqlite3
from typing import Any

# 標題取首則 user 訊息第一行的前 N 字（D.3）。純顯示用，不影響任何判讀。
TITLE_MAX_CHARS = 40


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _title_from(content: list[dict[str, Any]]) -> str:
    """從一則 user 訊息取出可當標題的字：**第一行**，去頭尾空白。

    只取 text 區塊——標題要的是學員問了什麼，不是 tool_use／tool_result 往返
    的內容。只取第一行，是因為面板會在訊息末尾自動附一行〔檢視脈絡〕
    （DASHBOARD_TASKS.md D.4），那行是介面加的、不是學員打的，放進標題只會
    讓對話清單難讀。這裡刻意不去認〔檢視脈絡〕這個字串——認了就等於後端
    知道前端的訊息格式，多一份得同步維護的真相。
    """
    text = "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()
    return text.splitlines()[0].strip() if text else ""


def create_conversation(conn: sqlite3.Connection, athlete_id: int) -> dict[str, Any]:
    """開一則新對話（標題為 NULL，等第一則 user 訊息進來時才補）。"""
    now = _now_iso()
    cursor = conn.execute(
        """
        INSERT INTO coach_conversations (athlete_id, title, created_at, updated_at)
        VALUES (?, NULL, ?, ?)
        """,
        (athlete_id, now, now),
    )
    conn.commit()
    return {
        "id": cursor.lastrowid,
        "title": None,
        "created_at": now,
        "updated_at": now,
        "source": "dashboard",
    }


def list_conversations(conn: sqlite3.Connection, athlete_id: int) -> list[dict[str, Any]]:
    """列出該學員的對話，updated_at 由新到舊（同時間以 id 新到舊）。"""
    rows = conn.execute(
        """
        SELECT id, title, created_at, updated_at, source
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
        SELECT id, title, created_at, updated_at, source
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
            "created_at": message["created_at"],
        }
        for message in conn.execute(
            """
            SELECT id, seq, role, content_json, created_at
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


def is_read_only(conn: sqlite3.Connection, conversation_id: int) -> bool:
    """匯入的對話（source 不是 dashboard）不可續聊。"""
    row = conn.execute(
        "SELECT source FROM coach_conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    return row is not None and row["source"] != "dashboard"


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


def append_turn(
    conn: sqlite3.Connection, conversation_id: int, messages: list[dict[str, Any]]
) -> list[int]:
    """把一回合產生的全部訊息**一次交易**寫入，回傳新列 id（順序同 messages）。

    messages 每項為 `{"role": "user"|"assistant", "content": [content blocks]}`。

    ⚠️ 這是「一回合一交易」的唯一入口：呼叫端必須等對話迴圈成功走完才呼叫，
    中途失敗就完全不呼叫。任何一列寫入失敗即整批 rollback——**絕不落地沒有
    對應 tool_result 的 tool_use**（那會讓下一回合送 API 直接 400）。

    順帶做兩件事，同屬這一個交易：
    - 標題仍為 NULL 時，取本批首則 user 訊息第一行的前 TITLE_MAX_CHARS 字補上。
    - 更新 updated_at（對話清單依此排序）。
    """
    if not messages:
        return []

    now = _now_iso()
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
                    (conversation_id, seq, role, content_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    next_seq + offset,
                    role,
                    json.dumps(message["content"], ensure_ascii=False),
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
    """標題仍為 NULL 時，用本批首則 user 訊息第一行的前 TITLE_MAX_CHARS 字補上。"""
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

    匯入的對話另外記進 coach_import_dismissed，下次匯入才不會把它加回來。
    """
    owned = conn.execute(
        "SELECT source_session_id FROM coach_conversations WHERE id = ? AND athlete_id = ?",
        (conversation_id, athlete_id),
    ).fetchone()
    if owned is None:
        return False

    try:
        if owned["source_session_id"]:
            conn.execute(
                "INSERT OR IGNORE INTO coach_import_dismissed (source_session_id, dismissed_at) "
                "VALUES (?, ?)",
                (owned["source_session_id"], _now_iso()),
            )
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


def is_import_dismissed(conn: sqlite3.Connection, source_session_id: str) -> bool:
    """學員是否已在面板刪掉這個 session 匯入的對話。"""
    return (
        conn.execute(
            "SELECT 1 FROM coach_import_dismissed WHERE source_session_id = ?",
            (source_session_id,),
        ).fetchone()
        is not None
    )


def save_imported_conversation(
    conn: sqlite3.Connection,
    athlete_id: int,
    source_session_id: str,
    messages: list[dict[str, Any]],
) -> str:
    """寫入（或更新）一則從 Claude Code session 匯入的唯讀對話，回傳
    'created'／'updated'／'unchanged'。

    messages 每項為 `{"role", "content", "created_at"}`，是該 session 目前**全部**
    的教練回合。session 紀錄只會往後長，所以更新時整批換掉訊息即可，不必比對
    哪幾則是新的；內容沒變就不動，避免 updated_at 每次匯入都被刷新。
    標題取首則 user 訊息第一行，時間取首末訊息的時間。整批同一交易。
    """
    if not messages:
        return "unchanged"

    row = conn.execute(
        "SELECT id FROM coach_conversations WHERE source_session_id = ?",
        (source_session_id,),
    ).fetchone()
    if row is not None:
        existing = [
            {"role": r["role"], "content": json.loads(r["content_json"]), "created_at": r["created_at"]}
            for r in conn.execute(
                "SELECT role, content_json, created_at FROM coach_messages "
                "WHERE conversation_id = ? ORDER BY seq",
                (row["id"],),
            )
        ]
        if existing == messages:
            return "unchanged"

    title = next(
        (t for t in (_title_from(m["content"]) for m in messages if m["role"] == "user") if t),
        "",
    )[:TITLE_MAX_CHARS] or None
    created_at, updated_at = messages[0]["created_at"], messages[-1]["created_at"]

    try:
        if row is None:
            conversation_id = conn.execute(
                """
                INSERT INTO coach_conversations
                    (athlete_id, title, created_at, updated_at, source, source_session_id)
                VALUES (?, ?, ?, ?, 'claude_code', ?)
                """,
                (athlete_id, title, created_at, updated_at, source_session_id),
            ).lastrowid
        else:
            conversation_id = row["id"]
            conn.execute(
                "UPDATE coach_conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, updated_at, conversation_id),
            )
            conn.execute("DELETE FROM coach_messages WHERE conversation_id = ?", (conversation_id,))
        conn.executemany(
            """
            INSERT INTO coach_messages (conversation_id, seq, role, content_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    conversation_id,
                    seq,
                    m["role"],
                    json.dumps(m["content"], ensure_ascii=False),
                    m["created_at"],
                )
                for seq, m in enumerate(messages)
            ],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return "created" if row is None else "updated"
