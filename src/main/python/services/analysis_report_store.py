"""analysis_reports 的讀寫（總覽「AI 分析」報告）。

只存原始報告與工具呼叫紀錄，不進事實層、規則引擎不讀——見 schema.sql
analysis_reports 的說明。本模組不 import fastapi，可用 unittest 直接測。
"""

from __future__ import annotations

import datetime
import json
import sqlite3
from typing import Any

# 清單只回傳開頭幾個字當預覽，完整報告用單筆查詢取
PREVIEW_CHARS = 80


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def create_report(conn: sqlite3.Connection, athlete_id: int, prompt: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO analysis_reports (athlete_id, created_at, status, prompt)
        VALUES (?, ?, 'running', ?)
        """,
        (athlete_id, _now(), prompt),
    )
    conn.commit()
    return cur.lastrowid


def finish_report(
    conn: sqlite3.Connection,
    report_id: int,
    *,
    report_text: str | None,
    tools: list[dict[str, Any]],
    error: str | None = None,
    num_turns: int | None = None,
    duration_ms: int | None = None,
) -> None:
    """收尾：有 error 就標 error（報告文字若已有部分仍一併保存，方便除錯）。"""
    conn.execute(
        """
        UPDATE analysis_reports
        SET status = ?, finished_at = ?, report_text = ?, tools_json = ?,
            error = ?, num_turns = ?, duration_ms = ?
        WHERE id = ?
        """,
        (
            "error" if error else "done",
            _now(),
            report_text,
            json.dumps(tools, ensure_ascii=False),
            error,
            num_turns,
            duration_ms,
            report_id,
        ),
    )
    conn.commit()


def _row_to_dict(row: sqlite3.Row, full: bool) -> dict[str, Any]:
    text = row["report_text"] or ""
    out = {
        "id": row["id"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
        "status": row["status"],
        "preview": text[:PREVIEW_CHARS],
    }
    if full:
        out.update(
            {
                "prompt": row["prompt"],
                "report_text": row["report_text"],
                "tools": json.loads(row["tools_json"]) if row["tools_json"] else [],
                "error": row["error"],
                "num_turns": row["num_turns"],
                "duration_ms": row["duration_ms"],
            }
        )
    return out


def list_reports(conn: sqlite3.Connection, athlete_id: int, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM analysis_reports WHERE athlete_id = ?
        ORDER BY created_at DESC, id DESC LIMIT ?
        """,
        (athlete_id, limit),
    ).fetchall()
    return [_row_to_dict(r, full=False) for r in rows]


def get_report(conn: sqlite3.Connection, athlete_id: int, report_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM analysis_reports WHERE id = ? AND athlete_id = ?",
        (report_id, athlete_id),
    ).fetchone()
    return _row_to_dict(row, full=True) if row else None


def mark_stale_running_as_error(conn: sqlite3.Connection) -> int:
    """server 重啟時把上次沒收尾的 running 標成 error（子程序已隨舊進程消失）。"""
    cur = conn.execute(
        """
        UPDATE analysis_reports SET status = 'error', finished_at = ?,
               error = '分析進行中 server 已重啟，未完成'
        WHERE status = 'running'
        """,
        (_now(),),
    )
    conn.commit()
    return cur.rowcount
