"""把單筆活動歷史轉換成「每日總訓練負荷」，供下游 EWMA 疲勞遞推使用（Issue #13）。

本模組刻意不碰資料庫：吃活動清單（純 dict/list）與心率參數，回傳每日負荷結構，
方便單元測試。資料庫查詢（拉活動、拉 HRmax/RHR）交給上層 orchestrator 負責。
（與 vdot_engine.py / periodization_scheduler.py 的分層方式一致。）

負荷單位與強度估算——對應 Issue #12 grill 討論結論：
跑步與重訓的「每日負荷」都歸一到「實際持續時間（秒）× 強度百分比」這個共同基礎，
兩者因此可以直接相加，因為身體疲勞不分運動類型：
    - 跑步優先用該次活動平均心率相對心率儲備（HRR = HRmax − RHR）的百分比當強度；
      缺心率資料時，退回用配速相對 VDOT easy 配速基準估算強度。
    - 重訓優先用同一套心率相對 HRR 公式；連心率都沒有，則用固定保守估計強度
      （50% HRR），並標記該筆負荷為不確定估算。
    - 完全無法估算強度的活動（跑步無心率也無配速）明確排除，不列入負荷計算，
      不強行給 0 或預設值掩蓋缺失。

Issue #13（compute_daily_loads）只算「今天身體承受了多少負荷」，不含任何跨日
疲勞累積邏輯；Issue #14（compute_training_load_series）接手這段每日負荷序列，
用 Banister impulse-response 模型的標準做法——指數加權移動平均（EWMA）分別對
每日負荷做急性（ATL，7 天時間常數）與慢性（CTL，42 天時間常數）平滑，
TSB（形態分數）= CTL − ATL。兩個時間常數沿用運動科學界廣泛驗證的標準值。
"""

from __future__ import annotations

import datetime
from typing import Any

# --- 可調閾值（集中管理，勿散落於邏輯中）---

# 視為「跑步類」的 activity_type。跟 dashboard_queries.py 的 RUNNING_ACTIVITY_TYPES
# 同一慣例——可調整的參數而非寫死的假設，之後要納入其他跑步子類型時擴充這裡即可。
RUNNING_ACTIVITY_TYPES: frozenset[str] = frozenset(
    {"running", "treadmill_running", "track_running", "trail_running"}
)

# 視為「重訓類」的 activity_type。同上，可調整參數。
STRENGTH_ACTIVITY_TYPES: frozenset[str] = frozenset({"strength_training"})

# 重訓完全無心率資料時的固定保守估計強度（% HRR）。
# 50% HRR 對應一般認知中「輕度～中度」的體能消耗強度，作為缺乏實測時的保守下界，
# 避免高估重訓造成的疲勞負荷。
STRENGTH_FALLBACK_HRR_PCT = 0.5

# 跑步無心率、退回用配速估算強度時，配速相對 VDOT easy 配速基準的估算表：
# key 為「活動配速 / easy 配速下界」的比值上限，value 為對應估算的 %HRR 強度。
# 比值越小（跑得比 easy 配速快）代表強度越高。此為簡化分段估算，非連續函式，
# 未來若累積足夠真實資料應重新校正。
_PACE_RATIO_TO_HRR_PCT: tuple[tuple[float, float], ...] = (
    (0.90, 0.85),  # 明顯快於 easy 配速下界 → 視為中高強度
    (1.00, 0.75),  # 落在 easy 配速區間內 → 視為 easy 強度
    (1.15, 0.65),  # 略慢於 easy 配速 → 視為低強度（如恢復跑）
)
# 比值超過上表最後一個門檻時的強度下限（極慢配速，如健走）。
_PACE_RATIO_FALLBACK_HRR_PCT = 0.5

# ATL（急性疲勞）EWMA 時間常數（天）。Banister impulse-response 模型標準值，
# 反映短期訓練壓力的快速累積與消退。
ATL_TIME_CONSTANT_DAYS = 7

# CTL（慢性體能水平）EWMA 時間常數（天）。同上標準值，反映長期訓練適應，
# 累積與消退速度皆遠慢於 ATL。
CTL_TIME_CONSTANT_DAYS = 42


def compute_hrr_intensity_pct(
    avg_hr_bpm: float,
    max_hr_bpm: float,
    resting_hr_bpm: float,
) -> float:
    """依平均心率算出相對心率儲備（HRR = HRmax − RHR）的強度百分比。

    公式：intensity_pct = (avg_hr - RHR) / (HRmax - RHR)，即 Karvonen 公式的
    強度換算部分。結果 clamp 在 [0, 1] 區間，避免感測器雜訊或低於安靜心率的
    異常值造成負值或超過 100% 的強度。
    """
    hrr = max_hr_bpm - resting_hr_bpm
    if hrr <= 0:
        raise ValueError("max_hr_bpm 必須大於 resting_hr_bpm，才能計算心率儲備")
    intensity_pct = (avg_hr_bpm - resting_hr_bpm) / hrr
    return max(0.0, min(1.0, intensity_pct))


