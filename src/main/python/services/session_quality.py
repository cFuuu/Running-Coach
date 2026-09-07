"""單場訓練的品質評估：把一場活動的原始數據轉成可解讀的判讀維度。

分層原則比照 vdot_engine.py／readiness.py：**本模組不碰資料庫**，
吃已查好的活動 dict 與參數，回傳判讀結果，方便單元測試。

範圍（2026-09-01 定案）：只評「這場練得如何」的**生理執行面**，
不做課表對照、不算遵從度——使用者選擇讓 AI 只分析實際練的內容，
不拿計畫課表比對（遵從度屬獨立的行為指標，見 PLAN.md §1）。

三個維度：
  1. 強度符合度（intensity_fit）：實際心率強度是否落在該課種應有的區間
  2. 配速穩定度（pace_consistency）：前後半配速差異，反映節奏控制
  3. 心率漂移（hr_drift）：透傳 fit_parser 的計算結果

**每個維度都帶信賴度標記**（PLAN.md §7.3 三欄位契約），且：
  - `workout_type` 為 unknown/NULL 時，需要課種前提的維度直接不評
  - 無法標記信賴度的維度回傳 None，不給裸數字

⚠️ 本模組**不輸出總分**。三個維度分開呈現，由判斷層（AI）權衡——
合成單一分數等於把判斷偷渡進事實層（PLAN.md §1「遵從度自訓練品質
分數中拆出」同一理由）。
"""

from __future__ import annotations

from typing import Any

from . import data_confidence

# --- 可調閾值（集中管理）---

# 各課種預期的心率強度區間（%HRR）。
# 🔴 工程實務決策：Daniels 的區間定義在**配速**上，而配速受地形、天候、
# 交通號誌影響大；改用 %HRR 是為了讓低量跑者的日常訓練也能判讀。
#
# easy／lsd 的邊界（2026-09-07 起）直接對齊學員手錶上實際設定的 Garmin
# Z2（Based On %HRR，59–74%），讓 App 的判讀與他跑步當下看到的區間一致；
# 兩邊不同步時，學員會拿到互相矛盾的兩個結論。
#
# ⚠️ tempo／interval／race **刻意不對齊**手錶的 Z3/Z4/Z5。本模組判斷的是
# **整場平均**心率（見 assess_intensity_fit 用 avg_hr_bpm），而這幾種課含
# 熱身與緩和，整場平均必然低於主課段強度；照搬 Z4 的 83–88% 當 tempo 門檻，
# 會讓幾乎每場 tempo 都被誤判成「強度不足」。正解是改抓主課段而非平均，
# 見 TODO.md N-14。在那之前這幾個維持原本的自訂寬區間。
#
# 邊界非直接引用單一文獻，未來應以本學員自己的資料校正。見
# docs/research/physiological-thresholds.md 的立場：查不到出處就標明是自訂，
# 不假託文獻。
INTENSITY_RANGES_HRR: dict[str, tuple[float, float]] = {
    "easy": (0.59, 0.74),
    "lsd": (0.59, 0.74),
    "tempo": (0.75, 0.88),
    "interval": (0.85, 0.95),
    "race": (0.85, 1.00),
}

# 配速前後半差異超過此比例，視為節奏控制不穩（正值代表後半變慢）。
# 🔴 工程實務決策，無文獻依據——文獻不存在「配速偏差多少算沒執行」的定義。
PACE_FADE_PCT_THRESHOLD = 0.05

# 配速穩定度與心率漂移都需要足夠長度才有意義（指標假設已進入穩態）。
MIN_DURATION_SEC_FOR_PACING = 20 * 60


def _hrr_pct(avg_hr: float, max_hr: float, resting_hr: float) -> float | None:
    """心率儲備百分比 %HRR = (HR − RHR) / (HRmax − RHR)。"""
    reserve = max_hr - resting_hr
    if reserve <= 0:
        return None
    return (avg_hr - resting_hr) / reserve


def assess_intensity_fit(
    activity: dict[str, Any],
    max_hr_bpm: float | None,
    resting_hr_bpm: float | None,
    max_hr_source: str | None = None,
) -> dict[str, Any] | None:
    """判斷實際心率強度是否落在該課種應有的區間。

    課種未知、缺心率、或缺 HRmax/RHR 時回傳 None（不評，而非猜）。
    """
    workout_type = activity.get("workout_type")
    if data_confidence.workout_type_bias_codes(workout_type):
        return None
    expected = INTENSITY_RANGES_HRR.get(workout_type)
    if expected is None:
        return None

    avg_hr = activity.get("avg_hr_bpm")
    if not avg_hr or not max_hr_bpm or not resting_hr_bpm:
        return None
    actual = _hrr_pct(avg_hr, max_hr_bpm, resting_hr_bpm)
    if actual is None:
        return None

    low, high = expected
    if actual < low:
        verdict, detail = "below", f"低於 {workout_type} 課預期的強度下限"
    elif actual > high:
        verdict, detail = "above", f"高於 {workout_type} 課預期的強度上限"
    else:
        verdict, detail = "within", f"落在 {workout_type} 課預期的強度區間內"

    # HRmax 偏誤 + 區間本身為自訂，兩者都要讓判斷層看見。
    bias_codes = [*data_confidence.hrmax_bias_codes(max_hr_source), "intensity_range_custom"]
    return {
        "dimension": "intensity_fit",
        "verdict": verdict,
        "actual_hrr_pct": round(actual * 100, 1),
        "expected_range_pct": [round(low * 100), round(high * 100)],
        "detail": (
            f"{detail}：實際 {actual * 100:.1f}% HRR，"
            f"預期 {low * 100:.0f}–{high * 100:.0f}%"
        ),
        "confidence": {
            "level": data_confidence.combine_level(bias_codes),
            "biases": [data_confidence.describe_bias(c) for c in bias_codes],
        },
    }


