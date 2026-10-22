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
Daniels 公式（會嚴重低估真實能力）。做法是用該次活動的配速與心率推估 VO2max，
再換回「若當時全力跑這個距離會是什麼配速」，換算後的候選仍可用，但信賴度
標記較低（非真實測驗/比賽成績）。算法與依據見 estimate_max_effort_pace()。
完全沒有心率資料（或缺 HRmax／靜止心率）的活動無法換算，維持排除。

2026-10-14 修正（換算 bug）：原公式為「配速 × 平均心率/HRmax」，等於假設
配速與心率成正比且過原點，一場 146bpm 的輕鬆 10K 被換算成 4:15/km，推出
VDOT 48.5（同期比賽成績僅 34~40）。修正兩處：
1. 換算公式改用 ACSM 的 %HRR ≈ %VO2R 關係（見 estimate_max_effort_pace）
2. **真實全力成績一律優先於換算候選**，代表性排序只在同一類內比較——原本
   換算後的長距離候選能壓過真實的短距離比賽，錯誤的換算因此直接主導結果

VDOT／配速輸出——套用 Daniels VDOT 公式（Daniels & Gilbert, "Oxygen Power"）：
把選中的基準成績（實際距離與完賽時間）直接代入 VO2/%VO2max 迴歸公式反推
VDOT（見 vdot_from_race），再依 VDOT 產出 Easy/Marathon/Tempo/Interval 各課種
的配速區間。

2026-10-14 改為 Daniels 原版：原本先用 Riegel（指數 1.06）把成績換算成全馬
等效時間再代入公式。Daniels 的 %VO2max 式本身就以比賽時間描述耐力衰減，
再疊一層 Riegel 是重複且互相不一致的假設（半馬 1:57:27 原版 37.1、經 Riegel
36.6，距離越長差越多）。Riegel 只保留於 project_to_marathon_time_sec() 供
全馬完賽時間推估，不再進 VDOT 計算。
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
# 3K 於 2026-10-14 補入（Fu 的跑團 3K 速度測試），代表性排在 5K 之後：
# 距離越短，Riegel 外推到全馬的誤差越大。
_DISTANCE_RANK: dict[str, int] = {
    "marathon": 0,
    "half_marathon": 1,
    "10k": 2,
    "5k": 3,
    "3k": 4,
    "short_interval": 5,
}

# marathon / half_marathon 屬於「長距離」候選，套用寬鬆新鮮度門檻；
# 其餘（10k/5k/short_interval）屬於「短距離」候選，套用嚴格門檻。
_LONG_DISTANCE_CATEGORIES = frozenset({"marathon", "half_marathon"})

# 平均心率低於 HRmax 的這個百分比時，視為「非全力程度」，需要換算才能使用。
# 一般認為馬拉松配速以上強度的平均心率約落在 88~92% HRmax 以上；取 85% 作為
# 保守的分界——高於此值視為已接近全力，不需要換算即可直接使用。
MAX_EFFORT_HR_PCT_THRESHOLD = 0.85

# 安靜代謝的攝氧量（ml/kg/min，1 MET），%VO2R 公式的基準點。
_RESTING_VO2 = 3.5

# Riegel 公式的距離代表性指數：T2 = T1 * (D2/D1)^RIEGEL_EXPONENT。
# PLAN.md §5.5 定案值。
RIEGEL_EXPONENT = 1.06

# 各距離區間對應的公里數，供 Riegel 跨距離推算使用。
_DISTANCE_KM: dict[str, float] = {
    "marathon": 42.195,
    "half_marathon": 21.0975,
    "10k": 10.0,
    "5k": 5.0,
    "3k": 3.0,
    # 短間歇沒有統一距離，Riegel 推算不適用短間歇（見 project_to_marathon 的檢查）。
}

_MARATHON_KM = _DISTANCE_KM["marathon"]

# vdot_to_race_time_sec() 二分搜尋的配速範圍（秒/公里，2:00～20:00）。
# 反解結果落在範圍外會被夾在邊界上，呼叫端要先確保輸入在此範圍內。
SEARCH_PACE_BOUNDS_SEC_PER_KM = (120.0, 1200.0)

