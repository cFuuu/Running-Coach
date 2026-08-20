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

# 週一為週首（0=週一...6=週日），與既有 dashboard weeklyVolume() 的慣例一致
# （見 docs/dev/TODO.md Phase 6 段落）。LSD 固定錨定在週日（星期索引 6），
# 其餘訓練日依課種樣板逆推分配在週日之前的星期幾，讓 LSD 落在真正的週末，
# 而非僅僅是樣板陣列中的最後一個索引。
_LSD_WEEKDAY = 6  # 週日

# 標準天數情況下，每週訓練日的課種輪替樣板（依訓練日在週內出現的順序，
# 最後一個固定為 lsd，實際星期幾由 _weekdays_for_pattern() 決定）。
_STANDARD_DAY_PATTERNS: dict[int, tuple[str, ...]] = {
    4: ("easy", "tempo", "easy", "lsd"),
    5: ("easy", "tempo", "easy", "interval", "lsd"),
    6: ("easy", "tempo", "easy", "interval", "easy", "lsd"),
}

# 低頻率天數（3 天/週）固定配置——2026-08-20 grill 決策：1 次 LSD + 1 次品質課
# + 1 次 easy，取代標準情況下依天數展開的多樣化分配。品質課種類依當週所屬
# 期別決定（見 _quality_workout_for_phase()）。
LOW_FREQUENCY_DAYS_PER_WEEK = 3
_LOW_FREQUENCY_DAY_PATTERN: tuple[str, ...] = ("easy", "quality", "lsd")

# Base 期以 tempo 為主（強度較低、著重有氧閾值累積）；Build/Peak 期可用
# interval（強度較高，對應 PLAN.md §5.1 的期別強度定位）。Taper 期維持 tempo，
# 避免減量期還安排最高強度的間歇。
_LOW_FREQUENCY_QUALITY_WORKOUT_BY_PHASE: dict[str, str] = {
    "base": "tempo",
    "build": "interval",
    "peak": "interval",
    "taper": "tempo",
}

# 課種對應的心率區間標籤（沿用 vdot_engine 配速區間的課種命名）。
# LSD 沿用 easy 的心率區間（vdot_engine.compute_pace_zones() 對 LSD 亦如此處理）。
_WORKOUT_TYPE_TO_PACE_ZONE: dict[str, str] = {
    "easy": "easy",
    "tempo": "tempo",
    "interval": "interval",
    "lsd": "lsd",
}

# 首馬保守配速緩衝——2026-08-20 grill 決策：is_first_marathon=True 時，
# 全部配速區間（fast/slow 兩端）一律加慢這麼多秒/公里，反映「首馬應加重
# 配速保守性」（PLAN.md §5.4）。只加在秒數上（讓配速變慢），不改變區間寬度。
FIRST_MARATHON_PACE_BUFFER_SEC_PER_KM = 10.0

# 首馬 Peak 期至少標記幾次「含補給演練」的長距離訓練日（PLAN.md §5.4）。
FIRST_MARATHON_FUELING_REHEARSAL_COUNT = 2

# 外部限制窗口的三個限制等級——2026-08-20 grill 決策（PLAN.md §5.4）：
# "skip"：窗口內完全不排任何訓練（等同休息）
# "reduced"：窗口內只排 easy/recovery，跳過所有品質課（tempo/interval/lsd）
# "flexible"：不特別限制，照常規排定，僅標記供使用者知道行程可能需要調整
CONSTRAINT_LEVEL_SKIP = "skip"
CONSTRAINT_LEVEL_REDUCED = "reduced"
CONSTRAINT_LEVEL_FLEXIBLE = "flexible"

# reduced 限制等級下，允許保留的課種——其餘一律跳過（不排）。
_REDUCED_ALLOWED_WORKOUT_TYPES = frozenset({"easy", "recovery", "rest"})


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


# 課種樣板中代表「依期別決定的品質課」的佔位符集合——用於 _distribute_week_distance()
# 統一計算距離分配時，把 "quality"（低頻率樣板）與 "tempo"/"interval"（標準樣板）
# 都當成同一種距離配額的品質課日看待。
_QUALITY_WORKOUT_PLACEHOLDERS = frozenset({"tempo", "interval", "quality"})


