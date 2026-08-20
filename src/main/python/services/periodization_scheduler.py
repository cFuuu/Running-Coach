"""依全馬週期化框架，把訓練窗口拆解成單日課表。

本模組刻意不碰資料庫：吃單一設定物件（dict），回傳單日課表清單（list[dict]），
方便單元測試。資料庫寫入（拉 VDOT 配速區間、寫入 training_plan）交給上層
orchestrator 負責。（與 vdot_engine.py 的分層方式一致。）

為什麼是單一設定物件而非分散參數——2026-08-20 grill 討論結論：
本模組需要的輸入項目（總週數、每週天數、配速區間、is_first_marathon、
限制窗口清單等）遠多於 vdot_engine 的 estimate_vdot_and_paces()，分散成
十幾個位置參數會讓函式簽章難以閱讀且容易傳錯順序，故包裝成單一 dict。

為什麼期別劃分／量進減量與低頻率天數／首馬條件／限制窗口分開設計——
Ticket B（本檔）只實作標準情況下的核心骨架（期別劃分、量進/減量、標準
天數分配、單日輸出），低頻率天數配置、首馬條件分支、外部限制窗口與外部
課表協調皆為疊加在此骨架之上的獨立擴充（見 Ticket C/D/E），互不依賴彼此
的實作細節，只依賴本檔案建立的期別劃分與量進/減量規則。
"""

from __future__ import annotations

import datetime
import math
from typing import Any

# --- 可調閾值（集中管理，勿散落於邏輯中）---

# 各期別佔總週數的比例，對應 PLAN.md §5.1。四者總和須為 1.0。
PHASE_RATIOS: dict[str, float] = {
    "base": 0.40,
    "build": 0.30,
    "peak": 0.20,
    "taper": 0.10,
}

# 期別劃分的固定順序（Taper 一定在最後，緊接比賽日）。
_PHASE_ORDER: tuple[str, ...] = ("base", "build", "peak", "taper")

# 週跑量每週增幅上限（比例）。PLAN.md §5.2：不超過 10%，保守情境 5~8%。
WEEKLY_VOLUME_INCREASE_CAP = 0.10

# 每隔幾週安排一次減量週。PLAN.md §5.2：每 3–4 週一次，此處取保守值 3。
STEP_BACK_INTERVAL_WEEKS = 3

# 減量週的量降至前一週的比例區間下界／上界。
STEP_BACK_RATIO_LOW = 0.70
STEP_BACK_RATIO_HIGH = 0.80

# Peak 期最長單次距離上限（公里）。PLAN.md §5.2：30–35K 或 3~3.5 小時取先到者，
# 此處只用距離上限（時間上限需搭配配速換算，留待呼叫端或未來擴充處理）。
PEAK_MAX_LONG_RUN_KM = 35.0

# 訓練起始週的基準週跑量（公里）。這是排程器計算後續每週增幅時的起點，
# 呼叫端可透過設定物件的 "starting_weekly_km" 覆寫；若未提供則用此保守預設值，
# 對應一般具備基礎跑步習慣者的起始量。
DEFAULT_STARTING_WEEKLY_KM = 20.0

# 標準天數情況下，每週訓練日的課種輪替樣板（依訓練日在週內出現的順序）。
# 本輪（Ticket B）僅支援 4~6 天/週的標準情況；3 天/週的簡化配置留給 Ticket C。
_STANDARD_DAY_PATTERNS: dict[int, tuple[str, ...]] = {
    4: ("easy", "tempo", "easy", "lsd"),
    5: ("easy", "tempo", "easy", "interval", "lsd"),
    6: ("easy", "tempo", "easy", "interval", "easy", "lsd"),
}

# 課種對應的心率區間標籤（沿用 vdot_engine 配速區間的課種命名）。
# LSD 沿用 easy 的心率區間（vdot_engine.compute_pace_zones() 對 LSD 亦如此處理）。
_WORKOUT_TYPE_TO_PACE_ZONE: dict[str, str] = {
    "easy": "easy",
    "tempo": "tempo",
    "interval": "interval",
    "lsd": "lsd",
}


def _split_weeks_into_phases(total_weeks: int) -> list[str]:
    """依 PHASE_RATIOS 把總週數劃分成每週所屬期別的清單，長度等於 total_weeks。

    採用最大餘數法（Largest Remainder Method）分配整數週數，確保捨入後
    各期別週數總和仍精確等於 total_weeks，且比例盡量貼近 PHASE_RATIOS。
    """
    raw_counts = {phase: total_weeks * ratio for phase, ratio in PHASE_RATIOS.items()}
    floor_counts = {phase: int(math.floor(count)) for phase, count in raw_counts.items()}
    remainder = total_weeks - sum(floor_counts.values())

    # 依小数部分大小，把剩餘週數依序分給小数部分最大的期別。
    remainders = sorted(
        _PHASE_ORDER, key=lambda p: raw_counts[p] - floor_counts[p], reverse=True
    )
    for phase in remainders[:remainder]:
        floor_counts[phase] += 1

    weeks: list[str] = []
    for phase in _PHASE_ORDER:
        weeks.extend([phase] * floor_counts[phase])
    return weeks


def _is_step_back_week(week_index: int) -> bool:
    """第 week_index 週（0-indexed）是否為減量週。

    每 STEP_BACK_INTERVAL_WEEKS 週的最後一週為減量週（例如間隔 3 時，
    第 2、5、8...週，0-indexed，為減量週）。第 0 週（訓練最初一週）不減量。
    """
    if week_index == 0:
        return False
    return (week_index + 1) % STEP_BACK_INTERVAL_WEEKS == 0


