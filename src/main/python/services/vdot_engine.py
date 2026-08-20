"""從活動歷史挑出最適合作為 VDOT／配速推算基準的候選成績。

本模組刻意不碰資料庫：吃候選活動清單（純 dict/list），回傳結構化的挑選結果，
方便單元測試。資料庫查詢（拉活動、拉 HRmax）交給上層 orchestrator 負責。
（與 fit_parser.py / workout_classifier.py 的分層方式一致。）

為什麼需要雙軌新鮮度門檻——2026-08-18 grill 討論結論：
「新鮮度」與「距離代表性」是兩種不同性質的判斷，不應合併成單一加權分數。
新鮮度回答「這筆資料還準不準」，是門檻式判斷（超過門檻直接排除，不參與加權）；
距離代表性回答「這筆資料多能代表全馬能力」，只在通過新鮮度門檻的候選之間排序取捨。
長距離成績（半馬/全馬）本身就是耐力能力的直接證據，即使數月未更新仍有參考價值，
因此適用比短距離成績更寬鬆的新鮮度門檻，而非統一的 90 天門檻。

心率強度換算等效全力配速——2026-08-18 grill 討論結論：
非全力程度的活動（如 easy run）不該因為配速慢就直接被排除，也不該直接套用
Daniels 公式（會嚴重低估真實能力）。做法是先用該次活動的平均心率相對 HRmax
的百分比，換算出「若當時全力跑會是什麼配速」的等效值，換算後的候選仍可用，
但信賴度標記較低（非真實測驗/比賽成績）。這是本引擎自訂的換算公式，非標準
Daniels 公式的一部分，假設與依據見 estimate_max_effort_pace() 的說明。
完全沒有心率資料的活動無法換算，維持排除。

VDOT／配速輸出——套用 Daniels VDOT 公式（Daniels & Gilbert, "Oxygen Power"）與
Riegel 跨距離推算公式（指數 1.06）：先用 Riegel 公式把選中的基準成績換算到全馬
距離的等效表現，再用 Daniels 的 VO2/%VO2max 迴歸公式反推 VDOT，最後依 VDOT
對照 Daniels 訓練配速公式產出 Easy/Marathon/Tempo/Interval 各課種的配速區間。
LSD 配速沿用 Easy 配速區間的下界（Daniels 對 LSD 沒有獨立公式，訓練上等同於
較長的 Easy run）。
"""

from __future__ import annotations

import datetime
from typing import Any

# --- 可調閾值（集中管理，勿散落於邏輯中）---

# 短距離候選（10K 以下）的新鮮度門檻：超過即直接排除，不參與後續任何計算。
SHORT_DISTANCE_FRESHNESS_DAYS = 90

# 長距離候選（半馬/全馬）的新鮮度門檻：比短距離門檻寬鬆許多，因為長距離成績
# 本身就是耐力能力的直接證據。取 6 個月的下界值（180 天）——grill 討論的
# 範圍是 6–12 個月，180 天是其中較保守（較嚴格）的一端。
LONG_DISTANCE_FRESHNESS_DAYS = 180

# 通過短距離門檻但超過此值（僅適用短距離）以外，不再有第二層短距離門檻；
# 這個常數只是註記語意邊界，供未來若要拆分「更短」與「短」距離時參考。

# 距離代表性排序表：數字越小代表性越高，優先被選中。
# 對應 PLAN.md §5.5：全馬/半馬 > 10K > 5K > 短間歇。
_DISTANCE_RANK: dict[str, int] = {
    "marathon": 0,
    "half_marathon": 1,
    "10k": 2,
    "5k": 3,
    "short_interval": 4,
}

# marathon / half_marathon 屬於「長距離」候選，套用寬鬆新鮮度門檻；
# 其餘（10k/5k/short_interval）屬於「短距離」候選，套用嚴格門檻。
_LONG_DISTANCE_CATEGORIES = frozenset({"marathon", "half_marathon"})

