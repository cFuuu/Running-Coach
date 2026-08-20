"""給定訓練負荷趨勢（TSB）與每日身體數據，判讀恢復狀態（Issue #17）。

本模組刻意不碰資料庫：吃 training_load.compute_training_load_series() 的輸出、
每日 wellness 資料（純 dict/list）、個人化恢復閾值，回傳狀態評分/旗標結構，
方便單元測試。資料庫查詢（拉 TSB 序列、拉 daily_wellness、拉 athlete_profile
閾值欄位）交給上層 orchestrator 負責。（與 vdot_engine.py / training_load.py
的分層方式一致。）

判斷邊界——對應 Issue #15 grill 討論結論：
本模組只輸出狀態評分/旗標 + 觸發原因，**明確不寫入或修改 training_plan**，
也不對「這樣好不好該怎麼辦」給出行動建議——那是 Phase 3 AI 教練的職責。

判斷維度：
    1. 連續訓練天數：連續無休息日達到（個人化或預設）閾值，視為高風險訊號。
    2. TSB 趨勢：急性疲勞持續高於慢性負荷（TSB 為負且達一定幅度），代表短期
       疲勞尚未被慢性體能消化，是 Banister 模型中「形態下滑」的直接訊號。
    3. HRV 相對近期趨勢：當日 HRV 相對 7 日均值明顯下降，是自律神經系統
       尚未恢復的常見生理訊號。此維度缺資料時直接跳過，不影響其他維度判斷
       （呼應 1C 的資料完整度標記，不因單一欄位缺失就整體失效）。

任一維度觸發即整體判定為 low；三個維度都無法判斷（訓練與 wellness 資料都缺）
時明確標記 unavailable，不勉強給出誤導性的 normal。
"""

from __future__ import annotations

import datetime
from typing import Any

# --- 可調閾值（集中管理，勿散落於邏輯中）---

# 個人化閾值（athlete_profile.high_risk_consecutive_training_days）未設定時
# 退回使用的預設值。取一般訓練科學經驗中「連續訓練一週左右即應有恢復訊號」
# 的保守下界。
DEFAULT_HIGH_RISK_CONSECUTIVE_TRAINING_DAYS = 6

# TSB 低於此值視為「急性疲勞明顯高於慢性負荷」的觸發門檻。運動科學界常見
# 經驗：TSB 落在 -10 ~ -30 之間屬正常訓練期的疲勞累積，超過 -30 常伴隨
# 過度訓練風險升高，取 -30 作為觸發門檻。
TSB_LOW_THRESHOLD = -30.0

# 當日 HRV 相對 7 日均值的下降幅度超過此比例，視為自律神經恢復不足的訊號。
# 取 15% 作為常見訓練監控實務中的保守分界。
HRV_DROP_PCT_THRESHOLD = 0.15


def _consecutive_training_days_ending_at(
    daily_loads_by_date: dict[datetime.date, float], as_of_date: datetime.date
) -> int:
    """算出截至 as_of_date（含當天）的連續訓練天數（load > 0 視為有訓練）。

    日期序列中斷（呼叫端資料缺漏）視同休息日，不假設連續。
    """
    count = 0
    current = as_of_date
    while daily_loads_by_date.get(current, 0.0) > 0:
        count += 1
        current -= datetime.timedelta(days=1)
    return count


def _evaluate_consecutive_training(
    consecutive_days: int, threshold: int
) -> dict[str, Any] | None:
    if consecutive_days < threshold:
        return None
    return {
        "dimension": "consecutive_training_days",
        "detail": f"連續訓練 {consecutive_days} 天，達到閾值 {threshold} 天",
    }


def _evaluate_tsb(tsb: float) -> dict[str, Any] | None:
    if tsb >= TSB_LOW_THRESHOLD:
        return None
    return {
        "dimension": "tsb",
        "detail": f"TSB 為 {tsb:.1f}，低於門檻 {TSB_LOW_THRESHOLD:.1f}（急性疲勞明顯高於慢性負荷）",
    }