def _estimate_intensity_from_pace(
    pace_sec_per_km: float,
    easy_pace_fast_sec_per_km: float,
) -> float:
    """跑步無心率資料時，用配速相對 VDOT easy 配速下界的比值估算 %HRR 強度。

    比值 = 活動配速 / easy 配速下界（fast 端）。比值越小代表跑得比 easy 配速
    區間還快，強度估算越高；比值越大代表配速越慢，強度估算越低。
    """
    ratio = pace_sec_per_km / easy_pace_fast_sec_per_km
    for threshold, hrr_pct in _PACE_RATIO_TO_HRR_PCT:
        if ratio <= threshold:
            return hrr_pct
    return _PACE_RATIO_FALLBACK_HRR_PCT


def _resolve_running_intensity(
    activity: dict[str, Any],
    max_hr_bpm: float | None,
    resting_hr_bpm: float | None,
    easy_pace_fast_sec_per_km: float | None,
) -> tuple[float | None, bool]:
    """回傳 (強度百分比或 None, 是否為不確定估算)。"""
    avg_hr_bpm = activity.get("avg_hr_bpm")
    if avg_hr_bpm and max_hr_bpm and resting_hr_bpm:
        return compute_hrr_intensity_pct(avg_hr_bpm, max_hr_bpm, resting_hr_bpm), False

    pace_sec_per_km = activity.get("avg_pace_sec_per_km")
    if pace_sec_per_km and easy_pace_fast_sec_per_km:
        return (
            _estimate_intensity_from_pace(pace_sec_per_km, easy_pace_fast_sec_per_km),
            True,
        )

    # 無心率也無配速（或缺少換算所需的 VDOT 基準）：完全無法估算，明確排除。
    return None, False


def _resolve_strength_intensity(
    activity: dict[str, Any],
    max_hr_bpm: float | None,
    resting_hr_bpm: float | None,
) -> tuple[float, bool]:
    """回傳 (強度百分比, 是否為不確定估算)。重訓一定能算出強度（有保守退回值）。"""
    avg_hr_bpm = activity.get("avg_hr_bpm")
    if avg_hr_bpm and max_hr_bpm and resting_hr_bpm:
        return compute_hrr_intensity_pct(avg_hr_bpm, max_hr_bpm, resting_hr_bpm), False
    return STRENGTH_FALLBACK_HRR_PCT, True


def compute_activity_load(
    activity: dict[str, Any],
    max_hr_bpm: float | None = None,
    resting_hr_bpm: float | None = None,
    easy_pace_fast_sec_per_km: float | None = None,
) -> dict[str, Any] | None:
    """算出單筆活動的訓練負荷。

    activity：至少包含
        - "date": datetime.date，活動日期
        - "activity_type": str，比對 RUNNING_ACTIVITY_TYPES / STRENGTH_ACTIVITY_TYPES
        - "duration_sec": float，實際持續時間（秒）
        - "avg_hr_bpm"：可選，平均心率
        - "avg_pace_sec_per_km"：可選，跑步配速（重訓活動忽略此欄位）

    easy_pace_fast_sec_per_km：VDOT 引擎算出的 easy 配速區間下界（秒/公里），
        供跑步活動缺心率時的配速估算退回路徑使用。沿用 vdot_engine.compute_pace_zones()
        的 "easy"."fast_sec_per_km" 語意，由呼叫端傳入（本函式不碰 VDOT 引擎）。

    回傳：
        可計算負荷時：
            {
                "load": float,  # duration_sec * intensity_pct
                "intensity_pct": float,
                "uncertain": bool,  # 是否為不確定估算（重訓保守退回 / 跑步配速估算）
            }
        完全無法估算強度時：None（明確排除，不強行給 0 或預設值掩蓋缺失）。
    """
    activity_type = activity.get("activity_type")
    duration_sec = activity.get("duration_sec")
    if not duration_sec or duration_sec <= 0:
        return None

    if activity_type in RUNNING_ACTIVITY_TYPES:
        intensity_pct, uncertain = _resolve_running_intensity(
            activity, max_hr_bpm, resting_hr_bpm, easy_pace_fast_sec_per_km
        )
        if intensity_pct is None:
            return None
    elif activity_type in STRENGTH_ACTIVITY_TYPES:
        intensity_pct, uncertain = _resolve_strength_intensity(
            activity, max_hr_bpm, resting_hr_bpm
        )
    else:
        # 非跑步/重訓類型（目前 Phase 2 範圍未涵蓋），不列入負荷計算。
        return None

    return {
        "load": duration_sec * intensity_pct,
        "intensity_pct": intensity_pct,
        "uncertain": uncertain,
    }


