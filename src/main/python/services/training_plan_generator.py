"""串接 VDOT 引擎 → 週期化排程器 → training_plan 寫入層的完整流程（Issue #20）。

vdot_engine.py／periodization_scheduler.py／training_plan_store.py 都是各自
獨立、刻意不碰資料庫（或只做資料庫存取，不含規則判斷）的模組。本模組是
**資料庫存取層 + 流程串接**，不引入新的規則邏輯——所有訓練科學判斷都委派給
既有三個模組，這裡只負責「查資料 → 組參數 → 呼叫 → 寫回」。

流程：
    1. 查該學員的 activities（跑步與重訓歷史）與 athlete_profile
       （max_hr_bpm／max_hr_source／resting_hr_bpm）
    2. 呼叫 vdot_engine.estimate_vdot_and_paces() 取得配速區間；
       available=False 時明確中止，不產生課表、不寫入資料庫
    3. 組出 periodization_scheduler.generate_schedule() 的 config
    4. 呼叫 generate_schedule() 產生單日課表
    5. 呼叫 training_plan_store.save_schedule() 落地寫入（正確處理版本化）

VDOT 候選活動的格式轉換——activities 表沒有現成的 distance_category／
is_max_effort 欄位，本模組在查詢時轉換：
    - distance_category：依 distance_km 落在哪個標準距離的容忍誤差範圍內
      判斷（±該距離 5% 或至少 0.3km，兩者取大——寬鬆到足以涵蓋 GPS 誤差與
      路線繞路，但不到會把 10K 誤判成半馬的程度）。distance_km 不落在任何
      已知距離容忍範圍內的活動，不是 VDOT 候選，直接排除（vdot_engine 對
      short_interval 這類無標準距離的類別本來就不支援 Riegel 推算，見
      vdot_engine.vdot_from_race()，故本模組也不嘗試映射它）。
    - is_max_effort：activities.workout_type='race'（人工標記的比賽記錄）
      視為全力程度；其餘一律 False，交由 vdot_engine 既有的心率強度換算
      路徑處理（缺心率則該筆活動不合格，被 select_candidate 排除）。

athlete_profile 缺 max_hr_bpm／resting_hr_bpm 時的行為：兩者都是 vdot_engine
換算非全力候選（%HRR）所需的參數，缺了它們 vdot_engine 仍可能靠
is_max_effort=True 的候選（比賽成績，不需要換算）成功推算 VDOT，因此不在
本模組內提前中止，原樣傳 None 讓 vdot_engine 的既有邏輯判斷是否可用。
"""

from __future__ import annotations

import datetime
import sqlite3
from typing import Any

from src.main.python.services import training_plan_store
from src.main.python.services.periodization_scheduler import generate_schedule
from src.main.python.services.vdot_engine import estimate_vdot_and_paces

# distance_category → 標準距離（公里）。與 vdot_engine._DISTANCE_KM 的類別
# 一致（short_interval 除外，見模組說明），供 distance_km 反推 distance_category。
_STANDARD_DISTANCES_KM: dict[str, float] = {
    "marathon": 42.195,
    "half_marathon": 21.0975,
    "10k": 10.0,
    "5k": 5.0,
    "3k": 3.0,
}

# 反推 distance_category 時的容忍誤差比例（相對標準距離）。
_DISTANCE_MATCH_TOLERANCE_PCT = 0.05

# 容忍誤差的絕對下限（公里）——避免短距離（如 5K）因誤差比例換算出的絕對值
# 過小，讓正常 GPS 誤差／繞路的活動被誤判為不符合任何距離類別。
_DISTANCE_MATCH_TOLERANCE_MIN_KM = 0.3

# activities.workout_type 標記為比賽的值——沿用 schema.sql 既有 CHECK 定義。
_RACE_WORKOUT_TYPE = "race"


def _distance_category_for_km(distance_km: float | None) -> str | None:
    if not distance_km:
        return None
    for category, standard_km in _STANDARD_DISTANCES_KM.items():
        tolerance = max(standard_km * _DISTANCE_MATCH_TOLERANCE_PCT, _DISTANCE_MATCH_TOLERANCE_MIN_KM)
        if abs(distance_km - standard_km) <= tolerance:
            return category
    return None


def get_vdot_candidate_activities(
    conn: sqlite3.Connection, athlete_id: int
) -> list[dict[str, Any]]:
    """查出該學員的跑步活動，轉換成 vdot_engine.select_candidate() 要的格式。

    只回傳 distance_km 落在某個標準距離容忍範圍內的活動（見模組頂端說明）；
    距離對不上任何已知類別的活動不是有效候選，不列入回傳清單。

    比賽場次（workout_type='race'）的距離類別與配速改由 race_result() 決定，
    見該函式說明。
    """
    rows = conn.execute(
        """
        SELECT date(started_at) AS activity_date, distance_km, duration_sec, avg_hr_bpm,
               avg_pace_sec_per_km, workout_type, race_official_time_sec,
               race_official_distance_km
        FROM activities
        WHERE athlete_id = ? AND distance_km IS NOT NULL
        ORDER BY started_at
        """,
        (athlete_id,),
    ).fetchall()

    candidates: list[dict[str, Any]] = []
    for row in rows:
        is_race = row["workout_type"] == _RACE_WORKOUT_TYPE
        if is_race:
            result = race_result(row)
            if result is None:
                continue
            distance_category, pace = result["distance_category"], result["pace_sec_per_km"]
        else:
            distance_category = _distance_category_for_km(row["distance_km"])
            if distance_category is None:
                continue
            pace = row["avg_pace_sec_per_km"]
        candidates.append(
            {
                "date": datetime.date.fromisoformat(row["activity_date"]),
                "distance_category": distance_category,
                "is_max_effort": is_race,
                "avg_hr_bpm": row["avg_hr_bpm"],
                "pace_sec_per_km": pace,
            }
        )
    return candidates