# 課種配速區間相對 VDOT 對應全馬配速（Marathon Pace）的比例係數，取自
# Daniels 訓練配速表的常見經驗區間（非逐一查表，屬簡化線性近似）：
# 數值代表「該課種配速 = marathon_pace_sec_per_km * 係數」，係數 < 1 代表更快。
# 每個課種給一組 (下界係數, 上界係數) 區間。
#
# 2026-10-14 Fu 決定**維持固定係數**，不改成 Daniels 的 %VO2max 反解法
# （E 59–74%、T 83–88% 等）。對照（VDOT 40.2）：E 現行 6:11–6:43 vs Daniels
# 5:50–6:58；T 4:53–5:06 vs 5:04–5:19。理由是固定係數較符合他目前的狀況；
# VDOT 本身已於同日改為 Daniels 原版公式，兩件事各自獨立。

_PACE_ZONE_FACTORS: dict[str, tuple[float, float]] = {
    "easy": (1.14, 1.24),
    "marathon": (0.98, 1.02),
    "tempo": (0.90, 0.94),
    "interval": (0.82, 0.87),
}

# 停訓折減——Daniels《Daniels' Running Formula》表 9.1：停跑 ≤ 5 天 VDOT 不變，
# 之後每多停一天約 −0.3%。這條線性規則與 TrailRunCoach 研究筆記
# （docs/research/detraining.md §0）轉引的兩個表值吻合：14 天 ×0.973、
# 28 天 ×0.931。原書未核對；表的最長一列約 72 天，超過不再外推（🔴 未核對）。
# 表另有「中斷期間有交叉訓練」一欄（折減約減半），本專案的交叉訓練多為重訓，
# 不算 Daniels 指的有氧交叉訓練，故一律用不含交叉訓練的那欄（較保守）。
DETRAINING_GRACE_DAYS = 5
DETRAINING_RATE_PER_DAY = 0.003
DETRAINING_MAX_GAP_DAYS = 72


def detraining_factor(gap_days: int) -> float:
    """停跑 gap_days 天後的 VDOT 折減係數（1.0 = 不折減）。"""
    capped = min(gap_days, DETRAINING_MAX_GAP_DAYS)
    return 1.0 - DETRAINING_RATE_PER_DAY * max(capped - DETRAINING_GRACE_DAYS, 0)


def longest_run_gap_days(
    run_dates: list[datetime.date], since: datetime.date, until: datetime.date
) -> int:
    """since（基準成績當天）之後到 until 之間，最長連續沒跑的天數。

    until 視為「虛擬的下一次跑步」：今天還沒跑不算進中斷。
    取**最長**而非最近一次中斷——停訓掉的體能不會因為恢復練習就自動回來，
    要等新的成績證明；Daniels 的折減也是以單次中斷的長度查表、不累加多次。
    """
    dates = sorted({d for d in run_dates if since <= d <= until} | {since, until})
    return max(((b - a).days - 1 for a, b in zip(dates, dates[1:])), default=0)


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


def running_vo2(pace_sec_per_km: float) -> float:
    """Daniels & Gilbert 的跑步攝氧成本迴歸式：配速 → VO2（ml/kg/min）。
    與 estimate_vdot() 內的 VO2 項是同一條公式。"""
    velocity_m_per_min = 60000.0 / pace_sec_per_km
    return -4.60 + 0.182258 * velocity_m_per_min + 0.000104 * velocity_m_per_min**2