def compute_daily_loads(
    activities: list[dict[str, Any]],
    start_date: datetime.date,
    end_date: datetime.date,
    max_hr_bpm: float | None = None,
    resting_hr_bpm: float | None = None,
    easy_pace_fast_sec_per_km: float | None = None,
) -> list[dict[str, Any]]:
    """把活動清單彙整成 [start_date, end_date] 區間內每日一筆的總負荷序列。

    同一天有多筆活動時，各自算出的負荷直接相加。完全無法估算強度的活動
    （見 compute_activity_load 回傳 None）不計入當天總負荷，也不影響
    當天是否標記 uncertain（uncertain 只反映「有算出負荷、但估算不確定」的情況）。
    沒有訓練的日期，當天負荷為 0（供 Issue #14 的 EWMA 遞推使用）。

    回傳：依日期升冪排序的 list，每筆
        {
            "date": datetime.date,
            "load": float,          # 當天總負荷，無訓練為 0.0
            "uncertain": bool,      # 當天是否含不確定估算的活動
        }
        涵蓋 [start_date, end_date] 區間每一天，無缺漏日期。
    """
    if start_date > end_date:
        raise ValueError("start_date 不可晚於 end_date")

    daily: dict[datetime.date, dict[str, Any]] = {
        start_date + datetime.timedelta(days=offset): {"load": 0.0, "uncertain": False}
        for offset in range((end_date - start_date).days + 1)
    }

    for activity in activities:
        activity_date = activity["date"]
        if activity_date not in daily:
            continue

        result = compute_activity_load(
            activity, max_hr_bpm, resting_hr_bpm, easy_pace_fast_sec_per_km
        )
        if result is None:
            continue

        entry = daily[activity_date]
        entry["load"] += result["load"]
        entry["uncertain"] = entry["uncertain"] or result["uncertain"]

    return [
        {"date": date, "load": entry["load"], "uncertain": entry["uncertain"]}
        for date, entry in sorted(daily.items())
    ]


def _ewma_step(previous: float, today_load: float, time_constant_days: int) -> float:
    """Banister impulse-response 模型的 EWMA 遞推單步公式。

    today_value = previous + (today_load - previous) * (1 - e^(-1/time_constant))
    時間常數越小，對當日負荷的反應越快（ATL）；越大則越平滑、反應越慢（CTL）。
    """
    alpha = 1 - pow(2.718281828459045, -1.0 / time_constant_days)
    return previous + (today_load - previous) * alpha


def compute_training_load_series(
    daily_loads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把 compute_daily_loads() 輸出的每日負荷序列，遞推成 ATL/CTL/TSB 時間序列。

    daily_loads：依日期升冪排序的 list，每筆至少含 "date"／"load"／"uncertain"
        （即 compute_daily_loads() 的回傳格式）。

    遞推規則：
        - 第一天的 ATL/CTL 直接等於當天負荷（不人為設為 0），避免起始段被低估。
        - 之後每一天依 _ewma_step() 用當天負荷更新前一天的 ATL/CTL。
        - 沒有訓練的日期（load=0）仍照規則遞推，讓 ATL/CTL 依 EWMA 公式自然
          衰減，反映恢復過程。
        - TSB = CTL − ATL，逐日計算。
        - "uncertain" 標記原樣透傳（不對負荷數值做任何主觀判讀，那是恢復判斷
          邏輯或 Phase 3 AI 教練的職責）。

    回傳：依日期升冪排序的 list，每筆
        {
            "date": datetime.date,
            "load": float,      # 當日總負荷（原樣透傳）
            "atl": float,
            "ctl": float,
            "tsb": float,
            "uncertain": bool,  # 原樣透傳自輸入
        }
        涵蓋輸入的整段日期範圍，無缺漏日期。輸入為空清單時回傳空清單。
    """
    series: list[dict[str, Any]] = []
    atl = ctl = 0.0

    for index, day in enumerate(daily_loads):
        load = day["load"]
        if index == 0:
            atl = ctl = load
        else:
            atl = _ewma_step(atl, load, ATL_TIME_CONSTANT_DAYS)
            ctl = _ewma_step(ctl, load, CTL_TIME_CONSTANT_DAYS)

        series.append(
            {
                "date": day["date"],
                "load": load,
                "atl": atl,
                "ctl": ctl,
                "tsb": ctl - atl,
                "uncertain": day["uncertain"],
            }
        )

    return series