# 平均心率低於 HRmax 的這個百分比時，視為「非全力程度」，需要換算才能使用。
# 一般認為馬拉松配速以上強度的平均心率約落在 88~92% HRmax 以上；取 85% 作為
# 保守的分界——高於此值視為已接近全力，不需要換算即可直接使用。
MAX_EFFORT_HR_PCT_THRESHOLD = 0.85

# 自訂換算公式的係數：低於全力強度時，每少 1 個百分點的 %HRmax，配速線性
# 放慢對應係數。此為本引擎自訂的簡化假設（非標準 Daniels/Riegel 公式），
# 依據是配速與攝氧量在次最大強度區間內大致呈線性關係的常見訓練經驗法則；
# 未來若累積足夠真實資料應重新校正。
_HR_PACE_ADJUSTMENT_FACTOR = 1.0

# Riegel 公式的距離代表性指數：T2 = T1 * (D2/D1)^RIEGEL_EXPONENT。
# PLAN.md §5.5 定案值。
RIEGEL_EXPONENT = 1.06

# 各距離區間對應的公里數，供 Riegel 跨距離推算使用。
_DISTANCE_KM: dict[str, float] = {
    "marathon": 42.195,
    "half_marathon": 21.0975,
    "10k": 10.0,
    "5k": 5.0,
    # 短間歇沒有統一距離，Riegel 推算不適用短間歇（見 project_to_marathon 的檢查）。
}

_MARATHON_KM = _DISTANCE_KM["marathon"]

# 課種配速區間相對 VDOT 對應全馬配速（Marathon Pace）的比例係數，取自
# Daniels 訓練配速表的常見經驗區間（非逐一查表，屬簡化線性近似）：
# 數值代表「該課種配速 = marathon_pace_sec_per_km * 係數」，係數 < 1 代表更快。
# 每個課種給一組 (下界係數, 上界係數) 區間。
_PACE_ZONE_FACTORS: dict[str, tuple[float, float]] = {
    "easy": (1.14, 1.24),
    "marathon": (0.98, 1.02),
    "tempo": (0.90, 0.94),
    "interval": (0.82, 0.87),
}


def _freshness_days(reference_date: datetime.date, activity_date: datetime.date) -> int:
    return (reference_date - activity_date).days


def _passes_freshness_gate(
    category: str,
    age_days: int,
) -> tuple[bool, bool]:
    """回傳 (是否通過門檻, 是否為降級信賴度)。

    降級信賴度 = 長距離候選超過短距離門檻、但仍在長距離門檻內的情況——
    這類候選可用，但信賴度應標記較低。
    """
    if category in _LONG_DISTANCE_CATEGORIES:
        if age_days <= SHORT_DISTANCE_FRESHNESS_DAYS:
            return True, False
        if age_days <= LONG_DISTANCE_FRESHNESS_DAYS:
            return True, True
        return False, False
    # 短距離候選只有一道門檻，沒有降級信賴度的中間地帶。
    return age_days <= SHORT_DISTANCE_FRESHNESS_DAYS, False


def estimate_max_effort_pace(
    pace_sec_per_km: float,
    avg_hr_bpm: float,
    max_hr_bpm: float,
) -> float:
    """依心率強度將次全力配速換算成等效全力配速（秒/公里）。

    自訂簡化公式（非標準 Daniels/Riegel 方法，需在未來累積真實資料後重新校正）：
    配速變慢幅度與強度不足幅度（1 - %HRmax）成正比。%HRmax 越低，代表跑者當時
    保留的力氣越多，實際「全力」配速應比記錄的配速更快。

        equivalent_pace = pace / (1 - _HR_PACE_ADJUSTMENT_FACTOR * (1 - hr_pct))

    hr_pct 為 1.0（達到 HRmax）時換算結果等於原配速；hr_pct 越低，等效配速
    比原配速快得越多。
    """
    hr_pct = avg_hr_bpm / max_hr_bpm
    hr_pct = min(hr_pct, 1.0)
    denominator = 1 - _HR_PACE_ADJUSTMENT_FACTOR * (1 - hr_pct)
    return pace_sec_per_km * denominator


