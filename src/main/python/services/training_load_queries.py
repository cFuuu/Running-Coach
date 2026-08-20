"""把 training_load.py／readiness.py 的純函式接到真實資料庫（Issue #21）。

training_load.py 的 compute_daily_loads()／compute_training_load_series()
與 readiness.py 的 assess_readiness() 全部刻意不碰資料庫，只吃合成的
list[dict] 輸入，方便單元測試。本模組是資料庫存取層：吃 sqlite3.Connection，
負責把 activities／daily_wellness／athlete_profile 的真實資料查出來、組成
這些純函式要的輸入格式，再串起完整流程。不 import fastapi，比照
dashboard_queries.py／training_plan_store.py 的分層方式。

（suggest_recovery_threshold(conn, athlete_id) 本身已是資料庫層，不在本模組
範圍內，直接呼叫 readiness.suggest_recovery_threshold() 即可。）

easy_pace_fast_sec_per_km 的降級行為——對應 Issue #21 Solution 段落：
compute_daily_loads() 的跑步配速估算退回路徑需要這個參數（來自 VDOT 引擎的
easy 配速區間下界），但本模組不負責呼叫 vdot_engine——呼叫端若有可用的 VDOT
結果，自行傳入；沒有時傳 None，讓 compute_daily_loads() 內建「無法估算則
排除」的既有降級邏輯自然生效，不在本模組內另外報錯或中止查詢。
"""

from __future__ import annotations

import datetime
import sqlite3
from typing import Any

from src.main.python.services.readiness import assess_readiness
from src.main.python.services.training_load import (
    RUNNING_ACTIVITY_TYPES,
    STRENGTH_ACTIVITY_TYPES,
    compute_daily_loads,
    compute_training_load_series,
)

# training_load.compute_activity_load() 只認得這兩類 activity_type，其餘一律
# 被純函式排除——查詢層先用同一份集合過濾，避免撈出用不到的資料列。
_LOAD_RELEVANT_ACTIVITY_TYPES: tuple[str, ...] = tuple(
    RUNNING_ACTIVITY_TYPES | STRENGTH_ACTIVITY_TYPES
)


def get_activities_for_load(
    conn: sqlite3.Connection,
    athlete_id: int,
    start_date: datetime.date,
    end_date: datetime.date,
) -> list[dict[str, Any]]:
    """查出 [start_date, end_date] 區間內、與訓練負荷計算相關的活動。

    只查 RUNNING_ACTIVITY_TYPES／STRENGTH_ACTIVITY_TYPES 涵蓋的 activity_type
    （training_load.compute_activity_load() 對其餘類型一律回傳 None 排除，
    查詢層先過濾掉可省去下游無謂的逐筆判斷）。

    回傳每筆至少含 training_load.compute_activity_load() 需要的欄位：
    "date"（datetime.date）／"activity_type"／"duration_sec"／"avg_hr_bpm"／
    "avg_pace_sec_per_km"。同一天可能有多筆（compute_daily_loads() 會加總）。
    """
    placeholders = ",".join("?" for _ in _LOAD_RELEVANT_ACTIVITY_TYPES)
    rows = conn.execute(
        f"""
        SELECT date(started_at) AS activity_date, activity_type, duration_sec,
               avg_hr_bpm, avg_pace_sec_per_km
        FROM activities
        WHERE athlete_id = ?
          AND activity_type IN ({placeholders})
          AND date(started_at) >= ? AND date(started_at) <= ?
        ORDER BY started_at
        """,
        (athlete_id, *_LOAD_RELEVANT_ACTIVITY_TYPES, start_date.isoformat(), end_date.isoformat()),
    ).fetchall()

    return [
        {
            "date": datetime.date.fromisoformat(row["activity_date"]),
            "activity_type": row["activity_type"],
            "duration_sec": row["duration_sec"],
            "avg_hr_bpm": row["avg_hr_bpm"],
            "avg_pace_sec_per_km": row["avg_pace_sec_per_km"],
        }
        for row in rows
    ]