def estimate_max_effort_pace(
    pace_sec_per_km: float,
    avg_hr_bpm: float,
    max_hr_bpm: float,
    resting_hr_bpm: float,
    distance_category: str,
) -> float | None:
    """依心率強度將次全力配速換算成該距離的等效全力配速（秒/公里）。

    算法（次最大強度推估 VO2max）：
    1. Daniels 攝氧成本式：該場配速 → 當時的 VO2
    2. %HRR ≈ %VO2R（ACSM 採用，Swain & Leutholtz 1997）：
           VO2max = VO2rest + (VO2 − VO2rest) / %HRR，VO2rest = 3.5
    3. 把推估的 VO2max 當 VDOT，用 Daniels 公式反解該距離的完賽時間與配速
       ——讓後續 vdot_from_race() 由這個配速得到同一個 VDOT

    已知偏誤：次最大強度推估對耐力訓練量少的跑者會**偏高**（體能測得出的
    有氧上限，比實際比賽跑得出的高）；HRmax／靜止心率的誤差也會直接放大到
    結果上。故換算候選一律降級信賴度，且永遠排在真實全力成績之後。

    %HRR 超出 (0, 1] 時回傳 None（心率不高於靜止心率代表資料有問題，無從換算）。
    """
    reserve = max_hr_bpm - resting_hr_bpm
    if reserve <= 0:
        return None
    hrr_pct = min((avg_hr_bpm - resting_hr_bpm) / reserve, 1.0)
    if hrr_pct <= 0:
        return None
    vo2max = _RESTING_VO2 + (running_vo2(pace_sec_per_km) - _RESTING_VO2) / hrr_pct
    distance_km = _DISTANCE_KM[distance_category]
    return vdot_to_race_time_sec(vo2max, distance_km) / distance_km