def _resolve_effort(
    activity: dict[str, Any],
    max_hr_bpm: float | None,
) -> tuple[bool, float | None, bool]:
    """判斷這筆活動能否成為候選，並在需要換算時算出等效全力配速。

    回傳 (是否可用作候選, 換算後配速或原配速, 是否為換算而降級信賴度)。
    """
    if activity.get("is_max_effort", False):
        return True, activity.get("pace_sec_per_km"), False

    avg_hr_bpm = activity.get("avg_hr_bpm")
    pace = activity.get("pace_sec_per_km")
    if not avg_hr_bpm or not pace or not max_hr_bpm:
        # 沒有心率資料（或呼叫端未提供 HRmax）就無法換算，維持排除。
        return False, None, False

    hr_pct = avg_hr_bpm / max_hr_bpm
    if hr_pct >= MAX_EFFORT_HR_PCT_THRESHOLD:
        # 強度已接近全力，直接視為可用候選，不需要換算也不降級信賴度。
        return True, pace, False

    equivalent_pace = estimate_max_effort_pace(pace, avg_hr_bpm, max_hr_bpm)
    return True, equivalent_pace, True


def project_to_marathon_time_sec(
    distance_category: str,
    pace_sec_per_km: float,
) -> float:
    """用 Riegel 公式將任一距離的配速，推算至全馬距離的等效完賽時間（秒）。

    T2 = T1 * (D2/D1)^RIEGEL_EXPONENT，D1/D2 為距離（公里）、T1/T2 為完賽時間。
    """
    distance_km = _DISTANCE_KM.get(distance_category)
    if distance_km is None:
        raise ValueError(
            f"'{distance_category}' 沒有明確的推算距離（如短間歇），無法套用 Riegel 公式"
        )
    base_time_sec = pace_sec_per_km * distance_km
    return base_time_sec * (_MARATHON_KM / distance_km) ** RIEGEL_EXPONENT


def estimate_vdot(marathon_time_sec: float) -> float:
    """依全馬完賽時間（秒），用 Daniels VO2/%VO2max 迴歸公式反推 VDOT。

    採用 Daniels & Gilbert（"Oxygen Power"）給出的兩條迴歸公式：
        VO2 = -4.60 + 0.182258 * v + 0.000104 * v^2   （v：公尺/分鐘配速）
        %VO2max = 0.8 + 0.1894393 * e^(-0.012778*t) + 0.2989558 * e^(-0.1932605*t)
                                                                    （t：分鐘）
        VDOT = VO2 / %VO2max
    """
    time_min = marathon_time_sec / 60.0
    velocity_m_per_min = (_MARATHON_KM * 1000) / time_min

    vo2 = -4.60 + 0.182258 * velocity_m_per_min + 0.000104 * velocity_m_per_min**2
    pct_vo2max = (
        0.8
        + 0.1894393 * pow(2.718281828459045, -0.012778 * time_min)
        + 0.2989558 * pow(2.718281828459045, -0.1932605 * time_min)
    )
    return vo2 / pct_vo2max


def compute_pace_zones(vdot: float) -> dict[str, dict[str, float]]:
    """依 VDOT 值產出各課種的配速區間（秒/公里）。

    先算出該 VDOT 對應的全馬配速（Marathon Pace，秒/公里），再依
    _PACE_ZONE_FACTORS 的比例係數推導各課種區間。LSD 沿用 Easy 區間下界
    （Daniels 對 LSD 沒有獨立公式，訓練上等同於較長的 Easy run）。
    """
    marathon_time_sec = _vdot_to_marathon_time_sec(vdot)
    marathon_pace_sec_per_km = marathon_time_sec / _MARATHON_KM

    zones: dict[str, dict[str, float]] = {}
    for zone_name, (low_factor, high_factor) in _PACE_ZONE_FACTORS.items():
        zones[zone_name] = {
            "fast_sec_per_km": marathon_pace_sec_per_km * low_factor,
            "slow_sec_per_km": marathon_pace_sec_per_km * high_factor,
        }
    # LSD 沿用 Easy 配速區間的慢端下界，供長距離訓練參考。
    zones["lsd"] = {
        "fast_sec_per_km": zones["easy"]["fast_sec_per_km"],
        "slow_sec_per_km": zones["easy"]["slow_sec_per_km"],
    }
    return zones


