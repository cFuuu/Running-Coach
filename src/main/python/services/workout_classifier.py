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
# ⚠️ 只用於下方「標準差＋全距」與 tempo／easy 規則。分段（segment_laps）
# 不能用距離過濾：錶設 400m 自動分圈時，1000m 反覆會被切成 400＋400＋約 200m，
# 休息慢跑也常只有約 200m——用距離過濾會把間歇的結構一起丟掉（N-24）。
MIN_MEANINGFUL_LAP_KM = 0.3

# --- 分段（N-24）：相鄰快圈合併成一趟 work、相鄰慢圈合併成一段 recovery ---
# 🔴 以下皆為工程決策、無文獻出處，依 2026-10-23 對真實資料的試算訂定。

# 短於此秒數的圈視為按錯錶，分段時忽略（不切斷前後段）
MIN_LAP_DURATION_SEC = 20

# 配速比「快圈基準」慢這麼多比例以上視為 recovery。快圈基準 = 較快那一半
# 圈數的配速中位數。0.35 對照試算：0.25 會把 LSD 中途停下的圈也切成休息段
# （24 場翻成間歇，0.35 為 20 場）；Fu 的休息段慢跑走路約比快段慢 70%。
RECOVERY_SLOWER_RATIO = 0.35

# work 段至少要這麼長才算一趟（排除結尾衝線的零碎小段）
MIN_WORK_SEGMENT_SEC = 30

# 每一趟 work 都要比近期平均配速快這麼多才算「快段」——只看快慢交替會把
# 「tempo 中途停下一次」誤判成間歇（實例 2025-11-25：5:51 暖身 1km、
# 停頓 1km、5:00 跑 8km），要求每段本身都夠快才排除得掉。
INTERVAL_WORK_FASTER_THAN_AVG_SEC = 20

# 至少要有幾趟快段才判為間歇。設 2 而非 3：課表 4000m＋休息＋2000m 也是間歇課
MIN_FAST_WORK_SEGMENTS = 2

# 快段之間的休息段最長秒數。長跑中途走路或停下 7–20 分鐘（例 2025-07-26：
# 3.7km、走 9 分鐘、5.3km）前後兩段都可能「夠快」，但那不是組間休息；
# Fu 的間歇課組間休息實測 2–4 分鐘（10/22 約 2 分鐘、10/15 為 3 分 49 秒）。
MAX_RECOVERY_SEGMENT_SEC = 5 * 60


def segment_laps(laps: list[dict]) -> list[dict]:
    """把分圈依配速切成 work／recovery 段，相鄰同角色的圈合併。

    分圈 ≠ 一趟反覆：自動分圈會把一趟 1000m 切成好幾圈，這裡把它們合回來。
    回傳依時間順序的段落清單，每段含 `role`、`lap_indexes`、`distance_km`、
    `duration_sec`、`pace_sec_per_km`、`avg_hr_bpm`（以時長加權，無心率為 None）。
    有效圈數不足 3 時回傳空 list（看不出結構）。
    """
    valid = [
        lap
        for lap in laps
        if lap.get("pace_sec_per_km")
        and (lap.get("duration_sec") or 0) >= MIN_LAP_DURATION_SEC
        and (lap.get("distance_km") or 0) > 0
    ]
    if len(valid) < MIN_LAPS_FOR_CLASSIFICATION:
        return []

    paces = [lap["pace_sec_per_km"] for lap in valid]
    median_pace = statistics.median(paces)
    fast_reference = statistics.median([p for p in paces if p <= median_pace])
    recovery_from = fast_reference * (1 + RECOVERY_SLOWER_RATIO)

    groups: list[tuple[str, list[dict]]] = []
    for lap in valid:
        role = "recovery" if lap["pace_sec_per_km"] >= recovery_from else "work"
        if groups and groups[-1][0] == role:
            groups[-1][1].append(lap)
        else:
            groups.append((role, [lap]))

    segments = []
    for role, members in groups:
        distance = sum(lap["distance_km"] for lap in members)
        duration = sum(lap["duration_sec"] for lap in members)
        with_hr = [lap for lap in members if lap.get("avg_hr_bpm")]
        hr_duration = sum(lap["duration_sec"] for lap in with_hr)
        segments.append({
            "role": role,
            "lap_indexes": [lap.get("lap_index") for lap in members],
            "distance_km": distance,
            "duration_sec": duration,
            "pace_sec_per_km": duration / distance,
            "avg_hr_bpm": (
                sum(lap["avg_hr_bpm"] * lap["duration_sec"] for lap in with_hr) / hr_duration
                if hr_duration else None
            ),
        })
    return segments


def work_segments(segments: list[dict]) -> list[dict]:
    """取出夠長、可當作一趟反覆的 work 段。"""
    return [
        s for s in segments
        if s["role"] == "work" and s["duration_sec"] >= MIN_WORK_SEGMENT_SEC
    ]


def _has_interval_structure(laps: list[dict], recent_avg_pace_sec_per_km: int | None) -> bool:
    """是否有 ≥2 趟明顯快於平常的 work 段、中間夾著不超過 5 分鐘的 recovery。

    沒有近期平均配速時不判（無從判斷「快」），交給後面的舊規則。
    """
    if not recent_avg_pace_sec_per_km:
        return False
    segments = segment_laps(laps)
    fast = [
        s for s in work_segments(segments)
        if s["pace_sec_per_km"] <= recent_avg_pace_sec_per_km - INTERVAL_WORK_FASTER_THAN_AVG_SEC
    ]
    if len(fast) < MIN_FAST_WORK_SEGMENTS:
        return False
    between = segments[segments.index(fast[0]):segments.index(fast[-1]) + 1]
    return all(
        s["duration_sec"] <= MAX_RECOVERY_SEGMENT_SEC for s in between if s["role"] == "recovery"
    )


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

    # 先看分段結構：快段與休息段交替即為間歇，不受 MIN_MEANINGFUL_LAP_KM 影響
    if _has_interval_structure(laps, recent_avg_pace_sec_per_km):
        return "interval"

    if len(meaningful) < MIN_LAPS_FOR_CLASSIFICATION:
        return "unknown"

    paces = [lap["pace_sec_per_km"] for lap in meaningful]
    stdev = statistics.stdev(paces)
    spread = max(paces) - min(paces)

    # 舊的「標準差＋全距」規則只在沒有近期平均（分段規則無法判「快」）時當備援。
    # 有近期平均時改由分段規則全權判定間歇：舊規則會把「輕鬆跑中途走路一公里」
    # 判成間歇——1km 自動分圈夾一圈 9:00/km 的走路，標準差就超標。2026-10-23
    # 實測當時被判 interval 的場次中 18 場屬此情形（N-24）。
    if (
        not recent_avg_pace_sec_per_km
        and stdev >= INTERVAL_PACE_STDEV_SEC
        and spread >= INTERVAL_PACE_SPREAD_SEC
    ):
        return "interval"

    if (
        recent_avg_pace_sec_per_km
        and stdev <= TEMPO_MAX_STDEV_SEC
        and statistics.mean(paces) <= recent_avg_pace_sec_per_km - TEMPO_FASTER_THAN_AVG_SEC
    ):
        return "tempo"

    return "easy"
