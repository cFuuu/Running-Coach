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

本輪（Ticket 1）範圍不含心率強度換算——強度不足的候選（見 is_max_effort）直接
視為不合格被排除，換算邏輯留給 Ticket 2 擴充。
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


def select_candidate(
    activities: list[dict[str, Any]],
    reference_date: datetime.date | None = None,
) -> dict[str, Any]:
    """從候選活動清單中挑出最適合作為 VDOT 推算基準的一筆。

    activities：每筆至少包含
        - "date": datetime.date，活動日期
        - "distance_category": "marathon"/"half_marathon"/"10k"/"5k"/"short_interval"
        - "is_max_effort": bool，是否為全力程度（測驗/比賽）——本輪（Ticket 1）
          範圍內，非全力程度的候選一律視為不合格，直接排除（Ticket 2 會擴充此行為）
        - 其餘欄位（配速、心率等）由呼叫端自行附加，本函式不使用、原樣透傳於
          選中結果的 "activity" 欄位

    reference_date：計算新鮮度的基準日，預設為今天（可覆寫供測試使用）。

    回傳：
        找到候選時：
            {
                "available": True,
                "activity": <選中的原始活動 dict>,
                "distance_category": str,
                "confidence": "high" | "low",
                "reason": str,  # 為何選中這筆，供除錯/顯示使用
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
        if not activity.get("is_max_effort", False):
            # 本輪範圍不含心率強度換算，非全力程度的候選直接跳過。
            continue

        category = activity.get("distance_category")
        if category not in _DISTANCE_RANK:
            continue

        activity_date = activity["date"]
        age_days = _freshness_days(reference_date, activity_date)
        passed, degraded = _passes_freshness_gate(category, age_days)
        if not passed:
            continue

        eligible.append(
            {
                "activity": activity,
                "distance_category": category,
                "rank": _DISTANCE_RANK[category],
                "degraded_confidence": degraded,
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
            "reason": "候選活動皆未通過新鮮度門檻，或皆非全力程度",
        }

    # 依代表性排序取最優先（rank 越小越優先）；同代表性等級取最新一筆。
    eligible.sort(key=lambda c: (c["rank"], -c["date"].toordinal()))
    best = eligible[0]

    confidence = "low" if best["degraded_confidence"] else "high"
    reason = (
        f"距離代表性最高的候選（{best['distance_category']}）"
        + ("，但已超過標準新鮮度門檻，信賴度降級" if best["degraded_confidence"] else "")
    )

    return {
        "available": True,
        "activity": best["activity"],
        "distance_category": best["distance_category"],
        "confidence": confidence,
        "reason": reason,
    }