def _vdot_to_marathon_time_sec(vdot: float, _tolerance_sec: float = 0.5) -> float:
    """estimate_vdot() 的反函式：給定 VDOT，二分搜尋對應的全馬完賽時間。

    Daniels 公式沒有 VDOT → 時間的封閉解析解，用二分搜尋在合理時間範圍
    （90 分鐘～6 小時）內反解，因為 estimate_vdot() 對時間是嚴格遞減函式。
    """
    low_sec, high_sec = 90 * 60.0, 6 * 60 * 60.0
    while high_sec - low_sec > _tolerance_sec:
        mid_sec = (low_sec + high_sec) / 2
        mid_vdot = estimate_vdot(mid_sec)
        if mid_vdot > vdot:
            # 時間越短、VDOT 越高——反解時間太短代表算出的 VDOT 偏高，要往長的方向找。
            low_sec = mid_sec
        else:
            high_sec = mid_sec
    return (low_sec + high_sec) / 2


def estimate_vdot_and_paces(
    activities: list[dict[str, Any]],
    max_hr_bpm: float | None = None,
    max_hr_source: str | None = None,
    reference_date: datetime.date | None = None,
) -> dict[str, Any]:
    """完整流程：挑選基準候選 → Riegel 跨距離推算 → Daniels VDOT → 各課種配速區間。

    max_hr_source：沿用既有 athlete_profile.max_hr_source 語意
        （'watch_display'/'measured'/'age_formula'/'observed_from_data'），
        原樣透傳到輸出結果，不在此函式內做任何信賴度判斷或阻斷計算。

    回傳：
        可推算時：
            {
                "available": True,
                "vdot": float,
                "pace_zones": {"easy": {...}, "marathon": {...}, "tempo": {...},
                                "interval": {...}, "lsd": {...}},
                "source_candidate": {  # 原樣透傳 select_candidate() 的選中結果
                    "distance_category": str, "confidence": str, "reason": str,
                    "hr_converted": bool, "activity": dict,
                },
                "max_hr_source": str | None,
            }
        無法推算時：
            {"available": False, "reason": str}
    """
    candidate_result = select_candidate(
        activities, reference_date=reference_date, max_hr_bpm=max_hr_bpm
    )
    if not candidate_result["available"]:
        return {
            "available": False,
            "reason": candidate_result["reason"],
        }

    if candidate_result["distance_category"] not in _DISTANCE_KM:
        # short_interval 等沒有明確標準距離的類別無法套用 Riegel 推算。
        return {
            "available": False,
            "reason": (
                f"選中的候選（{candidate_result['distance_category']}）"
                "沒有明確的推算距離，無法套用 Riegel 公式換算至全馬"
            ),
        }

    marathon_time_sec = project_to_marathon_time_sec(
        candidate_result["distance_category"],
        candidate_result["effective_pace_sec_per_km"],
    )
    vdot = estimate_vdot(marathon_time_sec)
    pace_zones = compute_pace_zones(vdot)

    return {
        "available": True,
        "vdot": vdot,
        "pace_zones": pace_zones,
        "source_candidate": {
            "distance_category": candidate_result["distance_category"],
            "confidence": candidate_result["confidence"],
            "reason": candidate_result["reason"],
            "hr_converted": candidate_result["hr_converted"],
            "activity": candidate_result["activity"],
        },
        "max_hr_source": max_hr_source,
    }