def assess_pace_consistency(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """比較前後半的平均配速，判斷節奏是否穩定。

    逐秒資料不足或時長過短時回傳 None——短時間活動尚未進入穩態，
    比較前後半沒有意義。
    """
    points = [
        r
        for r in records
        if r.get("pace_sec_per_km") and r.get("elapsed_sec") is not None
    ]
    if len(points) < 2:
        return None

    duration = points[-1]["elapsed_sec"] - points[0]["elapsed_sec"]
    if duration < MIN_DURATION_SEC_FOR_PACING:
        return None

    mid = (points[0]["elapsed_sec"] + points[-1]["elapsed_sec"]) / 2
    first = [r["pace_sec_per_km"] for r in points if r["elapsed_sec"] <= mid]
    second = [r["pace_sec_per_km"] for r in points if r["elapsed_sec"] > mid]
    if not first or not second:
        return None

    first_avg = sum(first) / len(first)
    second_avg = sum(second) / len(second)
    # 配速數字越大越慢，故正值代表後半變慢（fade）。
    fade_pct = (second_avg - first_avg) / first_avg

    if fade_pct > PACE_FADE_PCT_THRESHOLD:
        verdict = "faded"
        detail = f"後半配速較前半慢 {fade_pct * 100:.1f}%"
    elif fade_pct < -PACE_FADE_PCT_THRESHOLD:
        verdict = "negative_split"
        detail = f"後半配速較前半快 {abs(fade_pct) * 100:.1f}%（負分割）"
    else:
        verdict = "even"
        detail = f"前後半配速差異 {abs(fade_pct) * 100:.1f}%，節奏平穩"

    return {
        "dimension": "pace_consistency",
        "verdict": verdict,
        "fade_pct": round(fade_pct * 100, 1),
        "first_half_pace_sec_per_km": round(first_avg),
        "second_half_pace_sec_per_km": round(second_avg),
        "detail": detail,
        "confidence": {
            "level": "low",
            "biases": [data_confidence.describe_bias("pace_fade_threshold_custom")],
        },
    }


def assess_session(
    activity: dict[str, Any],
    records: list[dict[str, Any]],
    hr_drift: dict[str, Any] | None,
    max_hr_bpm: float | None,
    resting_hr_bpm: float | None,
    max_hr_source: str | None = None,
) -> dict[str, Any]:
    """評估單場訓練，回傳各維度的判讀結果。

    回傳：
        {
            "dimensions": list[dict],   # 可評的維度，每筆帶 confidence
            "not_assessed": list[dict], # 不可評的維度與原因
        }

    **不輸出總分**——三個維度分開呈現，權衡交給判斷層。
    """
    dimensions: list[dict[str, Any]] = []
    not_assessed: list[dict[str, Any]] = []

    intensity = assess_intensity_fit(activity, max_hr_bpm, resting_hr_bpm, max_hr_source)
    if intensity is not None:
        dimensions.append(intensity)
    else:
        codes = data_confidence.workout_type_bias_codes(activity.get("workout_type"))
        reason = (
            data_confidence.describe_bias(codes[0])["detail"]
            if codes
            else "缺少心率、HRmax 或靜止心率，無法計算心率強度"
        )
        not_assessed.append({"dimension": "intensity_fit", "reason": reason})

    pacing = assess_pace_consistency(records)
    if pacing is not None:
        dimensions.append(pacing)
    else:
        not_assessed.append(
            {
                "dimension": "pace_consistency",
                "reason": (
                    f"逐秒配速資料不足，或活動短於 "
                    f"{MIN_DURATION_SEC_FOR_PACING // 60} 分鐘（尚未進入穩態）"
                ),
            }
        )

    if hr_drift and hr_drift.get("available"):
        dimensions.append(
            {
                "dimension": "hr_drift",
                "drift_pct": hr_drift["drift_pct"],
                "first_half_avg_hr": hr_drift["first_half_avg_hr"],
                "second_half_avg_hr": hr_drift["second_half_avg_hr"],
                "detail": (
                    f"前半平均心率 {hr_drift['first_half_avg_hr']:.0f}、"
                    f"後半 {hr_drift['second_half_avg_hr']:.0f}，"
                    f"漂移 {hr_drift['drift_pct']:+.1f}%"
                ),
                # 刻意不給 verdict：本專案算的是純心率差而非 Pa:HR，
                # 沒有可用的異常閾值（見 fit_parser.compute_hr_drift 說明）。
                "confidence": {
                    "level": "low",
                    "biases": [data_confidence.describe_bias("hr_drift_no_threshold")],
                },
            }
        )
    else:
        not_assessed.append(
            {
                "dimension": "hr_drift",
                "reason": (hr_drift or {}).get("reason", "無逐秒心率資料"),
            }
        )

    return {"dimensions": dimensions, "not_assessed": not_assessed}