def _distribute_week_distance(
    day_pattern: tuple[str, ...], weekly_km: float
) -> dict[str, float]:
    """把一週的目標總量依課種樣板分配到各訓練日。

    LSD 佔較大比例（訓練上單次長距離通常佔週量相當比例）、easy 平分剩餘，
    tempo/interval/quality 為固定較短距離的品質課。此為簡化分配邏輯，非精確
    運動生理模型。
    """
    n_quality = sum(1 for w in day_pattern if w in _QUALITY_WORKOUT_PLACEHOLDERS)
    n_easy = sum(1 for w in day_pattern if w == "easy")
    has_lsd = "lsd" in day_pattern

    # LSD 抓週量的 30%，其餘平均分配給品質課與 easy（品質課距離略短於 easy）。
    lsd_km = weekly_km * 0.30 if has_lsd else 0.0
    remaining_km = weekly_km - lsd_km
    quality_km = remaining_km * 0.35 / n_quality if n_quality else 0.0
    easy_km = (remaining_km - quality_km * n_quality) / n_easy if n_easy else 0.0

    return {
        "lsd": lsd_km,
        "tempo": quality_km,
        "interval": quality_km,
        "quality": quality_km,
        "easy": easy_km,
    }


def _quality_workout_for_phase(phase: str) -> str:
    """依當週所屬期別，決定低頻率樣板中 "quality" 佔位符要展開成哪個實際課種。"""
    return _LOW_FREQUENCY_QUALITY_WORKOUT_BY_PHASE.get(phase, "tempo")


def _apply_first_marathon_buffer(pace_zones: dict[str, Any]) -> dict[str, Any]:
    """把每個課種配速區間的 fast/slow 兩端都加上保守緩衝（配速變慢）。

    只在首馬情境下呼叫；回傳新的 dict，不修改傳入的 pace_zones（該物件通常
    是呼叫端從 vdot_engine 拿到、可能在其他地方共用的既有資料）。
    """
    buffered: dict[str, Any] = {}
    for zone_name, zone in pace_zones.items():
        buffered[zone_name] = {
            "fast_sec_per_km": zone["fast_sec_per_km"] + FIRST_MARATHON_PACE_BUFFER_SEC_PER_KM,
            "slow_sec_per_km": zone["slow_sec_per_km"] + FIRST_MARATHON_PACE_BUFFER_SEC_PER_KM,
        }
    return buffered


def _constraint_level_for_date(
    target_date: datetime.date, constraint_windows: list[dict[str, Any]]
) -> str | None:
    """回傳 target_date 落在哪個限制窗口的等級，若不在任何窗口內回傳 None。

    多個窗口重疊涵蓋同一天時，取限制最嚴格者（skip > reduced > flexible），
    避免使用者定義了重疊窗口時，較寬鬆的窗口意外蓋掉較嚴格的窗口。
    """
    severity_order = {
        CONSTRAINT_LEVEL_SKIP: 0,
        CONSTRAINT_LEVEL_REDUCED: 1,
        CONSTRAINT_LEVEL_FLEXIBLE: 2,
    }
    matched_levels = [
        window["level"]
        for window in constraint_windows
        if window["start_date"] <= target_date <= window["end_date"]
    ]
    if not matched_levels:
        return None
    return min(matched_levels, key=lambda level: severity_order.get(level, 99))


def _apply_constraint_windows(
    schedule: list[dict[str, Any]],
    constraint_windows: list[dict[str, Any]],
    pace_zones: dict[str, Any],
) -> None:
    """依限制窗口清單，就地調整 schedule 中對應日期的課種與距離。

    skip：改為 rest，距離歸零、配速區間清空
    reduced：若原課種不在允許清單內（品質課/LSD），降級為 easy；easy/rest 不受影響
    flexible：不改動排定內容，只加上標記供使用者參考

    pace_zones：降級為 easy 時要重新指定的配速區間來源（沿用 generate_schedule()
    當下已套用首馬緩衝與否的那一份，而非原始未緩衝的版本）。
    """
    for day in schedule:
        level = _constraint_level_for_date(day["date"], constraint_windows)
        if level is None:
            day["constraint_level"] = None
            continue

        day["constraint_level"] = level

        if level == CONSTRAINT_LEVEL_SKIP:
            day["workout_type"] = "rest"
            day["target_distance_km"] = 0.0
            day["pace_zone"] = None
            day["fueling_rehearsal"] = False
        elif level == CONSTRAINT_LEVEL_REDUCED:
            if day["workout_type"] not in _REDUCED_ALLOWED_WORKOUT_TYPES:
                day["workout_type"] = "easy"
                day["pace_zone"] = _pace_zone_for_workout("easy", pace_zones)
                day["fueling_rehearsal"] = False
        # flexible：不改動 workout_type/target_distance_km/pace_zone，只留標記。


