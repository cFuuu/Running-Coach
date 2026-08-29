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

suggest_recovery_threshold()（Issue #18）是另一個定位：獨立的**分析工具**，
會碰資料庫掃描歷史 activities/daily_wellness，估算「連續訓練幾天後開始出現
HRV 惡化」的建議閾值，供人工參考後手動寫入 athlete_profile（子 A 的欄位）。
這與本模組其餘函式「純函式、不碰資料庫」的定位不同，故獨立標註；明確不寫入
athlete_profile，任何寫入都必須是呼叫端另外執行的獨立步驟。
"""

from __future__ import annotations

import datetime
import sqlite3
from typing import Any

from . import data_confidence

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

# suggest_recovery_threshold() 用同一套 HRV 下降門檻判斷「該次連續訓練是否
# 觀察到恢復訊號惡化」，與 _evaluate_hrv() 的判斷基準一致，避免兩套標準漂移。
_SUGGESTION_HRV_DROP_PCT_THRESHOLD = HRV_DROP_PCT_THRESHOLD

# 建議閾值分析至少需要幾段「連續訓練」樣本才視為可信，樣本過少時估算值
# 容易被單一段落主導、不具代表性，回傳「資料不足」而非誤導性建議。
_SUGGESTION_MIN_TRAINING_STREAKS = 3


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
        # 純粹數活動日，不經任何估算，無已知偏誤。
        "confidence": {"level": "high", "biases": []},
    }


def _evaluate_tsb(tsb: float, hrmax_bias_codes: list[str]) -> dict[str, Any] | None:
    if tsb >= TSB_LOW_THRESHOLD:
        return None
    return {
        "dimension": "tsb",
        "detail": f"TSB 為 {tsb:.1f}，低於門檻 {TSB_LOW_THRESHOLD:.1f}（急性疲勞明顯高於慢性負荷）",
        # TSB 由 ATL/CTL 遞推而來，而每日負荷是用 %HRR 算的強度——
        # HRmax 若非實測，整條負荷序列都帶著同一個系統性偏移。
        "confidence": {
            "level": data_confidence.combine_level(hrmax_bias_codes),
            "biases": [data_confidence.describe_bias(c) for c in hrmax_bias_codes],
        },
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
        # 這裡比的是「當日 vs 自己的 7 日均值」，屬短期變化，7 日均值不需要
        # 長歷史即可成立，故標 high；長期個人基準（「你的正常 HRV 是多少」）
        # 才受 292 天樣本期不足所限，那屬另一種用法，見 PLAN.md §7.4。
        "confidence": {"level": "high", "biases": []},
        "detail": (
            f"當日 HRV {hrv_ms:.1f}ms 較 7 日均值 {hrv_weekly_avg_ms:.1f}ms "
            f"下降 {drop_pct * 100:.1f}%，超過門檻 {HRV_DROP_PCT_THRESHOLD * 100:.0f}%"
        ),
    }


def assess_readiness(
    training_load_series: list[dict[str, Any]],
    daily_wellness: list[dict[str, Any]],
    high_risk_consecutive_training_days: int | None = None,
    output_from_date: datetime.date | None = None,
    max_hr_source: str | None = None,
) -> list[dict[str, Any]]:
    """對 training_load_series 涵蓋的每一天，輸出恢復狀態評分/旗標。

    training_load_series：training_load.compute_training_load_series() 的輸出
        （每筆至少含 "date"／"load"／"tsb"）。呼叫端若為了讓 ATL/CTL 有足夠
        暖機期而多算了序列前段（見 training_load_queries.compute_readiness_for_athlete）,
        仍應把完整序列傳進來——本函式用完整序列建立連續訓練天數的查詢表，
        只有「輸出哪幾天的結果」由 output_from_date 控制，避免暖機期天數
        因為查不到前一天的負荷資料而被誤判為連續中斷。

    daily_wellness：每筆至少含 "date"／"hrv_ms"／"hrv_weekly_avg_ms"（其餘
        daily_wellness 欄位本模組目前不使用，但不因額外欄位存在而出錯）。
        某天缺對應紀錄，或紀錄中 hrv_ms/hrv_weekly_avg_ms 為 None，視為該天
        HRV 維度資料缺失，判斷邏輯降級為僅依可用維度判斷。

    high_risk_consecutive_training_days：athlete_profile 的個人化恢復閾值，
        可能為 NULL（None）。為 None 時退回使用
        DEFAULT_HIGH_RISK_CONSECUTIVE_TRAINING_DAYS，並在輸出中標記
        "threshold_source": "default"；否則標記 "threshold_source": "personalized"。

    max_hr_source：athlete_profile.max_hr_source，決定 TSB 維度的信賴度標記
        （負荷以 %HRR 推算，HRmax 非實測則整條 ATL/CTL/TSB 同步偏移）。
        省略時保守視為未實測。

    output_from_date：只輸出這天（含）之後的結果，之前的天數僅用於連續訓練
        天數與 ATL/CTL 暖機計算，不出現在回傳結果中。為 None 時輸出
        training_load_series 的整段日期範圍（既有行為不變）。

    回傳：依日期升冪排序的 list，每筆
        {
            "date": datetime.date,
            "readiness": "low" | "normal",
            "triggers": list[dict],  # 觸發的維度、說明與該維度的信賴度標記
                # （{"dimension", "detail", "confidence": {"level", "biases"}}），
                # 空 list 代表無觸發
            "threshold_source": "default" | "personalized",
            "consecutive_training_days": int,
        }
        涵蓋輸入 training_load_series 的整段日期範圍（或 output_from_date 之後）。
        輸入為空清單時回傳空清單。

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

    # HRmax 來源決定 TSB 這條線的信賴度：負荷是用 %HRR 算的，
    # HRmax 非實測則整條 ATL/CTL/TSB 同步偏移。省略參數時保守視為未實測。
    hrmax_bias_codes = data_confidence.hrmax_bias_codes(max_hr_source)

    daily_loads_by_date = {day["date"]: day["load"] for day in training_load_series}
    wellness_by_date = {day["date"]: day for day in daily_wellness}

    results: list[dict[str, Any]] = []
    for day in training_load_series:
        date = day["date"]
        if output_from_date is not None and date < output_from_date:
            continue
        consecutive_days = _consecutive_training_days_ending_at(daily_loads_by_date, date)
        wellness = wellness_by_date.get(date, {})

        triggers = []
        for trigger in (
            _evaluate_consecutive_training(consecutive_days, threshold),
            _evaluate_tsb(day["tsb"], hrmax_bias_codes),
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


def _training_streaks(training_dates: set[datetime.date]) -> list[tuple[datetime.date, int]]:
    """把訓練日期集合切成連續段，回傳 [(該段最後一天, 該段長度), ...]。

    只回傳每段的「結束日＋長度」，因為 suggest_recovery_threshold 只關心
    「這段連續訓練跑到第幾天時，緊接著出現恢復訊號惡化」。
    """
    streaks: list[tuple[datetime.date, int]] = []
    for date in sorted(training_dates):
        if date - datetime.timedelta(days=1) in training_dates:
            # 延續前一段：把上一筆的長度 +1。
            last_end, last_len = streaks[-1]
            streaks[-1] = (date, last_len + 1)
        else:
            streaks.append((date, 1))
    return streaks


def suggest_recovery_threshold(
    conn: sqlite3.Connection, athlete_id: int
) -> dict[str, Any]:
    """分析該學員的歷史訓練頻率與 HRV 變化，估算建議的個人化恢復閾值。

    只分析、只建議，**明確不寫入 athlete_profile**——結果須由人工確認後，
    呼叫端另外執行獨立的寫入步驟（見 Issue #16 的
    high_risk_consecutive_training_days 欄位）。

    做法：找出所有「連續訓練段」（activities 有紀錄的連續日期），對每段
    掃描其中每一天，檢查「截至當天的連續訓練天數」與「當天 HRV 相對 7 日
    均值是否明顯下降」（門檻同 _evaluate_hrv()），找出連續訓練天數達到多少
    時，開始伴隨 HRV 惡化訊號的比例明顯提高。

    回傳：
        資料足夠時：
            {
                "available": True,
                "suggested_threshold_days": int,
                "basis": {
                    "training_streaks_analyzed": int,
                    "streaks_with_degradation": int,
                    "explanation": str,
                },
            }
        資料不足時（可信連續訓練段樣本數 < _SUGGESTION_MIN_TRAINING_STREAKS）：
            {
                "available": False,
                "reason": str,
            }
    """
    training_dates = {
        datetime.date.fromisoformat(row["d"])
        for row in conn.execute(
            "SELECT DISTINCT date(started_at) AS d FROM activities WHERE athlete_id = ?",
            (athlete_id,),
        )
    }
    hrv_by_date: dict[datetime.date, tuple[float | None, float | None]] = {
        datetime.date.fromisoformat(row["date"]): (row["hrv_ms"], row["hrv_weekly_avg_ms"])
        for row in conn.execute(
            "SELECT date, hrv_ms, hrv_weekly_avg_ms FROM daily_wellness WHERE athlete_id = ?",
            (athlete_id,),
        )
    }

    streaks = _training_streaks(training_dates)
    # 只保留有對應 HRV 資料可判讀的段落，樣本數不足時直接回報資料不足。
    analyzable_streaks = [
        (end_date, length)
        for end_date, length in streaks
        if hrv_by_date.get(end_date, (None, None))[0] is not None
        and hrv_by_date.get(end_date, (None, None))[1] is not None
    ]

    if len(analyzable_streaks) < _SUGGESTION_MIN_TRAINING_STREAKS:
        return {
            "available": False,
            "reason": (
                f"可分析的連續訓練段樣本數僅 {len(analyzable_streaks)} 筆"
                f"（需要至少 {_SUGGESTION_MIN_TRAINING_STREAKS} 筆含對應 HRV 資料的樣本），"
                "資料量不足以支持可信建議"
            ),
        }

    degraded_streaks = []
    for end_date, length in analyzable_streaks:
        hrv_ms, hrv_weekly_avg_ms = hrv_by_date[end_date]
        drop_pct = (hrv_weekly_avg_ms - hrv_ms) / hrv_weekly_avg_ms
        if drop_pct >= _SUGGESTION_HRV_DROP_PCT_THRESHOLD:
            degraded_streaks.append(length)

    if not degraded_streaks:
        return {
            "available": False,
            "reason": (
                f"分析了 {len(analyzable_streaks)} 段連續訓練，皆未觀察到明顯 HRV 惡化訊號，"
                "無法估算有意義的建議閾值"
            ),
        }

    suggested_threshold_days = min(degraded_streaks)

    return {
        "available": True,
        "suggested_threshold_days": suggested_threshold_days,
        "basis": {
            "training_streaks_analyzed": len(analyzable_streaks),
            "streaks_with_degradation": len(degraded_streaks),
            "explanation": (
                f"分析了 {len(analyzable_streaks)} 段含 HRV 資料的連續訓練段，"
                f"其中 {len(degraded_streaks)} 段在結束當天觀察到 HRV 較 7 日均值下降 "
                f"{_SUGGESTION_HRV_DROP_PCT_THRESHOLD * 100:.0f}% 以上；"
                f"觀察到惡化訊號的最短連續訓練天數為 {suggested_threshold_days} 天"
            ),
        },
    }
