"""把 periodization_scheduler.generate_schedule() 的輸出寫入 training_plan，
並維護 PLAN.md §5.8 定案的版本歷史設計（Issue #19）。

本模組是資料庫存取層：吃 sqlite3.Connection，不 import fastapi，方便單元測試
用臨時 SQLite 驗證（與 dashboard_queries.py 的分層方式一致）。與
periodization_scheduler.py／vdot_engine.py 等「純函式、不碰資料庫」的模組
定位不同——這裡的職責就是把純函式的輸出落地到資料庫。

版本化寫入規則——對應 Issue #19 Solution 段落：
    - 每次呼叫 save_schedule() 視為「產生一批新排程」，寫入的每一列
      plan_source='generated'、is_active=1。
    - 新舊排程的對應邏輯：同一 athlete_id、同一 planned_date、且
      plan_source='generated' 的既有生效列（is_active=1），視為被同一天的
      新列取代——舊列改為 is_active=0，superseded_by 指向新列 id，
      舊列本身不刪除、不覆蓋其餘欄位。
    - plan_source='external'（外部課表，如跟團課表）的既有列永遠不被本模組
      的寫入邏輯取代或觸碰——這呼應 periodization_scheduler 的
      _apply_external_dates()：外部課表日期本來就不會出現在
      generate_schedule() 的輸出中，本模組進一步確保寫入層也不會誤動它們。
    - 沒有舊排程可取代的日期（首次寫入該天），新列 is_active=1，
      superseded_by 維持 NULL，無需額外處理。

配速轉換：generate_schedule() 的 pace_zone 是區間
（{"fast_sec_per_km", "slow_sec_per_km"} 或 None），但 training_plan.
planned_pace_sec_per_km 是單一欄位。取區間中點作為代表值——本表本來就
只存單一配速供概覽查詢用，完整區間仍可從當初呼叫 vdot_engine 的結果
重新取得，不需要在 training_plan 這張表裡重複保存。workout_type='rest'
（pace_zone=None）時 planned_pace_sec_per_km 為 NULL。
"""

from __future__ import annotations

import datetime
import sqlite3
from typing import Any


def _pace_zone_midpoint(pace_zone: dict[str, float] | None) -> int | None:
    if pace_zone is None:
        return None
    return round((pace_zone["fast_sec_per_km"] + pace_zone["slow_sec_per_km"]) / 2)


def save_schedule(
    conn: sqlite3.Connection,
    athlete_id: int,
    schedule: list[dict[str, Any]],
    created_at: str | None = None,
) -> list[int]:
    """把 generate_schedule() 的輸出寫入 training_plan，維護版本歷史。

    schedule：periodization_scheduler.generate_schedule() 的回傳值，每筆至少
        含 "date"／"workout_type"／"target_distance_km"／"pace_zone"。

    created_at：本批寫入的時間戳記（ISO 格式字串）。未提供時使用呼叫當下的
        UTC 時間；供測試需要固定時間戳記時覆寫。

    回傳：本次新寫入列的 id 清單，依 schedule 原始順序對應。

    交易邊界：整批寫入（含取代舊列）在單一資料庫交易內完成，任何一步失敗
    整批 rollback，不會留下「舊列已停用但新列未寫入」的中間狀態。
    """
    if created_at is None:
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    new_ids: list[int] = []
    try:
        for day in schedule:
            planned_date = day["date"].isoformat()

            existing = conn.execute(
                """
                SELECT id FROM training_plan
                WHERE athlete_id = ? AND planned_date = ?
                  AND plan_source = 'generated' AND is_active = 1
                """,
                (athlete_id, planned_date),
            ).fetchone()

            cursor = conn.execute(
                """
                INSERT INTO training_plan
                    (athlete_id, planned_date, workout_type, planned_distance_km,
                     planned_pace_sec_per_km, plan_source, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, 'generated', 1, ?)
                """,
                (
                    athlete_id,
                    planned_date,
                    day["workout_type"],
                    day["target_distance_km"],
                    _pace_zone_midpoint(day["pace_zone"]),
                    created_at,
                ),
            )
            new_id = cursor.lastrowid
            new_ids.append(new_id)

            if existing is not None:
                conn.execute(
                    "UPDATE training_plan SET is_active = 0, superseded_by = ? WHERE id = ?",
                    (new_id, existing["id"]),
                )

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return new_ids


def get_active_schedule(conn: sqlite3.Connection, athlete_id: int) -> list[dict[str, Any]]:
    """取得該學員目前生效中的完整課表（is_active=1），依日期升冪排序。

    涵蓋 plan_source='generated' 與 'external' 兩者——「目前生效的完整課表」
    對使用者而言不分來源，皆是「現在該照哪個安排練」的一部分。
    """
    rows = conn.execute(
        """
        SELECT id, athlete_id, planned_date, workout_type, planned_distance_km,
               planned_duration_sec, planned_pace_sec_per_km, notes, plan_source,
               linked_activity_id, is_active, superseded_by, created_at
        FROM training_plan
        WHERE athlete_id = ? AND is_active = 1
        ORDER BY planned_date
        """,
        (athlete_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_plan_history_for_date(
    conn: sqlite3.Connection, athlete_id: int, planned_date: datetime.date
) -> list[dict[str, Any]]:
    """取得該學員、該日期的完整排程版本歷史（含已被取代的舊列與目前生效列）。

    依 created_at 升冪排序，讓呼叫端能看到「原計畫 → 後續調整」的時間順序，
    呼應 PLAN.md §5.8「原計畫 vs 調整後計畫」的偏差比對需求。
    """
    rows = conn.execute(
        """
        SELECT id, athlete_id, planned_date, workout_type, planned_distance_km,
               planned_duration_sec, planned_pace_sec_per_km, notes, plan_source,
               linked_activity_id, is_active, superseded_by, created_at
        FROM training_plan
        WHERE athlete_id = ? AND planned_date = ?
        ORDER BY created_at
        """,
        (athlete_id, planned_date.isoformat()),
    ).fetchall()
    return [dict(row) for row in rows]
