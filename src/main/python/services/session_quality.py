"""單場訓練的品質評估：把一場活動的原始數據轉成可解讀的判讀維度。

分層原則比照 vdot_engine.py／readiness.py：**本模組不碰資料庫**，
吃已查好的活動 dict 與參數，回傳判讀結果，方便單元測試。

範圍（2026-09-01 定案）：只評「這場練得如何」的**生理執行面**，
不做課表對照、不算遵從度——使用者選擇讓 AI 只分析實際練的內容，
不拿計畫課表比對（遵從度屬獨立的行為指標，見 PLAN.md §1）。

四個維度：
  1. 強度符合度（intensity_fit）：實際心率強度是否落在該課種應有的區間
  2. 配速執行度（pace_execution）：整場平均配速對該課種的 VDOT 配速區間；
     間歇課改為分段逐趟標示落在哪一區（N-24）
  3. 配速穩定度（pace_consistency）：前後半配速差異，反映節奏控制
  4. 心率漂移（hr_drift）：透傳 fit_parser 的計算結果

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
from .workout_classifier import segment_laps, work_segments

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
    "interval": (0.85, 0.95),  # assess_session 對間歇已改看逐趟配速、不用此區間（N-24）
    "race": (0.85, 1.00),
}

# 配速前後半差異超過此比例，視為節奏控制不穩（正值代表後半變慢）。
# 🔴 工程實務決策，無文獻依據——文獻不存在「配速偏差多少算沒執行」的定義。
PACE_FADE_PCT_THRESHOLD = 0.05

# 以「整場平均配速」評配速執行度的課種。
# ⚠️ 這是**資料模型限制，不是遺漏**：easy/lsd 全程都在同一個強度帶，整場平均
# 配速就代表該課的目標配速；tempo/interval 的整場平均含暖身、緩和與間歇之間的
# 休息段，會系統性地看起來「偏慢」——那是課的結構，不是沒執行。
# interval 自 2026-10-23（N-24）起改由分段逐趟評（assess_interval_pace）；
# tempo 仍缺「主段」概念（見 TODO N-10／N-14），維持不評。
# 缺前提時不評，與 workout_type 為 unknown 時不評是同一條原則。
PACE_EXECUTION_ASSESSABLE_TYPES = frozenset({"easy", "lsd"})

# 由快到慢的 VDOT 區間，供間歇逐趟標示落在哪一區
_INTERVAL_ZONE_ORDER = ("interval", "tempo", "marathon", "easy")
_ZONE_SHORT_NAMES = {"interval": "I", "tempo": "T", "marathon": "M", "easy": "E"}

# 間歇課的強度不看心率（N-24，2026-10-23 Fu 決定）：Daniels 的間歇以配速處方，
# 而 1–5 分鐘的反覆心率還在爬升，整場或各趟平均心率都會低估強度——
# 2026-10-22 那場 4 趟 1000m 配速落在 T～I 之間，各趟平均心率卻只有 66–77% HRR。
_INTERVAL_INTENSITY_NOT_ASSESSED_REASON = (
    "間歇課的強度改看各趟配速（見 pace_execution）：短反覆時心率還在爬升，"
    "整場或各趟平均心率都會低估實際強度，故不以心率判定；各趟心率只在 "
    "pace_execution 中列出數值"
)

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


def _format_pace(sec_per_km: float) -> str:
    """把秒/公里格式化成 m:ss，供給判斷層閱讀的說明字串使用。"""
    minutes, seconds = divmod(round(sec_per_km), 60)
    return f"{minutes}:{seconds:02d}/km"


def assess_pace_execution(
    activity: dict[str, Any], pace_zones: dict[str, Any] | None
) -> dict[str, Any] | None:
    """比對整場平均配速與該課種的目標配速區間。

    ⚠️ 比較基準是 **VDOT 推算的配速區間**，不是 `training_plan` 的課表——
    本模組維持「只分析實際練的內容、不算遵從度」的範圍（2026-09-01 決定）。
    問的是「這場 easy 跑得像不像 easy」，不是「有沒有照課表跑」。

    只評 PACE_EXECUTION_ASSESSABLE_TYPES 內的課種；其餘回傳 None。

    **只有偏快才是需要提示的方向**（承 N-3 查證）：低強度日跑太快會讓這天
    既沒有恢復效果、也構不成品質課刺激，是 80/20 分配最常見的失效模式；
    跑得比區間慢則不視為未執行，僅回報數值。
    """
    workout_type = activity.get("workout_type")
    if workout_type not in PACE_EXECUTION_ASSESSABLE_TYPES:
        return None
    zone = (pace_zones or {}).get(workout_type)
    if not zone:
        return None
    actual = activity.get("avg_pace_sec_per_km")
    if not actual:
        return None

    fast = zone["fast_sec_per_km"]
    slow = zone["slow_sec_per_km"]
    # 配速數字越大越慢：小於快端＝跑得比區間更快。
    if actual < fast:
        verdict = "too_fast"
        deviation_pct = (actual - fast) / fast * 100
        detail = (
            f"整場平均 {_format_pace(actual)}，比 {workout_type} 區間的快端 "
            f"{_format_pace(fast)} 再快 {abs(deviation_pct):.1f}%——"
            "低強度日跑太快，這天既達不到恢復效果，也構不成品質課的刺激"
        )
    elif actual > slow:
        verdict = "slower_than_zone"
        deviation_pct = (actual - slow) / slow * 100
        detail = (
            f"整場平均 {_format_pace(actual)}，比 {workout_type} 區間的慢端 "
            f"{_format_pace(slow)} 再慢 {deviation_pct:.1f}%。"
            "低強度日跑得更慢**不視為未執行**，此處僅回報數值"
        )
    else:
        verdict = "within"
        deviation_pct = 0.0
        detail = (
            f"整場平均 {_format_pace(actual)}，落在 {workout_type} 區間 "
            f"{_format_pace(fast)}–{_format_pace(slow)} 內"
        )

    return {
        "dimension": "pace_execution",
        "verdict": verdict,
        "actual_pace_sec_per_km": round(actual),
        "target_range_sec_per_km": [round(fast), round(slow)],
        "deviation_pct": round(deviation_pct, 1),
        "detail": detail,
        "confidence": {
            "level": data_confidence.combine_level(["pace_execution_zone_estimated"]),
            "biases": [data_confidence.describe_bias("pace_execution_zone_estimated")],
        },
    }


def _pace_zone_label(pace: float, pace_zones: dict[str, Any]) -> str | None:
    """標示配速落在哪個 VDOT 區間，或落在哪兩區之間（Daniels 區間之間有空隙）。"""
    faster_neighbour = None
    for name in _INTERVAL_ZONE_ORDER:
        zone = pace_zones.get(name)
        if not zone:
            continue
        if pace < zone["fast_sec_per_km"]:
            if faster_neighbour is None:
                return f"faster_than_{name}"
            return f"between_{faster_neighbour}_and_{name}"
        if pace <= zone["slow_sec_per_km"]:
            return name
        faster_neighbour = name
    return f"slower_than_{faster_neighbour}" if faster_neighbour else None


def _describe_zone_label(label: str) -> str:
    """把區間標籤轉成給判斷層讀的短說明，例如 between_interval_and_tempo → T 與 I 之間。"""
    names = _ZONE_SHORT_NAMES
    if label.startswith("faster_than_"):
        return f"快於 {names[label.removeprefix('faster_than_')]} 區"
    if label.startswith("slower_than_"):
        return f"慢於 {names[label.removeprefix('slower_than_')]} 區"
    if label.startswith("between_"):
        faster, slower = label.removeprefix("between_").split("_and_")
        return f"{names[slower]} 與 {names[faster]} 之間"
    return f"{names[label]} 區"


def assess_interval_pace(
    laps: list[dict[str, Any]],
    pace_zones: dict[str, Any] | None,
    max_hr_bpm: float | None = None,
    resting_hr_bpm: float | None = None,
) -> dict[str, Any] | None:
    """間歇課逐趟評配速：每一趟 work 落在哪個 VDOT 區間（N-24）。

    分圈先合併成段（`workout_classifier.segment_laps`），所以錶設 400m 自動分圈
    時，一趟 1000m 會被算成一趟而不是三圈。**不下「達標／未達標」判定**——
    課表要的是 T 還是 I 只有教練知道（本模組不比對課表，2026-09-01 決定），
    只回報各趟落在哪一區，由判斷層對照。各趟心率只列數值不判讀。

    切不出 ≥2 趟 work 段、或沒有 VDOT 區間時回傳 None。
    """
    zones = {
        name: pace_zones[name]
        for name in _INTERVAL_ZONE_ORDER
        if (pace_zones or {}).get(name)
    }
    if not zones:
        return None
    segments = segment_laps(laps)
    works = work_segments(segments)
    if len(works) < 2:
        return None

    first_work = segments.index(works[0])
    last_work = segments.index(works[-1])
    recoveries = [
        s for s in segments[first_work:last_work + 1] if s["role"] == "recovery"
    ]

    reps = []
    for number, seg in enumerate(works, start=1):
        hr = seg["avg_hr_bpm"]
        hrr = (
            _hrr_pct(hr, max_hr_bpm, resting_hr_bpm)
            if hr and max_hr_bpm and resting_hr_bpm else None
        )
        reps.append({
            "rep": number,
            "distance_m": round(seg["distance_km"] * 1000),
            "duration_sec": round(seg["duration_sec"]),
            "pace_sec_per_km": round(seg["pace_sec_per_km"]),
            "zone": _pace_zone_label(seg["pace_sec_per_km"], zones),
            "avg_hr_bpm": round(hr) if hr else None,
            "hrr_pct": round(hrr * 100, 1) if hrr is not None else None,
        })

    zone_counts: dict[str, int] = {}
    for rep in reps:
        zone_counts[rep["zone"]] = zone_counts.get(rep["zone"], 0) + 1
    rep_text = "、".join(
        f"{rep['distance_m']}m {_format_pace(rep['pace_sec_per_km'])}"
        f"（{_describe_zone_label(rep['zone'])}）"
        for rep in reps
    )
    recovery_text = (
        f"；組間休息 {min(round(r['duration_sec']) for r in recoveries)}–"
        f"{max(round(r['duration_sec']) for r in recoveries)} 秒"
        if recoveries else ""
    )
    zone_range_text = "、".join(
        f"{_ZONE_SHORT_NAMES[name]} {_format_pace(z['fast_sec_per_km'])}–"
        f"{_format_pace(z['slow_sec_per_km'])}"
        for name, z in zones.items()
    )

    bias_codes = ["pace_execution_zone_estimated", "interval_segments_from_laps"]
    return {
        "dimension": "pace_execution",
        "workout_structure": "interval",
        "reps": reps,
        "zone_counts": zone_counts,
        "recoveries_sec": [round(r["duration_sec"]) for r in recoveries],
        "zone_ranges_sec_per_km": {
            name: [round(z["fast_sec_per_km"]), round(z["slow_sec_per_km"])]
            for name, z in zones.items()
        },
        "detail": (
            f"{len(reps)} 趟：{rep_text}{recovery_text}。當日 VDOT 區間：{zone_range_text}。"
            "只標示各趟落在哪一區，不判定達標與否"
        ),
        "confidence": {
            "level": data_confidence.combine_level(bias_codes),
            "biases": [data_confidence.describe_bias(c) for c in bias_codes],
        },
    }


def _pace_execution_reason(
    activity: dict[str, Any], pace_zones: dict[str, Any] | None
) -> str:
    """assess_pace_execution() 回傳 None 時，說明為何不可評。"""
    workout_type = activity.get("workout_type")
    codes = data_confidence.workout_type_bias_codes(workout_type)
    if codes:
        return data_confidence.describe_bias(codes[0])["detail"]
    if workout_type == "interval":
        if not pace_zones:
            return "無 VDOT 推算的配速區間可作比較基準（需有可推算 VDOT 的歷史成績）"
        return (
            "分圈切不出 2 趟以上的快段（缺分圈資料，或分圈看不出快慢交替），"
            "無法逐趟評配速"
        )
    if workout_type not in PACE_EXECUTION_ASSESSABLE_TYPES:
        return (
            f"{workout_type} 不在可評的課種內（本維度僅評 easy／lsd）："
            "其餘課種的整場平均配速代表不了該課的目標配速——tempo/interval "
            "含暖身、緩和與間歇之間的休息段，會系統性地看起來偏慢。"
            "要評得先能標出「主段」，本專案尚未建模；這是資料模型的限制，"
            "不是這場資料有缺"
        )
    if not (pace_zones or {}).get(workout_type):
        return "無 VDOT 推算的配速區間可作比較基準（需有可推算 VDOT 的歷史成績）"
    return "該場活動無平均配速資料"


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
    pace_zones: dict[str, Any] | None = None,
    laps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """評估單場訓練，回傳各維度的判讀結果。

    pace_zones：vdot_engine.compute_pace_zones() 的輸出，供配速執行度維度
        作為比較基準。未提供時該維度列為不可評（不猜、不用預設區間）。
    laps：該場分圈（`activity_laps` 列）。間歇課逐趟評配速時需要；
        間歇課的強度符合度不以心率判定（見 _INTERVAL_INTENSITY_NOT_ASSESSED_REASON）。

    回傳：
        {
            "dimensions": list[dict],   # 可評的維度，每筆帶 confidence
            "not_assessed": list[dict], # 不可評的維度與原因
        }

    **不輸出總分**——三個維度分開呈現，權衡交給判斷層。
    """
    dimensions: list[dict[str, Any]] = []
    not_assessed: list[dict[str, Any]] = []

    is_interval = activity.get("workout_type") == "interval"
    intensity = (
        None if is_interval
        else assess_intensity_fit(activity, max_hr_bpm, resting_hr_bpm, max_hr_source)
    )
    if intensity is not None:
        dimensions.append(intensity)
    elif is_interval:
        not_assessed.append(
            {"dimension": "intensity_fit", "reason": _INTERVAL_INTENSITY_NOT_ASSESSED_REASON}
        )
    else:
        codes = data_confidence.workout_type_bias_codes(activity.get("workout_type"))
        reason = (
            data_confidence.describe_bias(codes[0])["detail"]
            if codes
            else "缺少心率、HRmax 或靜止心率，無法計算心率強度"
        )
        not_assessed.append({"dimension": "intensity_fit", "reason": reason})

    pace_execution = (
        assess_interval_pace(laps or [], pace_zones, max_hr_bpm, resting_hr_bpm)
        if is_interval
        else assess_pace_execution(activity, pace_zones)
    )
    if pace_execution is not None:
        dimensions.append(pace_execution)
    else:
        not_assessed.append(
            {
                "dimension": "pace_execution",
                "reason": _pace_execution_reason(activity, pace_zones),
            }
        )

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
        bias_codes = ["hr_drift_no_threshold"]
        if hr_drift["tier"] == "reference":
            bias_codes.append("hr_drift_reference_tier")
        dimensions.append(
            {
                "dimension": "hr_drift",
                "drift_pct": hr_drift["drift_pct"],
                "decoupling_pct": hr_drift["decoupling_pct"],
                "first_half_avg_hr": hr_drift["first_half_avg_hr"],
                "second_half_avg_hr": hr_drift["second_half_avg_hr"],
                "tier": hr_drift["tier"],
                "tier_reasons": hr_drift["tier_reasons"],
                "detail": (
                    f"暖身後 {hr_drift['steady_moving_min']:.0f} 分鐘穩定段："
                    f"前半平均心率 {hr_drift['first_half_avg_hr']:.0f}、"
                    f"後半 {hr_drift['second_half_avg_hr']:.0f}（心率漂移 "
                    f"{hr_drift['drift_pct']:+.1f}%），"
                    f"Pa:HR decoupling {hr_drift['decoupling_pct']:+.1f}%"
                ),
                # 刻意不給 verdict：Friel 的 5% 是教練經驗法則、非同儕審查，
                # 見 fit_parser.compute_hr_drift 說明。
                "confidence": {
                    "level": data_confidence.combine_level(bias_codes),
                    "biases": [data_confidence.describe_bias(c) for c in bias_codes],
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
