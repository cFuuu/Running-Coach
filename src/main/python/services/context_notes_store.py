"""學員軟性輸入（context_notes）的存取層——承 PLAN.md §7.5（2026-08-25 grill 定案）。

軟性輸入指「規則算不出、只有學員本人知道」的情境：出差、睡眠不足、工作壓力、
心情、「腳有點緊」等。它們解釋了訓練偏差的**原因**，而偏差本身（漏了三課）
規則層看得到、原因看不到。

⚠️ **硬邊界：本模組的輸出永不進事實層，僅作判斷層（AI 教練）的補充輸入。**
軟性輸入無法驗證——學員自述睡不好，系統無從查證。若讓它參與規則計算
（例如「有出差紀錄就自動降低 readiness 門檻」），等於在事實層開一個沒有
信賴度可標的洞，直接違反 §7.3「無信賴度標記不得輸出」的硬規則。
故 readiness.py／training_load.py／session_quality.py **皆不得** import 本模組。

⚠️ 刻意**不設**類型與嚴重度等結構化欄位，理由見 schema.sql 的表註解。

分層比照 training_plan_store.py：吃 sqlite3.Connection，不 import fastapi。
"""

from __future__ import annotations

import datetime
import sqlite3
from typing import Any


def _to_iso(value: datetime.date | str) -> str:
    return value.isoformat() if isinstance(value, datetime.date) else value


def add_note(
    conn: sqlite3.Connection,
    athlete_id: int,
    note: str,
    start_date: datetime.date | str,
    end_date: datetime.date | str | None = None,
) -> int:
    """新增一筆軟性輸入，回傳新列 id。

    note：學員的原話，自由文字。**不要代為摘要或分類**——這份文字的價值
        正在於它未經處理，三個月後回看時才知道當時實際發生什麼。
    start_date：影響起始日（必要）。
    end_date：影響結束日，省略代表單日，或影響仍在持續、結束日未知。

    ⚠️ 不做去重：同一天可以有多筆（「睡不好」與「腳有點緊」是兩件事）。
    """
    if not note or not note.strip():
        raise ValueError("note 不可為空——沒有內容的紀錄對日後回看毫無價值")

    start_iso = _to_iso(start_date)
    end_iso = _to_iso(end_date) if end_date is not None else None
    if end_iso is not None and end_iso < start_iso:
        raise ValueError(f"end_date（{end_iso}）不可早於 start_date（{start_iso}）")

    cursor = conn.execute(
        """
        INSERT INTO context_notes (athlete_id, start_date, end_date, note, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            athlete_id,
            start_iso,
            end_iso,
            note.strip(),
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    return cursor.lastrowid


def get_notes_in_range(
    conn: sqlite3.Connection,
    athlete_id: int,
    start_date: datetime.date | str,
    end_date: datetime.date | str,
) -> list[dict[str, Any]]:
    """取出與 [start_date, end_date] 有任何重疊的軟性輸入，依 start_date 升冪。

    重疊而非包含：一筆 09-01~09-10 的出差紀錄，在查詢 09-05~09-06 時**必須**
    出現——那幾天正在出差當中。以 COALESCE(end_date, start_date) 當作該筆的
    涵蓋末日，單日紀錄（end_date 為 NULL）因此自然退化為當天。
    """
    start_iso = _to_iso(start_date)
    end_iso = _to_iso(end_date)
    rows = conn.execute(
        """
        SELECT id, athlete_id, start_date, end_date, note, created_at
        FROM context_notes
        WHERE athlete_id = ?
          AND start_date <= ?
          AND COALESCE(end_date, start_date) >= ?
        ORDER BY start_date, id
        """,
        (athlete_id, end_iso, start_iso),
    ).fetchall()
    return [dict(row) for row in rows]


def delete_note(conn: sqlite3.Connection, athlete_id: int, note_id: int) -> bool:
    """刪除一筆軟性輸入（記錯了、或學員要求移除），回傳是否真的刪到。

    帶 athlete_id 條件是為了避免跨學員誤刪——多人情境下 note_id 由呼叫端
    傳入，不可只憑 id 就動手。
    """
    cursor = conn.execute(
        "DELETE FROM context_notes WHERE id = ? AND athlete_id = ?",
        (note_id, athlete_id),
    )
    conn.commit()
    return cursor.rowcount > 0