def _apply_external_dates(
    schedule: list[dict[str, Any]], external_dates: set[datetime.date]
) -> list[dict[str, Any]]:
    """移除 schedule 中已標記為 external 來源的日期，排程器不覆蓋這些日子的安排。"""
    return [day for day in schedule if day["date"] not in external_dates]


def _select_fueling_rehearsal_dates(
    schedule: list[dict[str, Any]], count: int
) -> set[datetime.date]:
    """從 Peak 期的 LSD 訓練日中，挑出最後 count 次作為補給演練日。

    取最後幾次而非最前幾次，是因為越接近比賽的長距離訓練，補給節奏的
    參考價值越高（距離與時間更貼近實際比賽情境）。
    """
    peak_lsd_dates = sorted(
        day["date"]
        for day in schedule
        if day["phase"] == "peak" and day["workout_type"] == "lsd"
    )
    return set(peak_lsd_dates[-count:]) if peak_lsd_dates else set()


def _weekdays_for_pattern(day_pattern: tuple[str, ...]) -> list[int]:
    """把課種樣板（最後一個固定是 lsd）映射到實際星期幾清單（0=週一...6=週日）。

    LSD 固定錨定在 _LSD_WEEKDAY（週日）；其餘訓練日在週日之前平均往回展開，
    盡量平均間隔（例如 3 天樣板 → 週二/週四/週日；5 天樣板 → 週一起算的
    等距分布），確保訓練日之間有恢復間隔而非全部擠在週初。
    """
    n_days = len(day_pattern)
    if n_days == 1:
        return [_LSD_WEEKDAY]

    # 除 LSD 外的 n_days-1 個訓練日，平均分布在週一（0）到週六（5）之間，
    # 最後一個（LSD）固定是 _LSD_WEEKDAY。
    other_days_count = n_days - 1
    span = _LSD_WEEKDAY  # 0..6，可分配的區間
    weekdays = [
        round(i * span / other_days_count) for i in range(other_days_count)
    ]
    weekdays.append(_LSD_WEEKDAY)
    return weekdays