def race_result(row: Any) -> dict[str, Any] | None:
    """一場比賽的成績：距離類別、完賽時間、配速。VDOT 候選與儀表板最佳成績
    共用這一份規則，兩邊對同一場比賽的判讀必須一致。

    row 需有 distance_km、duration_sec、race_official_time_sec、
    race_official_distance_km 四個欄位（sqlite3.Row 或 dict 皆可）。

    - 距離類別：有官方距離用官方距離，否則用手錶距離（舊錶 GPS 偏差會歸錯類）
    - 時間：有官方成績用官方成績，否則用手錶 duration_sec（忘了按停等操作
      會污染手錶時間，實例：3K 測驗官方 13:59，手錶記 14:26）
    - 配速：一律「時間 ÷ 標準距離」，不用手錶的 avg_pace——比賽距離是固定的，
      手錶多量的 GPS 距離不該讓配速看起來變快

    距離對不上任何標準類別、或沒有時間時回傳 None。
    """
    category = _distance_category_for_km(
        row["race_official_distance_km"] or row["distance_km"]
    )
    time_sec = row["race_official_time_sec"] or row["duration_sec"]
    if category is None or not time_sec:
        return None
    return {
        "distance_category": category,
        "time_sec": time_sec,
        "pace_sec_per_km": time_sec / _STANDARD_DISTANCES_KM[category],
        "official": bool(row["race_official_time_sec"]),
    }


def _get_hr_profile(
    conn: sqlite3.Connection, athlete_id: int
) -> dict[str, Any]:
    """取出 VDOT 推算需要的 HR 參數，key 與 estimate_vdot_and_paces() 的
    參數同名，可直接 ** 展開傳入。查無此人時值皆為 None。"""
    row = conn.execute(
        "SELECT max_hr_bpm, max_hr_source, resting_hr_bpm FROM athlete_profile WHERE id = ?",
        (athlete_id,),
    ).fetchone()
    return {
        "max_hr_bpm": row["max_hr_bpm"] if row else None,
        "max_hr_source": row["max_hr_source"] if row else None,
        "resting_hr_bpm": row["resting_hr_bpm"] if row else None,
    }


def get_pace_zones(
    conn: sqlite3.Connection,
    athlete_id: int,
    reference_date: datetime.date | None = None,
) -> dict[str, Any] | None:
    """取得該學員的 VDOT 配速區間；無可用候選成績而推不出時回傳 None。

    reference_date：新鮮度的基準日，同時**排除該日之後的活動**——評估一場
        兩年前的訓練時，不該拿今天的體能當比較基準。未提供時等同「現在」，
        沿用 vdot_engine 預設（以今天為基準、不排除任何活動）。
    """
    candidates = get_vdot_candidate_activities(conn, athlete_id)
    if reference_date is not None:
        candidates = [c for c in candidates if c["date"] <= reference_date]
    result = estimate_vdot_and_paces(
        candidates,
        reference_date=reference_date,
        **_get_hr_profile(conn, athlete_id),
    )
    return result["pace_zones"] if result["available"] else None


def generate_and_save_plan(
    conn: sqlite3.Connection,
    athlete_id: int,
    start_date: datetime.date,
    total_weeks: int,
    days_per_week: int,
    is_first_marathon: bool = False,
    constraint_windows: list[dict[str, Any]] | None = None,
    external_dates: list[datetime.date] | None = None,
    reference_date: datetime.date | None = None,
) -> dict[str, Any]:
    """完整流程：查歷史活動 → 算 VDOT/配速 → 產生單日課表 → 寫入 training_plan。

    reference_date：VDOT 候選活動新鮮度判斷的基準日，未提供時
        vdot_engine.select_candidate() 預設為今天（可覆寫供測試使用）。

    回傳：
        成功時：
            {
                "available": True,
                "vdot": float,
                "schedule": list[dict],       # generate_schedule() 的原始輸出
                "training_plan_ids": list[int],  # 寫入 training_plan 的新列 id
            }
        VDOT 無法推算時（無可用候選成績）：
            {"available": False, "reason": str}
    未寫入任何資料庫列即代表失敗——VDOT 無法推算時本函式在呼叫
    periodization_scheduler／training_plan_store 之前就明確中止。
    """
    candidate_activities = get_vdot_candidate_activities(conn, athlete_id)
    vdot_result = estimate_vdot_and_paces(
        candidate_activities,
        reference_date=reference_date,
        **_get_hr_profile(conn, athlete_id),
    )
    if not vdot_result["available"]:
        return {"available": False, "reason": vdot_result["reason"]}

    config: dict[str, Any] = {
        "start_date": start_date,
        "total_weeks": total_weeks,
        "days_per_week": days_per_week,
        "pace_zones": vdot_result["pace_zones"],
        "is_first_marathon": is_first_marathon,
    }
    if constraint_windows:
        config["constraint_windows"] = constraint_windows
    if external_dates:
        config["external_dates"] = set(external_dates)

    schedule = generate_schedule(config)
    training_plan_ids = training_plan_store.save_schedule(conn, athlete_id, schedule)

    return {
        "available": True,
        "vdot": vdot_result["vdot"],
        "schedule": schedule,
        "training_plan_ids": training_plan_ids,
    }
