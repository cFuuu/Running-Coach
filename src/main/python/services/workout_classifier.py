"""依每公里分圈推測這次實際練的是哪種課表（間歇／LSD／節奏跑／輕鬆跑）。

這是**啟發式猜測，不是精確分類**——同樣的配速分布，可能是刻意的節奏跑，也可能是
輕鬆跑遇到上坡。因此寫入資料庫時一律標記 workout_type_source='auto'，使用者可手動
覆寫成 'manual'。所有閾值集中在本模組頂端，方便依實際資料調整。

為什麼需要：使用者指出「跑課表訓練的單圈並不是固定 1km」，間歇的圈與 LSD 的圈意義
完全不同，同一組分圈資料要先知道是哪種訓練才能正確解讀。
"""

from __future__ import annotations

import statistics

# --- 可調閾值（集中管理，勿散落於邏輯中）---

# 分圈配速標準差超過此值（秒/km），視為配速起伏大 → 可能是間歇
INTERVAL_PACE_STDEV_SEC = 45

# 判定間歇還需要「快慢交替」：最快圈與最慢圈的配速差（秒/km）
INTERVAL_PACE_SPREAD_SEC = 90

# 距離達到近期平均的幾倍時視為長跑
LSD_DISTANCE_RATIO = 1.5

# 節奏跑：配速比近期平均快這麼多（秒/km）且維持平穩
TEMPO_FASTER_THAN_AVG_SEC = 20
TEMPO_MAX_STDEV_SEC = 25

# 少於這麼多圈就無法判斷配速結構
MIN_LAPS_FOR_CLASSIFICATION = 3

# 過短的圈（公里）多半是暖身/收操/按錯錶，計算配速結構時排除
MIN_MEANINGFUL_LAP_KM = 0.3


def classify_workout(
    laps: list[dict],
    total_distance_km: float | None = None,
    recent_avg_distance_km: float | None = None,
    recent_avg_pace_sec_per_km: int | None = None,
) -> str:
    """回傳 'interval' / 'lsd' / 'tempo' / 'easy' / 'unknown'。

    laps：fit_parser.parse_fit() 產出的分圈清單。
    recent_avg_*：該使用者近期平均值，用來做相對判斷；沒有就退回較保守的規則。
    """
    meaningful = [
        lap
        for lap in laps
        if lap.get("pace_sec_per_km") and (lap.get("distance_km") or 0) >= MIN_MEANINGFUL_LAP_KM
    ]

    # 先判斷長跑：即使圈數不足以看出配速結構，距離本身就足以認定
    if (
        total_distance_km
        and recent_avg_distance_km
        and total_distance_km >= recent_avg_distance_km * LSD_DISTANCE_RATIO
    ):
        return "lsd"

    if len(meaningful) < MIN_LAPS_FOR_CLASSIFICATION:
        return "unknown"

    paces = [lap["pace_sec_per_km"] for lap in meaningful]
    stdev = statistics.stdev(paces)
    spread = max(paces) - min(paces)

    if stdev >= INTERVAL_PACE_STDEV_SEC and spread >= INTERVAL_PACE_SPREAD_SEC:
        return "interval"

    if (
        recent_avg_pace_sec_per_km
        and stdev <= TEMPO_MAX_STDEV_SEC
        and statistics.mean(paces) <= recent_avg_pace_sec_per_km - TEMPO_FASTER_THAN_AVG_SEC
    ):
        return "tempo"

    return "easy"
