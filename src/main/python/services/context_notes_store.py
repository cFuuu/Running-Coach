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
追蹤事項（N-35）的 tracking_status 是生命週期而非分類，同見該註解；
其存取與狀態轉換也在本模組，「該不該追問」的判斷在 coach_tracking.py。

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


_NOTE_COLUMNS = (
    "id, athlete_id, start_date, end_date, note, created_at, tracking_status, "
    "last_followup_date, last_mentioned_date, stale_asked_date, closed_date"
)


def get_notes_in_range(
    conn: sqlite3.Connection,
    athlete_id: int,
    start_date: datetime.date | str,
    end_date: datetime.date | str,
    today: datetime.date | str | None = None,
) -> list[dict[str, Any]]:
    """取出與 [start_date, end_date] 有任何重疊的軟性輸入，依 start_date 升冪。

    重疊而非包含：一筆 09-01~09-10 的出差紀錄，在查詢 09-05~09-06 時**必須**
    出現——那幾天正在出差當中。一般筆記以 COALESCE(end_date, start_date) 當作
    涵蓋末日，單日紀錄（end_date 為 NULL）因此自然退化為當天。

    追蹤事項（N-35）的涵蓋末日不同：待同意／追蹤中的事項視為持續到 today
    （省略時取本機今天）——否則今天記「膝蓋痛」不填結束日，明天就從教練視野
    消失；已結案的以 closed_date 為末日（若比 end_date 晚）。
    """
    start_iso = _to_iso(start_date)
    end_iso = _to_iso(end_date)
    today_iso = _to_iso(today if today is not None else datetime.date.today())
    rows = conn.execute(
        f"""
        SELECT {_NOTE_COLUMNS}
        FROM context_notes
        WHERE athlete_id = ?
          AND start_date <= ?
          AND (
            CASE
              WHEN tracking_status IN ('proposed', 'tracking')
                THEN MAX(COALESCE(end_date, start_date), ?)
              WHEN tracking_status IN ('resolved', 'dismissed')
                THEN MAX(COALESCE(end_date, start_date), COALESCE(closed_date, start_date))
              ELSE COALESCE(end_date, start_date)
            END
          ) >= ?
        ORDER BY start_date, id
        """,
        (athlete_id, end_iso, today_iso, start_iso),
    ).fetchall()
    return [dict(row) for row in rows]


# --- 追蹤事項（N-35）：context_notes 上 tracking_status ≠ 'none' 的列 ---
#
# 狀態轉換集中在這裡（單一真實來源），MCP 工具與 REST 端點都呼叫本區函式。
# 「這次該不該追問」的判斷是唯讀計算，在 services/coach_tracking.py。

OPEN_TRACKING_STATUSES = ("proposed", "tracking")
CLOSED_TRACKING_STATUSES = ("resolved", "dismissed")

# action → (允許的起始狀態, 轉換後狀態（None＝不變）, 要設為今天的日期欄)
_TRACKING_TRANSITIONS: dict[str, tuple[tuple[str, ...], str | None, tuple[str, ...]]] = {
    # 同意追蹤：當天算一次追問，也當作學員提起（30 天沒提起的計算由此起算）
    "accept": (("proposed",), "tracking", ("last_followup_date", "last_mentioned_date")),
    "decline": (("proposed",), "dismissed", ("closed_date",)),
    "followed_up": (("tracking",), None, ("last_followup_date",)),
    "mentioned": (("tracking",), None, ("last_mentioned_date",)),
    "stale_asked": (("tracking",), None, ("stale_asked_date",)),
    "resolve": (("tracking",), "resolved", ("closed_date",)),
    "stop": (("tracking",), "dismissed", ("closed_date",)),
}
TRACKING_ACTIONS = tuple(_TRACKING_TRANSITIONS)

_STATUS_LABELS = {
    "proposed": "待學員同意",
    "tracking": "追蹤中",
    "resolved": "已痊癒",
    "dismissed": "不再追蹤",
}


class TrackingItemNotFound(LookupError):
    """找不到該 id 的追蹤事項（不存在、屬於別的學員、或是一般情境筆記）。"""


class TrackingActionError(ValueError):
    """動作名稱不合法，或該事項目前的狀態不能做這個動作。"""


def status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status)