def get_hr_params(
    conn: sqlite3.Connection, athlete_id: int
) -> tuple[float | None, float | None]:
    """取得該學員的 max_hr_bpm／resting_hr_bpm，供心率強度換算使用。

    回傳 (max_hr_bpm, resting_hr_bpm)。學員不存在或欄位未設定時對應位置為
    None——不報錯，讓下游純函式（compute_activity_load 等）既有的降級邏輯
    （缺心率退回配速估算，或明確排除）自然生效。
    """
    row = conn.execute(
        "SELECT max_hr_bpm, resting_hr_bpm FROM athlete_profile WHERE id = ?",
        (athlete_id,),
    ).fetchone()
    if row is None:
        return None, None
    return row["max_hr_bpm"], row["resting_hr_bpm"]


def get_wellness_for_readiness(
    conn: sqlite3.Connection,
    athlete_id: int,
    start_date: datetime.date,
    end_date: datetime.date,
) -> list[dict[str, Any]]:
    """查出 [start_date, end_date] 區間內、供 readiness.assess_readiness() 使用的每日身體數據。

    回傳每筆至少含 "date"（datetime.date）／"hrv_ms"／"hrv_weekly_avg_ms"。
    某天缺紀錄的日期不會出現在回傳清單中——assess_readiness() 對缺紀錄的日期
    本來就視為該天 HRV 維度資料缺失，呼叫端不需要為缺漏日期補上佔位列。
    """
    rows = conn.execute(
        """
        SELECT date, hrv_ms, hrv_weekly_avg_ms
        FROM daily_wellness
        WHERE athlete_id = ? AND date >= ? AND date <= ?
        ORDER BY date
        """,
        (athlete_id, start_date.isoformat(), end_date.isoformat()),
    ).fetchall()

    return [
        {
            "date": datetime.date.fromisoformat(row["date"]),
            "hrv_ms": row["hrv_ms"],
            "hrv_weekly_avg_ms": row["hrv_weekly_avg_ms"],
        }
        for row in rows
    ]


def compute_readiness_for_athlete(
    conn: sqlite3.Connection,
    athlete_id: int,
    start_date: datetime.date,
    end_date: datetime.date,
    easy_pace_fast_sec_per_km: float | None = None,
) -> list[dict[str, Any]]:
    """完整流程：查活動 → 每日負荷 → ATL/CTL/TSB → 查 wellness → readiness 判讀。

    easy_pace_fast_sec_per_km：見本模組頂端說明，未提供 VDOT 結果時傳 None，
    跑步活動缺心率又缺此參數的情況會被 compute_daily_loads() 明確排除，
    不影響其餘可估算的活動。

    個人化恢復閾值（athlete_profile.high_risk_consecutive_training_days）
    在此一併查出並傳入 assess_readiness()，未設定（NULL）時該函式自行退回
    預設值，本函式不重複那段降級邏輯。

    回傳：readiness.assess_readiness() 的輸出，依日期升冪排序，涵蓋
    [start_date, end_date] 整段區間。
    """
    max_hr_bpm, resting_hr_bpm = get_hr_params(conn, athlete_id)

    activities = get_activities_for_load(conn, athlete_id, start_date, end_date)
    daily_loads = compute_daily_loads(
        activities,
        start_date,
        end_date,
        max_hr_bpm=max_hr_bpm,
        resting_hr_bpm=resting_hr_bpm,
        easy_pace_fast_sec_per_km=easy_pace_fast_sec_per_km,
    )
    training_load_series = compute_training_load_series(daily_loads)

    daily_wellness = get_wellness_for_readiness(conn, athlete_id, start_date, end_date)

    threshold_row = conn.execute(
        "SELECT high_risk_consecutive_training_days FROM athlete_profile WHERE id = ?",
        (athlete_id,),
    ).fetchone()
    high_risk_consecutive_training_days = (
        threshold_row["high_risk_consecutive_training_days"] if threshold_row else None
    )

    return assess_readiness(
        training_load_series,
        daily_wellness,
        high_risk_consecutive_training_days=high_risk_consecutive_training_days,
    )