def _resolve_effort(
    activity: dict[str, Any],
    max_hr_bpm: float | None,
    resting_hr_bpm: float | None,
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

    category = activity.get("distance_category")
    if not resting_hr_bpm or category not in _DISTANCE_KM:
        # 換算需要靜止心率（%HRR）與明確距離（反解該距離的配速）
        return False, None, False
    equivalent_pace = estimate_max_effort_pace(
        pace, avg_hr_bpm, max_hr_bpm, resting_hr_bpm, category
    )
    if equivalent_pace is None:
        return False, None, False
    return True, equivalent_pace, True


def project_to_marathon_time_sec(
    distance_category: str,
    pace_sec_per_km: float,
) -> float:
    """用 Riegel 公式將任一距離的配速，推算至全馬距離的等效完賽時間（秒）。

    僅供「全馬大概跑幾小時」的推估用；**不參與 VDOT 計算**（見模組說明）。

    T2 = T1 * (D2/D1)^RIEGEL_EXPONENT，D1/D2 為距離（公里）、T1/T2 為完賽時間。
    """
    distance_km = _DISTANCE_KM.get(distance_category)
    if distance_km is None:
        raise ValueError(
            f"'{distance_category}' 沒有明確的推算距離（如短間歇），無法套用 Riegel 公式"
        )
    base_time_sec = pace_sec_per_km * distance_km
    return base_time_sec * (_MARATHON_KM / distance_km) ** RIEGEL_EXPONENT


def vdot_from_race(distance_km: float, time_sec: float) -> float:
    """依一場全力成績（距離、完賽時間），用 Daniels VO2/%VO2max 迴歸公式反推 VDOT。

    採用 Daniels & Gilbert（"Oxygen Power"）給出的兩條迴歸公式：
        VO2 = -4.60 + 0.182258 * v + 0.000104 * v^2   （v：公尺/分鐘配速）
        %VO2max = 0.8 + 0.1894393 * e^(-0.012778*t) + 0.2989558 * e^(-0.1932605*t)
                                                                    （t：分鐘）
        VDOT = VO2 / %VO2max
    第一式是該速度的攝氧成本，第二式是能持續 t 分鐘的最大攝氧比例
    （比賽越久比例越低）。這就是 Daniels VDOT 表背後的公式。
    """
    time_min = time_sec / 60.0
    vo2 = running_vo2(time_sec / distance_km)
    pct_vo2max = (
        0.8
        + 0.1894393 * pow(2.718281828459045, -0.012778 * time_min)
        + 0.2989558 * pow(2.718281828459045, -0.1932605 * time_min)
    )
    return vo2 / pct_vo2max


def estimate_vdot(marathon_time_sec: float) -> float:
    """vdot_from_race() 的全馬特例。"""
    return vdot_from_race(_MARATHON_KM, marathon_time_sec)


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


def _vdot_to_marathon_time_sec(vdot: float) -> float:
    return vdot_to_race_time_sec(vdot, _MARATHON_KM)


def vdot_to_race_time_sec(
    vdot: float, distance_km: float, _tolerance_sec: float = 0.5
) -> float:
    """vdot_from_race() 的反函式：給定 VDOT 與距離，二分搜尋對應的完賽時間。

    Daniels 公式沒有 VDOT → 時間的封閉解析解，用二分搜尋在合理配速範圍
    （2:00～20:00/km）內反解，因為 vdot_from_race() 對時間是嚴格遞減函式。
    """
    low_sec, high_sec = (distance_km * p for p in SEARCH_PACE_BOUNDS_SEC_PER_KM)
    while high_sec - low_sec > _tolerance_sec:
        mid_sec = (low_sec + high_sec) / 2
        mid_vdot = vdot_from_race(distance_km, mid_sec)
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
    resting_hr_bpm: float | None = None,
    run_dates: list[datetime.date] | None = None,
) -> dict[str, Any]:
    """完整流程：挑選基準候選 → Daniels VDOT（實際距離）→ 停訓折減 → 各課種配速區間。

    max_hr_source：沿用既有 athlete_profile.max_hr_source 語意
        （'watch_display'/'measured'/'age_formula'/'observed_from_data'），
        原樣透傳到輸出結果，不在此函式內做任何信賴度判斷或阻斷計算。

    resting_hr_bpm：非全力候選的心率換算需要（%HRR），見 select_candidate()。

    run_dates：所有跑步的日期，用來找基準成績之後最長的停跑中斷並依 Daniels
        表 9.1 折減（見 detraining_factor）。未提供時不折減，detraining 為 None。

    回傳：
        可推算時：
            {
                "available": True,
                "vdot": float,           # 已套用停訓折減
                "detraining": {"longest_gap_days": int, "factor": float,
                               "unadjusted_vdot": float} | None,
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
        activities,
        reference_date=reference_date,
        max_hr_bpm=max_hr_bpm,
        resting_hr_bpm=resting_hr_bpm,
    )
    if not candidate_result["available"]:
        return {
            "available": False,
            "reason": candidate_result["reason"],
        }

    if candidate_result["distance_category"] not in _DISTANCE_KM:
        # short_interval 等沒有明確標準距離的類別無法代入 Daniels 公式。
        return {
            "available": False,
            "reason": (
                f"選中的候選（{candidate_result['distance_category']}）"
                "沒有明確的標準距離，無法代入 Daniels 公式"
            ),
        }

    distance_km = _DISTANCE_KM[candidate_result["distance_category"]]
    vdot = vdot_from_race(
        distance_km, candidate_result["effective_pace_sec_per_km"] * distance_km
    )
    detraining = None
    if run_dates is not None:
        gap_days = longest_run_gap_days(
            run_dates,
            candidate_result["activity"]["date"],
            reference_date or datetime.date.today(),
        )
        factor = detraining_factor(gap_days)
        detraining = {
            "longest_gap_days": gap_days,
            "factor": round(factor, 3),
            "unadjusted_vdot": vdot,
        }
        vdot *= factor
    pace_zones = compute_pace_zones(vdot)

    return {
        "available": True,
        "vdot": vdot,
        "detraining": detraining,
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
    resting_hr_bpm: float | None = None,
) -> dict[str, Any]:
    """從候選活動清單中挑出最適合作為 VDOT 推算基準的一筆。

    排序：**真實全力成績（含心率已達全力門檻者）一律優先於心率換算候選**；
    同一類之內才比距離代表性，同代表性取最新。見模組說明的 2026-10-14 修正。

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

    resting_hr_bpm：使用者的靜止心率，心率換算（%HRR）所需。若為 None，
        需要換算的低強度候選一律視為不合格（心率已達全力門檻者不受影響）。

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

        usable, effective_pace, hr_converted = _resolve_effort(
            activity, max_hr_bpm, resting_hr_bpm
        )
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

    # 真實全力成績優先（hr_converted=False 排前），其次代表性（rank 越小越
    # 優先），同代表性等級取最新一筆。
    eligible.sort(key=lambda c: (c["hr_converted"], c["rank"], -c["date"].toordinal()))
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