def generate_schedule(config: dict[str, Any]) -> list[dict[str, Any]]:
    """依設定物件產生單日課表清單（標準天數情況，Ticket B 範圍）。

    config 至少包含：
        - "start_date": datetime.date，訓練起始日
        - "total_weeks": int，總訓練週數
        - "days_per_week": int，每週跑步天數。支援 3（低頻率，1 LSD+1 品質課+1
          easy 固定配置）與 4~6（標準情況，依 _STANDARD_DAY_PATTERNS 展開）
        - "pace_zones": dict，沿用 vdot_engine.compute_pace_zones() 的輸出結構
        - "starting_weekly_km"（可選）：起始週跑量，未提供則用 DEFAULT_STARTING_WEEKLY_KM
        - "is_first_marathon"（可選，預設 False）：True 時，(1) 所有配速區間
          加保守緩衝（FIRST_MARATHON_PACE_BUFFER_SEC_PER_KM）、(2) Peak 期最後
          FIRST_MARATHON_FUELING_REHEARSAL_COUNT 次 LSD 標記 fueling_rehearsal=True
        - "constraint_windows"（可選）：外部限制窗口清單，每筆為
          {"start_date": date, "end_date": date, "level": "skip"/"reduced"/"flexible"}。
          窗口只定義日期範圍與限制等級，具體哪天排什麼仍由常規規則在窗口限制下決定
        - "external_dates"（可選）：已標記為外部課表來源（如跟團課表）的日期
          集合/清單，排程器對這些日期直接跳過、不產生新安排（優先於限制窗口——
          若某天已是 external，不需要再套用限制等級判斷）

    回傳：單日課表清單，每筆包含：
        {
            "date": datetime.date,
            "phase": "base"/"build"/"peak"/"taper",
            "workout_type": "easy"/"tempo"/"interval"/"lsd"/"rest",
            "target_distance_km": float,
            "pace_zone": {"fast_sec_per_km": float, "slow_sec_per_km": float} | None,
            "fueling_rehearsal": bool,  # 是否為首馬補給演練日（非首馬恆為 False）
            "constraint_level": "skip"/"reduced"/"flexible" | None,  # 落在哪個限制窗口
        }
    非訓練日（週內未被 day_pattern 涵蓋的日子）workout_type 為 "rest"，
    target_distance_km 為 0.0，pace_zone 為 None。已標記 external 的日期
    完全不會出現在回傳清單中。
    """
    start_date: datetime.date = config["start_date"]
    total_weeks: int = config["total_weeks"]
    days_per_week: int = config["days_per_week"]
    pace_zones: dict[str, Any] = config["pace_zones"]
    starting_weekly_km: float = config.get("starting_weekly_km", DEFAULT_STARTING_WEEKLY_KM)
    is_first_marathon: bool = config.get("is_first_marathon", False)
    constraint_windows: list[dict[str, Any]] = config.get("constraint_windows", [])
    external_dates: set[datetime.date] = set(config.get("external_dates", []))

    if is_first_marathon:
        pace_zones = _apply_first_marathon_buffer(pace_zones)

    is_low_frequency = days_per_week == LOW_FREQUENCY_DAYS_PER_WEEK
    if not is_low_frequency and days_per_week not in _STANDARD_DAY_PATTERNS:
        raise ValueError(
            f"days_per_week={days_per_week} 不在本模組支援的範圍內"
            f"（{LOW_FREQUENCY_DAYS_PER_WEEK} 或 {sorted(_STANDARD_DAY_PATTERNS)}）"
        )

    phase_per_week = _split_weeks_into_phases(total_weeks)
    weekly_volumes = _compute_weekly_volumes(total_weeks, starting_weekly_km)

    schedule: list[dict[str, Any]] = []

    for week_index in range(total_weeks):
        phase = phase_per_week[week_index]
        weekly_km = weekly_volumes[week_index]

        if is_low_frequency:
            day_pattern = _LOW_FREQUENCY_DAY_PATTERN
        else:
            day_pattern = _STANDARD_DAY_PATTERNS[days_per_week]
        distances = _distribute_week_distance(day_pattern, weekly_km)
        training_weekdays = _weekdays_for_pattern(day_pattern)
        weekday_to_workout = dict(zip(training_weekdays, day_pattern))

        # 週一為週首：把該週第 week_index 週的週一算出來，再依實際星期幾放置訓練日，
        # 讓 LSD 真正落在週末（週日），而非樣板陣列中的最後一個索引。
        monday_of_week = (
            start_date
            - datetime.timedelta(days=start_date.weekday())
            + datetime.timedelta(weeks=week_index)
        )
        for weekday in range(7):
            current_date = monday_of_week + datetime.timedelta(days=weekday)
            if current_date < start_date:
                # 訓練起始日之前的日子（僅可能發生在第一週）不產生排程。
                continue
            if weekday in weekday_to_workout:
                workout_type = weekday_to_workout[weekday]
                if workout_type == "quality":
                    # 低頻率樣板的品質課種類依當週所屬期別展開為 tempo/interval。
                    workout_type = _quality_workout_for_phase(phase)
                    target_distance_km = distances["quality"]
                else:
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
                    "fueling_rehearsal": False,
                    "constraint_level": None,
                }
            )

    if constraint_windows:
        # 限制窗口須先套用，才能決定補給演練日——若 Peak 期某次 LSD 已被
        # skip/reduced 窗口拿掉，該天就不該被誤標為補給演練日（見 Ticket F
        # 的組合驗證：首馬條件與限制窗口同時作用時的邊界情境）。
        _apply_constraint_windows(schedule, constraint_windows, pace_zones)

    if is_first_marathon:
        rehearsal_dates = _select_fueling_rehearsal_dates(
            schedule, FIRST_MARATHON_FUELING_REHEARSAL_COUNT
        )
        for day in schedule:
            if day["date"] in rehearsal_dates:
                day["fueling_rehearsal"] = True

    if external_dates:
        schedule = _apply_external_dates(schedule, external_dates)

    return schedule