def _evaluate_hrv(hrv_ms: float | None, hrv_weekly_avg_ms: float | None) -> dict[str, Any] | None:
    if not hrv_ms or not hrv_weekly_avg_ms:
        # 缺資料：無法判斷，跳過（不視為觸發，也不視為正常）。
        return None
    drop_pct = (hrv_weekly_avg_ms - hrv_ms) / hrv_weekly_avg_ms
    if drop_pct < HRV_DROP_PCT_THRESHOLD:
        return None
    return {
        "dimension": "hrv",
        "detail": (
            f"當日 HRV {hrv_ms:.1f}ms 較 7 日均值 {hrv_weekly_avg_ms:.1f}ms "
            f"下降 {drop_pct * 100:.1f}%，超過門檻 {HRV_DROP_PCT_THRESHOLD * 100:.0f}%"
        ),
    }


def assess_readiness(
    training_load_series: list[dict[str, Any]],
    daily_wellness: list[dict[str, Any]],
    high_risk_consecutive_training_days: int | None = None,
) -> list[dict[str, Any]]:
    """對 training_load_series 涵蓋的每一天，輸出恢復狀態評分/旗標。

    training_load_series：training_load.compute_training_load_series() 的輸出
        （每筆至少含 "date"／"load"／"tsb"）。

    daily_wellness：每筆至少含 "date"／"hrv_ms"／"hrv_weekly_avg_ms"（其餘
        daily_wellness 欄位本模組目前不使用，但不因額外欄位存在而出錯）。
        某天缺對應紀錄，或紀錄中 hrv_ms/hrv_weekly_avg_ms 為 None，視為該天
        HRV 維度資料缺失，判斷邏輯降級為僅依可用維度判斷。

    high_risk_consecutive_training_days：athlete_profile 的個人化恢復閾值，
        可能為 NULL（None）。為 None 時退回使用
        DEFAULT_HIGH_RISK_CONSECUTIVE_TRAINING_DAYS，並在輸出中標記
        "threshold_source": "default"；否則標記 "threshold_source": "personalized"。

    回傳：依日期升冪排序的 list，每筆
        {
            "date": datetime.date,
            "readiness": "low" | "normal",
            "triggers": list[dict],  # 觸發的維度與說明，空 list 代表無觸發
            "threshold_source": "default" | "personalized",
            "consecutive_training_days": int,
        }
        涵蓋輸入 training_load_series 的整段日期範圍。輸入為空清單時回傳空清單。

    本函式不碰資料庫，呼叫後不會、也無從修改 training_plan 或其他任何資料表。
    """
    threshold = (
        high_risk_consecutive_training_days
        if high_risk_consecutive_training_days is not None
        else DEFAULT_HIGH_RISK_CONSECUTIVE_TRAINING_DAYS
    )
    threshold_source = (
        "personalized" if high_risk_consecutive_training_days is not None else "default"
    )

    daily_loads_by_date = {day["date"]: day["load"] for day in training_load_series}
    wellness_by_date = {day["date"]: day for day in daily_wellness}

    results: list[dict[str, Any]] = []
    for day in training_load_series:
        date = day["date"]
        consecutive_days = _consecutive_training_days_ending_at(daily_loads_by_date, date)
        wellness = wellness_by_date.get(date, {})

        triggers = []
        for trigger in (
            _evaluate_consecutive_training(consecutive_days, threshold),
            _evaluate_tsb(day["tsb"]),
            _evaluate_hrv(wellness.get("hrv_ms"), wellness.get("hrv_weekly_avg_ms")),
        ):
            if trigger is not None:
                triggers.append(trigger)

        results.append(
            {
                "date": date,
                "readiness": "low" if triggers else "normal",
                "triggers": triggers,
                "threshold_source": threshold_source,
                "consecutive_training_days": consecutive_days,
            }
        )

    return results