def _compute_weekly_volumes(total_weeks: int, starting_weekly_km: float) -> list[float]:
    """依週增幅上限與減量週規則，算出每週的目標週跑量（公里）清單。"""
    volumes: list[float] = []
    current = starting_weekly_km
    for week_index in range(total_weeks):
        if week_index == 0:
            volumes.append(current)
            continue
        if _is_step_back_week(week_index):
            # 減量週：降至前一週的 70~80%，取區間中點作為確定值。
            current = volumes[-1] * (STEP_BACK_RATIO_LOW + STEP_BACK_RATIO_HIGH) / 2
        else:
            current = volumes[-1] * (1 + WEEKLY_VOLUME_INCREASE_CAP)
        volumes.append(current)
    return volumes


def _pace_zone_for_workout(workout_type: str, pace_zones: dict[str, Any]) -> dict[str, Any] | None:
    zone_key = _WORKOUT_TYPE_TO_PACE_ZONE.get(workout_type)
    if zone_key is None:
        return None
    return pace_zones.get(zone_key)


def _distribute_week_distance(
    day_pattern: tuple[str, ...], weekly_km: float
) -> dict[str, float]:
    """把一週的目標總量依課種樣板分配到各訓練日。

    LSD 佔較大比例（訓練上單次長距離通常佔週量相當比例）、easy 平分剩餘，
    tempo/interval 為固定較短距離的品質課。此為簡化分配邏輯，非精確運動生理模型。
    """
    n_quality = sum(1 for w in day_pattern if w in ("tempo", "interval"))
    n_easy = sum(1 for w in day_pattern if w == "easy")
    has_lsd = "lsd" in day_pattern

    # LSD 抓週量的 30%，其餘平均分配給品質課與 easy（品質課距離略短於 easy）。
    lsd_km = weekly_km * 0.30 if has_lsd else 0.0
    remaining_km = weekly_km - lsd_km
    quality_km = remaining_km * 0.35 / n_quality if n_quality else 0.0
    easy_km = (remaining_km - quality_km * n_quality) / n_easy if n_easy else 0.0

    return {"lsd": lsd_km, "tempo": quality_km, "interval": quality_km, "easy": easy_km}


def generate_schedule(config: dict[str, Any]) -> list[dict[str, Any]]:
    """依設定物件產生單日課表清單（標準天數情況，Ticket B 範圍）。

    config 至少包含：
        - "start_date": datetime.date，訓練起始日
        - "total_weeks": int，總訓練週數
        - "days_per_week": int，每週跑步天數（本輪僅支援 4~6，3 天/週見 Ticket C）
        - "pace_zones": dict，沿用 vdot_engine.compute_pace_zones() 的輸出結構
        - "starting_weekly_km"（可選）：起始週跑量，未提供則用 DEFAULT_STARTING_WEEKLY_KM

    回傳：單日課表清單，每筆包含：
        {
            "date": datetime.date,
            "phase": "base"/"build"/"peak"/"taper",
            "workout_type": "easy"/"tempo"/"interval"/"lsd"/"rest",
            "target_distance_km": float,
            "pace_zone": {"fast_sec_per_km": float, "slow_sec_per_km": float} | None,
        }
    非訓練日（週內未被 day_pattern 涵蓋的日子）workout_type 為 "rest"，
    target_distance_km 為 0.0，pace_zone 為 None。
    """
    start_date: datetime.date = config["start_date"]
    total_weeks: int = config["total_weeks"]
    days_per_week: int = config["days_per_week"]
    pace_zones: dict[str, Any] = config["pace_zones"]
    starting_weekly_km: float = config.get("starting_weekly_km", DEFAULT_STARTING_WEEKLY_KM)

    if days_per_week not in _STANDARD_DAY_PATTERNS:
        raise ValueError(
            f"days_per_week={days_per_week} 不在本模組（Ticket B）支援的標準範圍 "
            f"{sorted(_STANDARD_DAY_PATTERNS)} 內；3 天/週的簡化配置見 Ticket C"
        )

    day_pattern = _STANDARD_DAY_PATTERNS[days_per_week]
    phase_per_week = _split_weeks_into_phases(total_weeks)
    weekly_volumes = _compute_weekly_volumes(total_weeks, starting_weekly_km)

    schedule: list[dict[str, Any]] = []

    for week_index in range(total_weeks):
        phase = phase_per_week[week_index]
        weekly_km = weekly_volumes[week_index]
        distances = _distribute_week_distance(day_pattern, weekly_km)

        week_start = start_date + datetime.timedelta(weeks=week_index)
        # 訓練日安排在週的前 days_per_week 天，其餘為休息日；
        # LSD 固定安排在該週的最後一個訓練日（樣板中 lsd 一律位於末位）。
        for day_offset in range(7):
            current_date = week_start + datetime.timedelta(days=day_offset)
            if day_offset < len(day_pattern):
                workout_type = day_pattern[day_offset]
                target_distance_km = distances.get(workout_type, 0.0)
                if phase == "peak" and workout_type == "lsd":
                    target_distance_km = min(target_distance_km, PEAK_MAX_LONG_RUN_KM)
                pace_zone = _pace_zone_for_workout(workout_type, pace_zones)
            else:
                workout_type = "rest"
                target_distance_km = 0.0
                pace_zone = None

            schedule.append(
                {
                    "date": current_date,
                    "phase": phase,
                    "workout_type": workout_type,
                    "target_distance_km": target_distance_km,
                    "pace_zone": pace_zone,
                }
            )

    return schedule
