"""學員每日體感（daily_feel）的存取層——承 TODO N-3 HRV 規則並存決策（2026-10-22）。

用途：逐日記下「今天累不累」，日後拿來比對 readiness 各規則觸發的日子是否
真的累。對照報告已證明事後回想整段補填分不出規則優劣，故需要即時紀錄。

⚠️ **硬邊界：本模組的輸出永不進事實層。** readiness.py／training_load.py／
session_quality.py **皆不得** import 本模組——體感是驗證規則的對照組，
若反過來餵進規則，就再也無法拿它驗證規則（與 context_notes_store 同一條邊界）。

分層比照 context_notes_store.py：吃 sqlite3.Connection，不 import fastapi。
"""

from __future__ import annotations

import datetime
import sqlite3
from typing import Any

# 由累到好排列；與 schema.sql 的 CHECK 約束一致。
FEEL_LEVELS = ("very_tired", "tired", "normal", "good")
SOURCES = ("dashboard", "coach", "backfill")


def _to_iso(value: datetime.date | str) -> str:
    if isinstance(value, datetime.date):
        return value.isoformat()
    # 先驗格式，避免 "2026-9-1" 這類字串寫進去後與其他表的日期對不上。
    return datetime.date.fromisoformat(value).isoformat()


def set_feel(
    conn: sqlite3.Connection,
    athlete_id: int,
    date: datetime.date | str,
    feel: str,
    source: str,
    note: str | None = None,
) -> dict[str, Any]:
    """寫入（或覆寫）某天的體感，回傳寫入後的那一列。

    一天只留一筆：早上點錯、晚上改主意都直接覆寫，不累積多筆讓分析時還要挑。
    note 是選填的原話補充（例如「昨晚只睡 5 小時」），空白視為無。
    """
    if feel not in FEEL_LEVELS:
        raise ValueError(f"feel 必須是 {'/'.join(FEEL_LEVELS)} 之一，收到：{feel}")
    if source not in SOURCES:
        raise ValueError(f"source 必須是 {'/'.join(SOURCES)} 之一，收到：{source}")
    date_iso = _to_iso(date)
    note = note.strip() if note and note.strip() else None

    conn.execute(
        """
        INSERT INTO daily_feel (athlete_id, date, feel, note, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (athlete_id, date) DO UPDATE SET
            feel = excluded.feel,
            note = excluded.note,
            source = excluded.source,
            updated_at = excluded.updated_at
        """,
        (
            athlete_id,
            date_iso,
            feel,
            note,
            source,
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    return get_feels_in_range(conn, athlete_id, date_iso, date_iso)[0]


def get_feels_in_range(
    conn: sqlite3.Connection,
    athlete_id: int,
    start_date: datetime.date | str,
    end_date: datetime.date | str,
) -> list[dict[str, Any]]:
    """取出 [start_date, end_date] 內的體感紀錄，依日期升冪。沒記的日子不出現。"""
    rows = conn.execute(
        """
        SELECT date, feel, note, source, updated_at
        FROM daily_feel
        WHERE athlete_id = ? AND date >= ? AND date <= ?
        ORDER BY date
        """,
        (athlete_id, _to_iso(start_date), _to_iso(end_date)),
    ).fetchall()
    return [dict(row) for row in rows]


def delete_feel(conn: sqlite3.Connection, athlete_id: int, date: datetime.date | str) -> bool:
    """清除某天的體感（點錯想取消），回傳是否真的刪到。"""
    cursor = conn.execute(
        "DELETE FROM daily_feel WHERE athlete_id = ? AND date = ?",
        (athlete_id, _to_iso(date)),
    )
    conn.commit()
    return cursor.rowcount > 0