def add_tracking_item(
    conn: sqlite3.Connection,
    athlete_id: int,
    note: str,
    start_date: datetime.date | str,
) -> int:
    """建立一筆「待學員同意」的追蹤事項，回傳新列 id。

    note 記學員原話（理由同 add_note）。end_date 一律留空——追蹤事項的
    結束由學員說好了或不用再追蹤決定（closed_date），不預設結束日。
    """
    if not note or not note.strip():
        raise ValueError("note 不可為空——要追蹤什麼得記下學員的原話")
    start_iso = _to_iso(start_date)
    try:
        start_iso = datetime.date.fromisoformat(start_iso).isoformat()
    except ValueError:
        raise ValueError(f"start_date 必須是 YYYY-MM-DD，收到：{start_date}") from None
    cursor = conn.execute(
        """
        INSERT INTO context_notes (athlete_id, start_date, end_date, note, created_at, tracking_status)
        VALUES (?, ?, NULL, ?, ?, 'proposed')
        """,
        (
            athlete_id,
            start_iso,
            note.strip(),
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    return cursor.lastrowid


def get_tracking_item(
    conn: sqlite3.Connection, athlete_id: int, item_id: int
) -> dict[str, Any]:
    """取一筆追蹤事項；不存在、別的學員、或一般情境筆記都拋 TrackingItemNotFound。"""
    row = conn.execute(
        f"SELECT {_NOTE_COLUMNS} FROM context_notes WHERE id = ? AND athlete_id = ?",
        (item_id, athlete_id),
    ).fetchone()
    if row is None:
        raise TrackingItemNotFound(f"找不到 id={item_id} 的追蹤事項")
    if row["tracking_status"] == "none":
        raise TrackingItemNotFound(
            f"id={item_id} 是一般情境筆記，不是追蹤事項，不能用追蹤事項的動作修改或結案"
        )
    return dict(row)


def list_tracking_items(
    conn: sqlite3.Connection,
    athlete_id: int,
    statuses: tuple[str, ...] = OPEN_TRACKING_STATUSES,
) -> list[dict[str, Any]]:
    """列出指定狀態的追蹤事項（預設：進行中＝待同意＋追蹤中），依起始日、id 升冪。"""
    placeholders = ", ".join("?" for _ in statuses)
    rows = conn.execute(
        f"""
        SELECT {_NOTE_COLUMNS}
        FROM context_notes
        WHERE athlete_id = ? AND tracking_status IN ({placeholders})
        ORDER BY start_date, id
        """,
        (athlete_id, *statuses),
    ).fetchall()
    return [dict(row) for row in rows]


def apply_tracking_action(
    conn: sqlite3.Connection,
    athlete_id: int,
    item_id: int,
    action: str,
    today: datetime.date | str,
) -> dict[str, Any]:
    """對追蹤事項做一次狀態轉換或記錄，回傳更新後的那一列。

    只動 tracking_status ≠ 'none' 的列：一般情境筆記拋 TrackingItemNotFound，
    教練不得藉此結案或修改學員的一般筆記。不合法的動作或轉換拋 TrackingActionError，
    訊息說明目前狀態與可用的動作。
    """
    if action not in _TRACKING_TRANSITIONS:
        raise TrackingActionError(
            f"不認得的動作「{action}」，可用：{'／'.join(TRACKING_ACTIONS)}"
        )
    item = get_tracking_item(conn, athlete_id, item_id)
    allowed_from, new_status, date_columns = _TRACKING_TRANSITIONS[action]
    current = item["tracking_status"]
    if current not in allowed_from:
        usable = [a for a, (src, _, _) in _TRACKING_TRANSITIONS.items() if current in src]
        hint = f"目前可用：{'／'.join(usable)}" if usable else "此事項已結案，不能再變更"
        raise TrackingActionError(
            f"id={item_id} 目前是「{status_label(current)}」，不能做 {action}；{hint}"
        )

    today_iso = _to_iso(today)
    assignments = [f"{column} = ?" for column in date_columns]
    params: list[Any] = [today_iso] * len(date_columns)
    if new_status is not None:
        assignments.append("tracking_status = ?")
        params.append(new_status)
    conn.execute(
        f"UPDATE context_notes SET {', '.join(assignments)} WHERE id = ? AND athlete_id = ?",
        (*params, item_id, athlete_id),
    )
    conn.commit()
    return get_tracking_item(conn, athlete_id, item_id)


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