def select_candidate(
    activities: list[dict[str, Any]],
    reference_date: datetime.date | None = None,
    max_hr_bpm: float | None = None,
) -> dict[str, Any]:
    """從候選活動清單中挑出最適合作為 VDOT 推算基準的一筆。

    activities：每筆至少包含
        - "date": datetime.date，活動日期
        - "distance_category": "marathon"/"half_marathon"/"10k"/"5k"/"short_interval"
        - "is_max_effort": bool，是否為全力程度（測驗/比賽）。若為 False，會嘗試
          用 "avg_hr_bpm" 與 "pace_sec_per_km"（連同 max_hr_bpm 參數）換算等效
          全力配速；換算成功的候選標記為較低信賴度，換算所需欄位缺失時排除
        - "avg_hr_bpm" / "pace_sec_per_km"：非全力程度候選要能被換算所需的欄位
        - 其餘欄位由呼叫端自行附加，本函式不使用、原樣透傳於選中結果的
          "activity" 欄位

    reference_date：計算新鮮度的基準日，預設為今天（可覆寫供測試使用）。

    max_hr_bpm：使用者的最大心率，用於非全力程度候選的心率強度換算。
        沿用既有 athlete_profile.max_hr_bpm 語意，由呼叫端傳入（本函式不碰
        資料庫）。若為 None，非全力程度候選一律無法換算、視為不合格。

    回傳：
        找到候選時：
            {
                "available": True,
                "activity": <選中的原始活動 dict>,
                "distance_category": str,
                "confidence": "high" | "low",
                "reason": str,  # 為何選中這筆，供除錯/顯示使用
                "effective_pace_sec_per_km": float,  # 實際採用的配速
                    # （全力程度候選為原配速，換算候選為等效全力配速）
                "hr_converted": bool,  # 是否經過心率強度換算
            }
        找不到候選時：
            {
                "available": False,
                "reason": str,
            }
    """
    if reference_date is None:
        reference_date = datetime.date.today()

    eligible: list[dict[str, Any]] = []

    for activity in activities:
        category = activity.get("distance_category")
        if category not in _DISTANCE_RANK:
            continue

        usable, effective_pace, hr_converted = _resolve_effort(activity, max_hr_bpm)
        if not usable:
            continue

        activity_date = activity["date"]
        age_days = _freshness_days(reference_date, activity_date)
        passed, freshness_degraded = _passes_freshness_gate(category, age_days)
        if not passed:
            continue

        eligible.append(
            {
                "activity": activity,
                "distance_category": category,
                "rank": _DISTANCE_RANK[category],
                # 新鮮度降級與心率換算降級，任一發生都視為降級信賴度。
                "degraded_confidence": freshness_degraded or hr_converted,
                "hr_converted": hr_converted,
                "effective_pace_sec_per_km": effective_pace,
                "date": activity_date,
            }
        )

    if not eligible:
        if not activities:
            return {
                "available": False,
                "reason": "沒有任何候選活動可供評估",
            }
        return {
            "available": False,
            "reason": "候選活動皆未通過新鮮度門檻，或缺乏可用的心率/配速資料無法換算",
        }

    # 依代表性排序取最優先（rank 越小越優先）；同代表性等級取最新一筆。
    eligible.sort(key=lambda c: (c["rank"], -c["date"].toordinal()))
    best = eligible[0]

    confidence = "low" if best["degraded_confidence"] else "high"
    reason_parts = [f"距離代表性最高的候選（{best['distance_category']}）"]
    if best["hr_converted"]:
        reason_parts.append("，經心率強度換算為等效全力配速，信賴度降級")
    elif best["degraded_confidence"]:
        reason_parts.append("，但已超過標準新鮮度門檻，信賴度降級")

    return {
        "available": True,
        "activity": best["activity"],
        "distance_category": best["distance_category"],
        "confidence": confidence,
        "reason": "".join(reason_parts),
        "effective_pace_sec_per_km": best["effective_pace_sec_per_km"],
        "hr_converted": best["hr_converted"],
    }
