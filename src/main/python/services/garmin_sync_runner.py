"""Garmin Connect 增量同步：抓取 → 正規化 → 去重寫入 SQLite。

用法：
    # 自動補齊到上次同步的斷點（預設行為）
    python -m src.main.python.services.garmin_sync_runner \
        --db-path output/running_coach.db

    # 只看計畫與預估耗時，不打 API
    python -m src.main.python.services.garmin_sync_runner \
        --db-path output/running_coach.db --dry-run

需先完成一次性認證（見 `garmin_auth`）：
    python -m src.main.python.services.garmin_auth login

## 自動補齊機制（`--days` 省略時的預設行為）

長期未同步時，固定回溯 N 天會漏掉中間的缺口。故 `days` 省略時改由
`resolve_sync_days()` 依資料庫現況回推：

    斷點 = min(activities 最新日, daily_wellness 最新日)
    天數 = (今天 − 斷點) + 1 + OVERLAP_DAYS，夾到 MAX_AUTO_SYNC_DAYS

三個設計要點：

1. **斷點取兩表較舊者**。兩表天生不同步——沒運動的日子沒有活動列，
   wellness 卻天天都有。以較新的 wellness 日期為斷點，會讓當天稍晚
   才上傳的活動永遠落在下次區間之外，形成永久缺口。
2. **往前多抓 `OVERLAP_DAYS` 天**。Garmin 的睡眠分數／HRV／Training
   Readiness 常隔日才算出，當天抓到的列欄位不全，需重抓補齊。
   upsert 用 COALESCE 只補空值不覆寫，重抓無副作用。
3. **夾到 `MAX_AUTO_SYNC_DAYS`（90 天）**。wellness 是逐日各打 4 個
   端點，天數與請求數成正比；超過此範圍應走官方匯出包。夾擠發生時
   結果會帶 `gap_truncated=True`，呼叫端須據此提示使用者。

⚠️ 同步前應先以 `dry_run=True` 取得 `estimate` 並告知使用者預計耗時
——這是與 Fu 議定的行為（2026-09-01）。CLI 已內建詢問確認；MCP tool
無互動能力，改以回傳值中的 `estimate` 讓 AI 在對話中轉述。

## 為何不重用 `garmin_export_parser` 的換算函式

⚠️ **API 與官方匯出包的單位不同**（2026-09-01 實測）：

| 欄位 | 官方匯出包 | REST API |
|---|---|---|
| `distance` | 公分 | **公尺** |
| `duration` | 毫秒 | **秒** |
| `elevationGain` | 公分 | **公尺** |
| `calories` | 千焦耳 | **大卡** |
| `maxSpeed` | 公分/毫秒 | **公尺/秒** |

匯出包那套 `cm_to_km()`／`ms_to_sec()`／`kj_to_kcal()` 套到 API 回傳會
產生 100～1000 倍的錯誤，故本模組自行換算。實測佐證：API `distance=10158.57`
對應 DB 既有的 10.1586km（活動 23997807612）。

## 去重機制

不自行實作指紋比對——`activities` 表已有
`UNIQUE (athlete_id, started_at, activity_type, distance_km)`，
`daily_wellness` 有 `UNIQUE (athlete_id, date, source)`。本模組沿用
`garmin_import_runner` 的 `INSERT ... ON CONFLICT DO UPDATE` 寫法，
由資料庫保證冪等，重複執行同一區間不會產生重複列。

`source` 沿用 `'garmin_export'`（非 `'garmin_mcp'`）是刻意的決定：
`daily_wellness` 的 UNIQUE 含 `source`，若改用新值會讓同一天出現兩列，
而現有查詢（`dashboard_queries` / `readiness` / `training_load_queries`）
皆未依 source 篩選，`readiness.py` 更以日期為 dict key，兩列會非確定性地
互相覆蓋。來源可追溯性改由 `source_version` 欄位承載。

## 分圈與 `workout_type`（`fetch_splits=True` 時自動補齊）

活動摘要端點（`get_activities_by_date`）**不含逐圈明細**——它只有
`splitSummaries`（按跑/走/站類型彙總的總和與平均，無逐圈序列）。故本模組
會對每場跑步另打 `get_activity_splits` 取回逐圈明細，寫入 `activity_laps`
後餵給既有的 `workout_classifier.classify_workout()`。

**刻意重用 `fit_import_runner.store_laps()` 與 `update_workout_type()`**，
不另寫一套：兩條匯入路徑必須用同一套分類標準，否則同一個 `workout_type`
欄位會混入兩種語意不同的值。

⚠️ **不採用「以心率區間推算課種」的替代方案**。摘要確實帶
`hrTimeInZone_1..5`、`hasIntensityIntervals`、`trainingEffectLabel`，
但心率區間分布把時間軸攤平了——10×400m 間歇與穩定節奏跑的區間分布可以
幾乎相同（間歇的休息段把平均拉回來），且心率有滯後。用它反推配速結構
正是分類器設計要避開的錯誤。這些欄位適合當**分類結果的交叉驗證**
（例如分類器說 interval 但 `hasIntensityIntervals=False` 就值得懷疑），
不適合當主要分類依據。

仍為 `NULL` 的情況：該場抓不到分圈、或非 `DEFAULT_CLASSIFIABLE_SPORTS`
（重訓/健走不分類——用跑步的配速門檻分類它們會產生誤導標籤）。此時依
PLAN.md §1「缺前提時留空讓下游拒絕判讀」，屬正確行為。

## 逐點資料與經緯度（N-32，2026-10-24）

跑步活動另打 `get_activity_details`，降到每 10 秒一點寫入
`activity_records`（含經緯度，供路線地圖），補上 N-9 的缺口：同步進來的
活動也能評心率漂移與配速穩定度。已有 FIT 逐點資料但沒有座標的場次，只
補經緯度、不覆寫 FIT 的數值。

## Garmin 黑盒衍生值

Training Readiness／Body Battery／HRV status **照抓照存**，但取用時必須
帶 `garmin_blackbox`（`unusable`）標記，只可作為校準與對照，不得作為
判讀依據。見 PLAN.md §1「黑盒衍生值照抓照存、只當對照組」。
"""

from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
from pathlib import Path

from src.main.python.models.db import get_connection
from src.main.python.services.garmin_api_client import (
    DEFAULT_REQUEST_INTERVAL_SEC,
    GarminApiClient,
    GarminFetchError,
    GarminRateLimited,
)
from src.main.python.services.garmin_auth import GarminAuthError, GarminAuthRequired
from src.main.python.services.warmup_links import link_warmups
from src.main.python.services.fit_import_runner import (
    DEFAULT_CLASSIFIABLE_SPORTS,
    DEFAULT_SAMPLE_EVERY_SEC,
    store_laps,
    store_records,
    update_workout_type,
)
from src.main.python.services.fit_parser import (
    COORD_DECIMALS,
    downsample_records,
    mps_to_pace_sec_per_km,
)

# 寫入時採用的 source 值。沿用 garmin_export 的理由見模組 docstring。
SYNC_SOURCE = "garmin_export"
# 記在 source_version，讓「同步進來的」與「匯出包來的」仍可區分。
SYNC_SOURCE_VERSION = "api_sync"

_SECONDS_PER_MINUTE = 60

# --- 自動補齊參數（見模組 docstring「自動補齊機制」）---

# 資料庫查無任何資料時的保底天數（尚未匯入歷史的全新資料庫）。
DEFAULT_SYNC_DAYS = 7

# 斷點往前多抓的重疊天數。Garmin 的睡眠分數／HRV／Training Readiness
# 常隔日才算出，當天抓到的列欄位不全，須重抓補齊。upsert 用 COALESCE
# 只補空值不覆寫，故重抓無副作用，唯一代價是多幾次 API 呼叫。
OVERLAP_DAYS = 3

# 自動模式單次回溯上限。90 天可涵蓋一個完整馬拉松訓練週期（12–16 週），
# 成本為 721 次請求／約 12 分鐘（見 estimate_sync_cost；2026-10-22 每日端點
# 5 → 8 後由 451 次上升）。更早的缺口應走
# 官方匯出包——一次拿完整歷史，且含分圈與 workout_type，品質優於 API 摘要。
# ⚠️ 不可貿然調高：wellness 是逐日各打 8 個端點，天數與請求數成正比，
# garmin_api_client 的硬上限 365 天等於 2921 次請求／約 49 分鐘連續請求，
# 已超出正常使用者行為的樣子，有觸發 Cloudflare 風控的風險。
MAX_AUTO_SYNC_DAYS = 90

# 每日身體數據的端點數（stats／sleep／hrv／training_readiness／
# training_status／stress_intraday／heart_rate_intraday／health_status），與
# garmin_api_client.fetch_daily_wellness_raw() 的來源數一致。
# 2026-09-02：Task 1B-9 新增 training_status，由 4 → 5；2026-10-22 新增
# 兩支日內序列（Garmin 卡片的日內圖）與健康狀態，5 → 8
# ——若忘記同步更新，耗時預告會低估，違反與 Fu 議定的預告行為。
_WELLNESS_ENDPOINTS_PER_DAY = 8

# 節流間隔沿用 client 的預設值，讓耗時預估與實際行為一致。
REQUEST_INTERVAL_SEC = DEFAULT_REQUEST_INTERVAL_SEC

# 需要維護 metric_coverage 的 daily_wellness 欄位。名稱與既有資料一致
# （匯入器寫入的 metric_name 即為欄位名），故沿用同一組字串，
# 讓同步與匯出匯入兩條路徑更新到同一批列而非各自新增。
_WELLNESS_METRIC_COLUMNS = (
    "resting_hr_bpm",
    "hrv_ms",
    "hrv_weekly_avg_ms",
    "spo2_pct",
    "respiration_rate",
    "sleep_duration_sec",
    "sleep_score",
    "all_day_stress_avg",
    "steps",
    "training_readiness_score",
    "recovery_time_hours",
    "acwr",
    # --- 2026-09-02：Task 1B-9 新增（見 GARMIN_METRICS_AUDIT.md）---
    "hrv_baseline_low_upper",
    "hrv_baseline_balanced_low",
    "hrv_baseline_balanced_upper",
    "hrv_status",
    "hrv_last_night_5min_high",
    "acute_load",
    "rhr_7day_avg_bpm",
    "sleep_deep_sec",
    "sleep_light_sec",
    "sleep_rem_sec",
    "sleep_awake_sec",
    "body_battery_at_wake",
    "readiness_input_context",
    "readiness_timestamp_local",
)


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()


def _round_or_none(value: float | None) -> int | None:
    return None if value is None else round(value)


def _as_int(value) -> int | None:
    """把可能是字串的數值欄位轉為 int（Garmin 的 sleepTimeSeconds 是字串）。"""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


_HR_ZONE_PREFIX = "hrTimeInZone_"
# 只收 Zone 1~5（使用者的訓練強度區間）。Zone 0（低於 Zone 1 的暖身/靜止）
# 與 Zone 6（裝置固定產生但收不到資料的 padding 桶，見 dashboard_queries.py
# HR_ZONE_MAX 註解）不列入，避免與既有的 below_zone_1 查詢語意重複定義。
_HR_ZONE_MIN = 1
_HR_ZONE_MAX = 5


def _build_hr_time_in_zone_json(raw: dict) -> str | None:
    """從活動摘要抽出 `hrTimeInZone_1..5`，換算成秒後存為 JSON 字串。

    ⚠️ 此 payload 的欄位值單位是**秒**，與同一 payload 的 `duration`
    一致，**不可**套用 `ms_to_sec()`——那是官方匯出包（`dashboard_queries.
    _build_hr_zones()` 的來源）才需要的換算。

    2026-09-02 對真實帳號實測：8/30 那場 duration=5648 秒，
    `hrTimeInZone_1..5` 總和為 5517.4（各區間 203.9／5197.4／116.0／0／0），
    與時長相符；若誤除以 1000 會得到總和 5 秒的荒謬值——雲端實作曾因
    沿用匯出包係數而寫錯，已修正。

    全部區間皆缺值或為 0 視為無心率資料，回傳 None（不存一個全 0 的 JSON，
    那會讓下游誤以為「查過了、確實是 0」而非「根本沒量到」）。
    """
    zones: dict[str, int] = {}
    for i in range(_HR_ZONE_MIN, _HR_ZONE_MAX + 1):
        value = raw.get(f"{_HR_ZONE_PREFIX}{i}")
        if value is None:
            continue
        zones[str(i)] = round(value)
    if not zones or all(v == 0 for v in zones.values()):
        return None
    return json.dumps(zones, ensure_ascii=False)


def normalize_activity(raw: dict) -> dict | None:
    """把 API 的活動摘要轉成 `activities` 表的欄位。

    ⚠️ API 單位為公尺／秒／大卡，與匯出包不同——見模組 docstring。
    無法取得開始時間或活動類型的資料視為不可用，回傳 None。
    """
    started_at = raw.get("startTimeLocal")
    type_key = (raw.get("activityType") or {}).get("typeKey")
    if not started_at or not type_key:
        return None

    # "2026-08-16 18:43:31" -> "2026-08-16T18:43:31"（比照既有 DB 格式）
    started_at_iso = str(started_at).replace(" ", "T")

    distance_m = raw.get("distance")
    duration_sec_f = raw.get("duration")
    distance_km = None if distance_m is None else distance_m / 1000
    duration_sec = _round_or_none(duration_sec_f)

    # ⚠️ 配速以**四捨五入後的整數秒**為分子，與 garmin_export_parser 一致
    # （該處分子是已換算成整數秒的 duration_sec）。實測活動 23997807612
    # 真值為 373.5 s/km，正好落在進位邊界：用原始浮點秒得 374，用整數秒得
    # 373——DB 既有值為 373。若此處不對齊，每次重新同步都會讓配速在
    # ±1 秒間跳動，產生假的資料變動。
    avg_pace = None
    if duration_sec and distance_km and distance_km > 0:
        avg_pace = round(duration_sec / distance_km)

    # maxSpeed 為公尺/秒；配速（秒/公里）= 1000 / 速度
    max_speed = raw.get("maxSpeed")
    best_pace = round(1000 / max_speed) if max_speed else None

    # 坡度校正配速（Grade Adjusted Pace）：avgGradeAdjustedSpeed 同樣是
    # 公尺/秒，換算方式與 best_pace 一致。
    grade_adj_speed = raw.get("avgGradeAdjustedSpeed")
    avg_grade_adj_pace = (
        round(1000 / grade_adj_speed) if grade_adj_speed else None
    )

    return {
        "external_id": str(raw["activityId"]) if raw.get("activityId") else None,
        "activity_type": type_key,
        "title": raw.get("activityName"),
        "started_at": started_at_iso,
        "distance_km": distance_km,
        "duration_sec": duration_sec,
        "moving_time_sec": _round_or_none(raw.get("movingDuration")),
        "avg_hr_bpm": _round_or_none(raw.get("averageHR")),
        "max_hr_bpm": _round_or_none(raw.get("maxHR")),
        "aerobic_te": raw.get("aerobicTrainingEffect"),
        "avg_cadence_spm": _round_or_none(
            raw.get("averageRunningCadenceInStepsPerMinute")
        ),
        "max_cadence_spm": _round_or_none(raw.get("maxRunningCadenceInStepsPerMinute")),
        "avg_pace_sec_per_km": avg_pace,
        "best_pace_sec_per_km": best_pace,
        # 無氧訓練效果（Anaerobic Training Effect）：與既有 aerobic_te 成對，
        # 是間歇課品質判讀的核心（見 schema.sql 註解）。
        "anaerobic_te": raw.get("anaerobicTrainingEffect"),
        # 單場運動負荷（Activity Training Load）：累加成 acute load 的來源。
        "activity_training_load": raw.get("activityTrainingLoad"),
        # 訓練效果標籤（如 AEROBIC_BASE／TEMPO／VO2MAX）。
        "training_effect_label": raw.get("trainingEffectLabel"),
        "avg_grade_adj_pace_sec_per_km": avg_grade_adj_pace,
        # --- 跑步動態（Running Dynamics，需錶端支援，實測覆蓋率約 72%）---
        "avg_ground_contact_time_ms": raw.get("avgGroundContactTime"),
        "avg_vertical_oscillation_cm": raw.get("avgVerticalOscillation"),
        "avg_vertical_ratio_pct": raw.get("avgVerticalRatio"),
        "avg_stride_length_cm": raw.get("avgStrideLength"),
        # --- 跑步功率（Running Power，瓦特）---
        "avg_power_watts": raw.get("avgPower"),
        "max_power_watts": raw.get("maxPower"),
        "norm_power_watts": raw.get("normPower"),
        # --- 環境溫度：手腕體溫污染值，照存但不可當氣溫用，見 schema.sql 註解 ---
        "min_temperature_c": raw.get("minTemperature"),
        "max_temperature_c": raw.get("maxTemperature"),
        "hr_time_in_zone_json": _build_hr_time_in_zone_json(raw),
        # API 已是公尺，不需換算（匯出包才是公分）
        "elevation_gain_m": raw.get("elevationGain"),
        "elevation_loss_m": raw.get("elevationLoss"),
        # API 已是大卡，不需 kj_to_kcal（匯出包才是千焦耳）
        "calories": _round_or_none(raw.get("calories")),
        "raw_data_json": json.dumps(raw, ensure_ascii=False),
    }


def normalize_laps(lap_dtos: list[dict]) -> list[dict]:
    """把 API 的 `lapDTOs` 轉成 `activity_laps` 表的欄位。

    ⚠️ 與活動摘要同樣是公尺／秒（非匯出包的公分／毫秒）。
    欄位名稱刻意對齊 `fit_parser.parse_fit()` 的分圈輸出，讓兩條路徑
    都能餵進同一個 `classify_workout()` 與 `store_laps()`。
    """
    rows = []
    for dto in lap_dtos:
        distance_km = (dto.get("distance") or 0) / 1000
        duration_sec = dto.get("duration")
        pace = (
            round(duration_sec / distance_km)
            if duration_sec and distance_km > 0
            else None
        )
        rows.append(
            {
                "lap_index": dto.get("lapIndex"),
                "distance_km": distance_km,
                "duration_sec": duration_sec,
                "pace_sec_per_km": pace,
                "avg_hr_bpm": _round_or_none(dto.get("averageHR")),
                "max_hr_bpm": _round_or_none(dto.get("maxHR")),
            }
        )
    return rows


def normalize_detail_records(raw: dict | None) -> list[dict]:
    """把 `get_activity_details` 的逐點序列轉成 `activity_records` 的欄位（N-32）。

    欄位名稱對齊 `fit_parser.parse_fit()` 的 records，讓兩條路徑共用
    `downsample_records()` 與 `store_records()`。回傳的是 API 原始密度，
    呼叫端負責降採樣。

    ⚠️ 每點的欄位位置以 `metricDescriptors[].metricsIndex` 對應——實測兩次呼叫
    同一場活動，描述的順序不同。

    2026-10-24 以同一場活動對照 FIT 逐秒資料，在 600／1200 秒兩點完全一致：
    `sumElapsedDuration` 秒（含暫停，同 FIT 的 timestamp 差）、`sumDistance`
    公尺、`directSpeed` m/s、`directRunCadence` 單腳步頻（同 FIT `cadence`；
    `directDoubleCadence` 為雙腳）、`directElevation` 公尺、經緯度已是度數。
    """
    if not raw:
        return []
    index = {
        d.get("key"): d.get("metricsIndex") for d in raw.get("metricDescriptors") or []
    }
    if index.get("sumElapsedDuration") is None:
        return []

    def pick(values: list, key: str):
        i = index.get(key)
        return values[i] if i is not None and i < len(values) else None

    records = []
    for point in raw.get("activityDetailMetrics") or []:
        values = point.get("metrics") or []
        elapsed = pick(values, "sumElapsedDuration")
        if elapsed is None:
            continue
        distance_m = pick(values, "sumDistance")
        lat = pick(values, "directLatitude")
        lon = pick(values, "directLongitude")
        records.append(
            {
                "elapsed_sec": int(elapsed),
                "distance_km": round(distance_m / 1000, 4) if distance_m is not None else None,
                "hr_bpm": _round_or_none(pick(values, "directHeartRate")),
                "pace_sec_per_km": mps_to_pace_sec_per_km(pick(values, "directSpeed")),
                "cadence_spm": _round_or_none(pick(values, "directRunCadence")),
                "altitude_m": pick(values, "directElevation"),
                "lat": round(lat, COORD_DECIMALS) if lat is not None else None,
                "lon": round(lon, COORD_DECIMALS) if lon is not None else None,
            }
        )
    return records


# Training Readiness 快照選取（缺陷 C）：課表決策要問的是「今天能不能練」，
# 而非「恢復得如何」，故取起床時的那筆而非睡前最後一筆。
_READINESS_PREFERRED_CONTEXT = "AFTER_WAKEUP_RESET"


def _select_readiness_snapshot(readiness_list: list[dict]) -> dict:
    """依缺陷 C 的決策，從單日多筆 Training Readiness 快照中選出起床那筆。

    該端點每日回傳多筆快照（實測 2–4 筆），分數在一天內可劇烈變動
    （實測 2026-08-31：起床 60 分、睡前 75 分）。本專案取
    `inputContext == 'AFTER_WAKEUP_RESET'` 的那筆，因為教練決策要問的是
    「今天能不能練」而非「恢復得如何」。

    ⚠️ 沒有該 context 的日子**不靜默取錯**：退回沿用修正前的既有行為
    （取 `[0]`，實測確認為時間戳最新的一筆），但無論退回與否，實際採用的
    `inputContext`／`timestampLocal` 一律存進 `readiness_input_context`／
    `readiness_timestamp_local` 兩欄，讓下游能判斷「這天沒有起床快照，
    這個分數代表的是別的時點」，而不是誤以為每天都是同一種語意。
    此頻率（多常沒有起床快照）待本機對真實帳號驗證確認。
    """
    if not readiness_list:
        return {}
    for entry in readiness_list:
        if entry.get("inputContext") == _READINESS_PREFERRED_CONTEXT:
            return entry
    return readiness_list[0]


def normalize_wellness(raw: dict) -> dict | None:
    """把單日各來源的原始回傳彙整成 `daily_wellness` 表的欄位。

    ⚠️ Training Readiness／Body Battery／HRV status 為 Garmin 黑盒衍生值，
    照存但取用時須帶 `garmin_blackbox` 標記。

    ⚠️ `acwr` 欄位存的是 `acwrFactorPercent`（0–100 貢獻度分數），
    **不是**古典的 0.8–1.3 急慢性負荷比——沿用既有欄位語意。真正的 ACWR
    （急慢性負荷比）改存於 `training_status.acwr_ratio`（見
    `normalize_training_status()`），依決策視為可判讀的累加量而非黑盒。

    全日皆無資料時回傳 None（不寫入空列）。
    """
    stats = raw.get("stats") or {}
    sleep = raw.get("sleep") or {}
    hrv = raw.get("hrv") or {}
    readiness_list = raw.get("training_readiness") or []
    readiness = _select_readiness_snapshot(readiness_list)

    sleep_dto = sleep.get("dailySleepDTO") or {}
    hrv_summary = hrv.get("hrvSummary") or {}
    hrv_baseline = hrv_summary.get("baseline") or {}

    row = {
        "date": raw["date"],
        "resting_hr_bpm": stats.get("restingHeartRate"),
        "hrv_ms": hrv_summary.get("lastNightAvg"),
        "hrv_weekly_avg_ms": hrv_summary.get("weeklyAvg"),
        "spo2_pct": stats.get("averageSpo2"),
        "skin_temp_c": None,  # API 未於此端點提供
        "respiration_rate": stats.get("avgWakingRespirationValue"),
        "sleep_duration_sec": _as_int(sleep_dto.get("sleepTimeSeconds")),
        # HRV status（BALANCED/UNBALANCED/LOW）是黑盒評級，沿用 sleep_quality
        # 以外的欄位無處可放，故不寫入；保留於 raw 供日後需要時取用。
        "sleep_quality": None,
        "sleep_score": (sleep_dto.get("sleepScores") or {})
        .get("overall", {})
        .get("value"),
        # ⚠️ 缺陷 A 修正：stress_avg 為**睡眠期間**平均壓力
        # （sleepData.dailySleepDTO.avgSleepStress），與 all_day_stress_avg
        # 的**全天**平均壓力（stats.averageStressLevel）範疇不同，
        # 修正前兩欄被誤寫入同一個值（見 schema.sql 註解與審計報告缺陷 A）。
        "stress_avg": sleep_dto.get("avgSleepStress"),
        "all_day_stress_avg": stats.get("averageStressLevel"),
        "body_battery_max": stats.get("bodyBatteryHighestValue"),
        "body_battery_min": stats.get("bodyBatteryLowestValue"),
        "steps": stats.get("totalSteps"),
        "training_readiness_score": readiness.get("score"),
        "recovery_time_hours": (
            readiness["recoveryTime"] / _SECONDS_PER_MINUTE
            if readiness.get("recoveryTime") is not None
            else None
        ),
        "acwr": readiness.get("acwrFactorPercent"),
        # --- HRV 個人基準線（三值）：單一讀數無意義，須對照基準線 ---
        "hrv_baseline_low_upper": hrv_baseline.get("lowUpper"),
        "hrv_baseline_balanced_low": hrv_baseline.get("balancedLow"),
        "hrv_baseline_balanced_upper": hrv_baseline.get("balancedUpper"),
        "hrv_status": hrv_summary.get("status"),
        "hrv_last_night_5min_high": hrv_summary.get("lastNight5MinHigh"),
        # 短期訓練負荷（Acute Load）：零額外 API 成本，已在 readiness payload 內。
        "acute_load": readiness.get("acuteLoad"),
        "rhr_7day_avg_bpm": _round_or_none(stats.get("lastSevenDaysAvgRestingHeartRate")),
        # --- 睡眠階段拆解（秒）---
        "sleep_deep_sec": _as_int(sleep_dto.get("deepSleepSeconds")),
        "sleep_light_sec": _as_int(sleep_dto.get("lightSleepSeconds")),
        "sleep_rem_sec": _as_int(sleep_dto.get("remSleepSeconds")),
        "sleep_awake_sec": _as_int(sleep_dto.get("awakeSleepSeconds")),
        "body_battery_at_wake": stats.get("bodyBatteryAtWakeTime"),
        # --- Training Readiness 快照的時點資訊（缺陷 C）---
        "readiness_input_context": readiness.get("inputContext"),
        "readiness_timestamp_local": readiness.get("timestampLocal"),
        # --- 2026-10-22：Garmin 網頁卡片所需欄位（皆在既有 payload 內）---
        "readiness_level": readiness.get("level"),
        "readiness_feedback_short": readiness.get("feedbackShort"),
        # --- 2026-10-23：訓練完備程度的六個因素評級（皆在既有 payload 內，零額外請求）---
        "readiness_sleep_feedback": readiness.get("sleepScoreFactorFeedback"),
        "readiness_recovery_feedback": readiness.get("recoveryTimeFactorFeedback"),
        "readiness_hrv_feedback": readiness.get("hrvFactorFeedback"),
        "readiness_load_feedback": readiness.get("acwrFactorFeedback"),
        "readiness_sleep_history_feedback": readiness.get("sleepHistoryFactorFeedback"),
        "readiness_stress_feedback": readiness.get("stressHistoryFactorFeedback"),
        "sleep_start_local": _local_ms_to_iso(sleep_dto.get("sleepStartTimestampLocal")),
        "sleep_end_local": _local_ms_to_iso(sleep_dto.get("sleepEndTimestampLocal")),
        "respiration_sleep_avg": sleep_dto.get("averageRespirationValue"),
        "respiration_low": stats.get("lowestRespirationValue"),
        "respiration_high": stats.get("highestRespirationValue"),
        "spo2_lowest": stats.get("lowestSpo2"),
        "spo2_latest": stats.get("latestSpo2"),
        "body_battery_latest": stats.get("bodyBatteryMostRecentValue"),
        "body_battery_charged": stats.get("bodyBatteryChargedValue"),
        "body_battery_drained": stats.get("bodyBatteryDrainedValue"),
        "calories_active": _round_or_none(stats.get("activeKilocalories")),
        "calories_resting": _round_or_none(stats.get("bmrKilocalories")),
        "intensity_minutes_moderate": stats.get("moderateIntensityMinutes"),
        "intensity_minutes_vigorous": stats.get("vigorousIntensityMinutes"),
        "intensity_minutes_weekly_goal": stats.get("intensityMinutesGoal"),
        "floors_ascended": stats.get("floorsAscended"),
        "floors_goal": _round_or_none(stats.get("userFloorsAscendedGoal")),
        "steps_goal": stats.get("dailyStepGoal"),
    }
    # 除了日期以外全為 None 代表該日無任何資料，不寫入空列。
    if all(v is None for k, v in row.items() if k != "date"):
        return None
    return row


def _local_ms_to_iso(ms: float | None) -> str | None:
    """Garmin 的 `*TimestampLocal` 是「把當地牆上時間當成 UTC」的毫秒數，
    以 UTC 解讀即得當地時間，不可再套時區。"""
    if ms is None:
        return None
    return (
        datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)
        .replace(tzinfo=None)
        .isoformat(timespec="seconds")
    )


def _descriptor_index(descriptors: list[dict] | None, key: str, default: int) -> int:
    """依 payload 附的欄位描述找出某欄在每個點裡的位置，找不到就用實測的預設位置。

    壓力與心率的描述用 `index`／`key`，身體能量用
    `bodyBatteryValueDescriptorIndex`／`bodyBatteryValueDescriptorKey`。
    """
    for d in descriptors or []:
        name = d.get("key", d.get("bodyBatteryValueDescriptorKey"))
        if name == key:
            return d.get("index", d.get("bodyBatteryValueDescriptorIndex", default))
    return default


def _intraday_points(payload: dict, array_key: str, descriptors_key: str,
                     value_key: str, value_default_index: int) -> list | None:
    """把一支日內端點的序列轉成 `[[當日第幾分鐘, 數值], ...]`（當地時間）。

    時間戳是 GMT 毫秒；用 payload 自帶的 `startTimestampLocal − startTimestampGMT`
    換算當地時間，不寫死時區。負值（Garmin 的「無資料／活動中」）存成 None。
    """
    values = payload.get(array_key) or []
    start_gmt = payload.get("startTimestampGMT")
    start_local = payload.get("startTimestampLocal")
    if not values or not start_gmt or not start_local:
        return None
    gmt = datetime.datetime.fromisoformat(start_gmt[:19])
    local = datetime.datetime.fromisoformat(start_local[:19])
    offset_ms = (local - gmt).total_seconds() * 1000
    local_midnight = local.replace(hour=0, minute=0, second=0)
    ts_i = _descriptor_index(payload.get(descriptors_key), "timestamp", 0)
    val_i = _descriptor_index(payload.get(descriptors_key), value_key, value_default_index)
    points = []
    for p in values:
        if not p or len(p) <= max(ts_i, val_i) or p[ts_i] is None:
            continue
        t = datetime.datetime.fromtimestamp((p[ts_i] + offset_ms) / 1000, tz=datetime.timezone.utc)
        minute = int((t.replace(tzinfo=None) - local_midnight).total_seconds() // 60)
        v = p[val_i]
        points.append([minute, v if v is not None and v >= 0 else None])
    return points or None


def _sleep_level_segments(sleep: dict) -> list | None:
    """睡眠階段時間軸：`[["HH:MM", "HH:MM", 階段], ...]`（當地時間）。"""
    levels = sleep.get("sleepLevels") or []
    dto = sleep.get("dailySleepDTO") or {}
    local_ms = dto.get("sleepStartTimestampLocal")
    gmt_ms = dto.get("sleepStartTimestampGMT")
    if not levels or local_ms is None or gmt_ms is None:
        return None
    offset = datetime.timedelta(milliseconds=local_ms - gmt_ms)
    segments = []
    for lv in levels:
        try:
            start = datetime.datetime.fromisoformat(lv["startGMT"][:19]) + offset
            end = datetime.datetime.fromisoformat(lv["endGMT"][:19]) + offset
        except (KeyError, TypeError, ValueError):
            continue
        segments.append([start.strftime("%H:%M"), end.strftime("%H:%M"), int(lv.get("activityLevel") or 0)])
    return segments or None


def normalize_intraday(raw: dict) -> list[dict]:
    """把單日原始回傳中的日內序列轉成 `wellness_intraday` 的列（每種一列）。

    格式見 schema.sql 的 wellness_intraday 註解。沒有資料的種類不產生列。
    """
    stress = raw.get("stress_intraday") or {}
    heart = raw.get("heart_rate_intraday") or {}
    series = {
        "sleep_levels": _sleep_level_segments(raw.get("sleep") or {}),
        "stress": _intraday_points(
            stress, "stressValuesArray", "stressValueDescriptorsDTOList", "stressLevel", 1),
        "body_battery": _intraday_points(
            stress, "bodyBatteryValuesArray", "bodyBatteryValueDescriptorsDTOList",
            "bodyBatteryLevel", 2),
        "heart_rate": _intraday_points(
            heart, "heartRateValues", "heartRateValueDescriptors", "heartrate", 1),
    }
    return [
        {"date": raw["date"], "kind": kind, "points_json": json.dumps(points, separators=(",", ":"))}
        for kind, points in series.items()
        if points
    ]


def normalize_health_status(raw: dict) -> list[dict]:
    """把健康狀態回傳轉成 `health_status` 表的列（一個指標一列）。

    手錶不支援的指標（皮膚溫度等）回傳 status=UNKNOWN、value=None，不產生列。
    """
    payload = raw.get("health_status") or {}
    rows = []
    for m in payload.get("metrics") or []:
        if m.get("value") is None or not m.get("type"):
            continue
        rows.append({
            "date": raw["date"],
            "metric": m["type"],
            "value": m.get("value"),
            "baseline_lower": m.get("baselineLowerLimit"),
            "baseline_upper": m.get("baselineUpperLimit"),
            "status": m.get("status"),
            "percentage": m.get("percentage"),
        })
    return rows


def _select_primary_device(device_map: dict | None) -> dict:
    """從以 deviceId 字串為 key 的 dict 中選出主力錶（`primaryTrainingDevice=True`）。

    ⚠️ `get_training_status()` 的 `latestTrainingStatusData` 與
    `mostRecentTrainingLoadBalance.metricsTrainingLoadBalanceDTOMap` 皆以
    **deviceId 字串**為 key（非固定名稱），不可寫死 device id，也不可直接
    取第一個 key——換錶或多錶並用時會取錯（見審計報告 §2.1「結構陷阱」）。

    找不到任何標記 primary 的裝置時（理論上不應發生，但單錶使用者的
    payload 也可能不帶這個旗標）退回取第一筆，而非整批丟棄——這不是
    靜默取錯：只有單一裝置時「第一筆」就是唯一解，多裝置又缺旗標的
    情況待本機對真實帳號驗證後再評估是否需要更保守的處理。
    """
    if not device_map:
        return {}
    for entry in device_map.values():
        if isinstance(entry, dict) and entry.get("primaryTrainingDevice") is True:
            return entry
    first = next(iter(device_map.values()), None)
    return first if isinstance(first, dict) else {}


def normalize_training_status(date_str: str, raw: dict | None) -> dict | None:
    """把 `get_training_status()` 的原始回傳轉成 `training_status` 表的欄位。

    一支端點同時帶回真 ACWR（急慢性負荷比）、急/慢性負荷、最佳負荷範圍、
    VO2Max 精確值、熱適應——這些是可解釋的累加量（依決策視為可判讀，
    非黑盒對照組），與 `daily_wellness.acwr`（Garmin 的 0–100 貢獻度分數，
    語意不同）分開存表，理由見 schema.sql 表註解。

    ## 巢狀路徑（2026-09-02 對真實帳號 dump 驗證）

    回傳有三個平行的頂層區塊，deviceId-keyed 的 map 各自埋在自己的區塊下：

    - `mostRecentTrainingStatus.latestTrainingStatusData.<deviceId>`
      ——訓練狀態碼、fitnessTrend，及巢狀的 `acuteTrainingLoadDTO`（真 ACWR）
    - `mostRecentTrainingLoadBalance.metricsTrainingLoadBalanceDTOMap.<deviceId>`
      ——月負荷三分類與平衡回饋
    - `mostRecentVO2Max.generic`／`.heatAltitudeAcclimation`
      ——VO2Max 與熱／高度適應，**非**裝置維度，不經 `_select_primary_device()`

    裝置維度的兩個 map 一律經 `_select_primary_device()` 挑
    `primaryTrainingDevice=True`，見其 docstring。

    `raw` 為 None 或空 dict 時回傳 None（不寫入空列）。
    """
    if not raw:
        return None

    device = _select_primary_device(
        (raw.get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData")
    )
    load_balance = _select_primary_device(
        (raw.get("mostRecentTrainingLoadBalance") or {}).get(
            "metricsTrainingLoadBalanceDTOMap"
        )
    )
    acute_dto = device.get("acuteTrainingLoadDTO") or {}
    vo2max_block = raw.get("mostRecentVO2Max") or {}
    heat_dto = vo2max_block.get("heatAltitudeAcclimation") or {}
    vo2max = vo2max_block.get("generic") or {}

    row = {
        "date": date_str,
        "training_status_code": device.get("trainingStatus"),
        "training_status_phrase": device.get("trainingStatusFeedbackPhrase"),
        # Garmin 判定目前狀態的起始日（網頁「自 10月9日」）。不可由逐日狀態
        # 自行回推：實測 2026-10-22 逐日資料自 10/4 起即為 PRODUCTIVE，
        # Garmin 卻寫自 10/9，兩者規則不同。
        "status_since_date": device.get("sinceDate"),
        "acute_load": acute_dto.get("dailyTrainingLoadAcute"),
        "chronic_load": acute_dto.get("dailyTrainingLoadChronic"),
        "acwr_ratio": acute_dto.get("dailyAcuteChronicWorkloadRatio"),
        "chronic_load_optimal_min": acute_dto.get("minTrainingLoadChronic"),
        "chronic_load_optimal_max": acute_dto.get("maxTrainingLoadChronic"),
        "acwr_status": acute_dto.get("acwrStatus"),
        "vo2max_precise": vo2max.get("vo2MaxPreciseValue"),
        "vo2max_date": vo2max.get("calendarDate"),
        "heat_acclimation_pct": heat_dto.get("heatAcclimationPercentage"),
        "heat_trend": heat_dto.get("heatTrend"),
        "altitude_acclimation_pct": heat_dto.get("altitudeAcclimation"),
        "fitness_trend": device.get("fitnessTrend"),
        "monthly_load_aerobic_low": load_balance.get("monthlyLoadAerobicLow"),
        "monthly_load_aerobic_high": load_balance.get("monthlyLoadAerobicHigh"),
        "monthly_load_anaerobic": load_balance.get("monthlyLoadAnaerobic"),
        # 負荷焦點的目標範圍（2026-10-22 實測 key 名稱）
        "monthly_load_aerobic_low_target_min": load_balance.get("monthlyLoadAerobicLowTargetMin"),
        "monthly_load_aerobic_low_target_max": load_balance.get("monthlyLoadAerobicLowTargetMax"),
        "monthly_load_aerobic_high_target_min": load_balance.get("monthlyLoadAerobicHighTargetMin"),
        "monthly_load_aerobic_high_target_max": load_balance.get("monthlyLoadAerobicHighTargetMax"),
        "monthly_load_anaerobic_target_min": load_balance.get("monthlyLoadAnaerobicTargetMin"),
        "monthly_load_anaerobic_target_max": load_balance.get("monthlyLoadAnaerobicTargetMax"),
        "training_balance_feedback": load_balance.get("trainingBalanceFeedbackPhrase"),
    }
    if all(v is None for k, v in row.items() if k != "date"):
        return None
    return row


def normalize_performance_metrics(date_str: str, raw: dict) -> dict | None:
    """把賽事預測／乳酸閾值／耐力分數／體能年齡／爬坡分數彙整成
    `performance_metrics` 表的欄位。

    這些端點語意上是「當前值快照」而非逐日序列（見 schema.sql 表註解），
    `date_str` 由呼叫端傳入同步當下的日期，不是從回傳內容推導。

    ## 巢狀路徑（2026-09-02 對真實帳號 dump 全數驗證）

    - 賽事預測：平坦結構，`time5K`／`time10K`／`timeHalfMarathon`／
      `timeMarathon`，單位為秒
    - 乳酸閾值：**兩個子區塊** `speed_and_heart_rate`（`speed`／`heartRate`）
      與 `power`（`functionalThresholdPower`），非平坦結構
    - 耐力分數／爬坡分數：平坦，各取 `overallScore`
    - 體能年齡：平坦，`fitnessAge`／`chronologicalAge`／`achievableFitnessAge`

    `raw` 為 `garmin_api_client.fetch_performance_metrics_raw()` 的回傳，
    五項獨立容錯，全部為 None／空 dict 時回傳 None（不寫入空列）。
    """
    race = raw.get("race_predictions") or {}
    lactate = raw.get("lactate_threshold") or {}
    endurance = raw.get("endurance_score") or {}
    fitness_age = raw.get("fitness_age") or {}
    hill = raw.get("hill_score") or {}

    contributors = endurance.get("contributors")
    contributors_json = None
    if contributors:
        contributors_json = json.dumps(
            [
                {
                    "activity_type_id": c.get("activityTypeId"),
                    "group": c.get("group"),
                    "contribution": c.get("contribution"),
                }
                for c in contributors
            ],
            ensure_ascii=False,
        )

    # 乳酸閾值回傳為兩個子區塊，非平坦結構（2026-09-02 對真實帳號驗證）。
    lactate_shr = lactate.get("speed_and_heart_rate") or {}
    lactate_power = lactate.get("power") or {}

    # ⚠️ 此處 `speed` 的單位是**10 公尺/秒**（不是 activities 慣用的公尺/秒），
    # 秒/公里 = 100 / speed。
    # 2026-10-22 以 Garmin Connect 網頁為準更正：speed=0.3583 → 279 秒/km
    # （4:39/km），與網頁「跑步乳酸閾值 4:39 /公里」一致。原本的 speed×1000
    # （358 秒、5:58/km）是拿輕鬆跑配速比對推出的單位，推錯了——閾值配速
    # 本來就該比 avgHR 150 多的輕鬆跑快得多。
    lactate_speed = lactate_shr.get("speed")
    lactate_pace = round(100 / lactate_speed) if lactate_speed else None

    row = {
        "date": date_str,
        "race_predict_5k_sec": race.get("time5K"),
        "race_predict_10k_sec": race.get("time10K"),
        "race_predict_half_sec": race.get("timeHalfMarathon"),
        "race_predict_marathon_sec": race.get("timeMarathon"),
        # 實測 2026-09-02 為正確拼字 `heartRate`；仍保留 `hearRate` 後備，
        # 因 Garmin 該欄位歷史上曾缺一個 t，不同帳號／韌體可能仍回舊拼字。
        "lactate_threshold_hr_bpm": _round_or_none(
            lactate_shr.get("heartRate") if lactate_shr.get("heartRate") is not None
            else lactate_shr.get("hearRate")
        ),
        "lactate_threshold_pace_sec_per_km": lactate_pace,
        "functional_threshold_power": _round_or_none(
            lactate_power.get("functionalThresholdPower")
        ),
        "endurance_score": _round_or_none(endurance.get("overallScore")),
        "endurance_classification": endurance.get("classification"),
        "endurance_contributors_json": contributors_json,
        "fitness_age": fitness_age.get("fitnessAge"),
        "chronological_age": fitness_age.get("chronologicalAge"),
        "achievable_fitness_age": fitness_age.get("achievableFitnessAge"),
        "hill_score": _round_or_none(hill.get("overallScore")),
    }
    if all(v is None for k, v in row.items() if k != "date"):
        return None
    return row


def _upsert_activities(conn: sqlite3.Connection, athlete_id: int, rows: list[dict]) -> int:
    """寫入活動。沿用 garmin_import_runner 的 ON CONFLICT 慣例。

    ⚠️ ON CONFLICT 刻意**不更新** `workout_type` / `workout_type_source`
    ——分類結果（尤其人工標註的 manual）不可被同步覆蓋。

    ⚠️ 去重以 **`external_id`**（Garmin activityId）為準，不用
    `(started_at, activity_type, distance_km)`：API 回傳完整浮點距離
    （10.1585703125），匯出包存四捨五入值（10.1586），兩者不相等會讓
    同一場活動寫成兩列——2026-09-01 實測確實發生（8/13、8/16 兩場，
    正落在 API 與匯出包的來源重疊區）。`external_id` 為穩定唯一鍵，
    實測 530 筆活動全部非 NULL。
    """
    now = _utc_now_iso()
    count = 0
    for row in rows:
        # 先以 external_id 找既有列——它可能是匯出包寫入的（距離精度不同，
        # 光靠 UNIQUE(started_at, ..., distance_km) 撞不到）。
        existing = None
        if row["external_id"]:
            existing = conn.execute(
                "SELECT id FROM activities WHERE athlete_id = ? AND external_id = ?",
                (athlete_id, row["external_id"]),
            ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE activities SET
                    title=?, duration_sec=?, moving_time_sec=?, avg_hr_bpm=?, max_hr_bpm=?,
                    aerobic_te=?, avg_cadence_spm=?, max_cadence_spm=?,
                    avg_pace_sec_per_km=?, best_pace_sec_per_km=?, elevation_gain_m=?,
                    elevation_loss_m=?, calories=?, raw_data_json=?, fetched_at=?,
                    source_version=?, anaerobic_te=?, activity_training_load=?,
                    training_effect_label=?, avg_grade_adj_pace_sec_per_km=?,
                    avg_ground_contact_time_ms=?, avg_vertical_oscillation_cm=?,
                    avg_vertical_ratio_pct=?, avg_stride_length_cm=?, avg_power_watts=?,
                    max_power_watts=?, norm_power_watts=?, min_temperature_c=?,
                    max_temperature_c=?, hr_time_in_zone_json=?
                WHERE id = ?
                """,
                (
                    row["title"], row["duration_sec"], row["moving_time_sec"],
                    row["avg_hr_bpm"], row["max_hr_bpm"], row["aerobic_te"],
                    row["avg_cadence_spm"], row["max_cadence_spm"],
                    row["avg_pace_sec_per_km"], row["best_pace_sec_per_km"],
                    row["elevation_gain_m"], row["elevation_loss_m"], row["calories"],
                    row["raw_data_json"], now, SYNC_SOURCE_VERSION,
                    row["anaerobic_te"], row["activity_training_load"],
                    row["training_effect_label"], row["avg_grade_adj_pace_sec_per_km"],
                    row["avg_ground_contact_time_ms"], row["avg_vertical_oscillation_cm"],
                    row["avg_vertical_ratio_pct"], row["avg_stride_length_cm"],
                    row["avg_power_watts"], row["max_power_watts"], row["norm_power_watts"],
                    row["min_temperature_c"], row["max_temperature_c"],
                    row["hr_time_in_zone_json"],
                    existing["id"],
                ),
            )
            count += 1
            continue

        conn.execute(
            """
            INSERT INTO activities (
                athlete_id, external_id, activity_type, title, started_at,
                distance_km, duration_sec, moving_time_sec, avg_hr_bpm, max_hr_bpm,
                aerobic_te, avg_cadence_spm, max_cadence_spm, avg_pace_sec_per_km,
                best_pace_sec_per_km, elevation_gain_m, elevation_loss_m, calories,
                source, source_version, fetched_at, has_wellness_data, raw_data_json,
                anaerobic_te, activity_training_load, training_effect_label,
                avg_grade_adj_pace_sec_per_km, avg_ground_contact_time_ms,
                avg_vertical_oscillation_cm, avg_vertical_ratio_pct, avg_stride_length_cm,
                avg_power_watts, max_power_watts, norm_power_watts,
                min_temperature_c, max_temperature_c, hr_time_in_zone_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT (athlete_id, started_at, activity_type, distance_km) DO UPDATE SET
                title=excluded.title, duration_sec=excluded.duration_sec,
                moving_time_sec=excluded.moving_time_sec, avg_hr_bpm=excluded.avg_hr_bpm,
                max_hr_bpm=excluded.max_hr_bpm, aerobic_te=excluded.aerobic_te,
                avg_cadence_spm=excluded.avg_cadence_spm,
                max_cadence_spm=excluded.max_cadence_spm,
                avg_pace_sec_per_km=excluded.avg_pace_sec_per_km,
                best_pace_sec_per_km=excluded.best_pace_sec_per_km,
                elevation_gain_m=excluded.elevation_gain_m,
                elevation_loss_m=excluded.elevation_loss_m,
                calories=excluded.calories, raw_data_json=excluded.raw_data_json,
                fetched_at=excluded.fetched_at, source_version=excluded.source_version,
                anaerobic_te=excluded.anaerobic_te,
                activity_training_load=excluded.activity_training_load,
                training_effect_label=excluded.training_effect_label,
                avg_grade_adj_pace_sec_per_km=excluded.avg_grade_adj_pace_sec_per_km,
                avg_ground_contact_time_ms=excluded.avg_ground_contact_time_ms,
                avg_vertical_oscillation_cm=excluded.avg_vertical_oscillation_cm,
                avg_vertical_ratio_pct=excluded.avg_vertical_ratio_pct,
                avg_stride_length_cm=excluded.avg_stride_length_cm,
                avg_power_watts=excluded.avg_power_watts,
                max_power_watts=excluded.max_power_watts,
                norm_power_watts=excluded.norm_power_watts,
                min_temperature_c=excluded.min_temperature_c,
                max_temperature_c=excluded.max_temperature_c,
                hr_time_in_zone_json=excluded.hr_time_in_zone_json
            """,
            (
                athlete_id,
                row["external_id"],
                row["activity_type"],
                row["title"],
                row["started_at"],
                row["distance_km"],
                row["duration_sec"],
                row["moving_time_sec"],
                row["avg_hr_bpm"],
                row["max_hr_bpm"],
                row["aerobic_te"],
                row["avg_cadence_spm"],
                row["max_cadence_spm"],
                row["avg_pace_sec_per_km"],
                row["best_pace_sec_per_km"],
                row["elevation_gain_m"],
                row["elevation_loss_m"],
                row["calories"],
                SYNC_SOURCE,
                SYNC_SOURCE_VERSION,
                now,
                0,
                row["raw_data_json"],
                row["anaerobic_te"],
                row["activity_training_load"],
                row["training_effect_label"],
                row["avg_grade_adj_pace_sec_per_km"],
                row["avg_ground_contact_time_ms"],
                row["avg_vertical_oscillation_cm"],
                row["avg_vertical_ratio_pct"],
                row["avg_stride_length_cm"],
                row["avg_power_watts"],
                row["max_power_watts"],
                row["norm_power_watts"],
                row["min_temperature_c"],
                row["max_temperature_c"],
                row["hr_time_in_zone_json"],
            ),
        )
        count += 1
    return count


def _upsert_daily_rows(conn: sqlite3.Connection, table: str, athlete_id: int, rows: list[dict]) -> int:
    """以 (athlete_id, date, source) 為鍵寫入每日一列的表；重抓時只補空值不覆寫
    （COALESCE，理由見 OVERLAP_DAYS）。

    欄位清單取自 normalize_*() 的輸出，INSERT 欄位、佔位符、COALESCE 子句
    與參數都由同一份清單產生——原本四處手寫，每加一欄就要改四個地方，
    對錯位置會讓值靜默寫進別的欄位。
    """
    if not rows:
        return 0
    now = _utc_now_iso()
    columns = [k for k in rows[0] if k != "date"]
    insert_cols = ", ".join(["athlete_id", "date", *columns, "source", "source_version", "fetched_at"])
    placeholders = ", ".join("?" * (len(columns) + 5))
    updates = ",\n".join(f"{c}=COALESCE(excluded.{c}, {c})" for c in columns)
    sql = f"""
        INSERT INTO {table} ({insert_cols}) VALUES ({placeholders})
        ON CONFLICT (athlete_id, date, source) DO UPDATE SET
            {updates},
            source_version=excluded.source_version,
            fetched_at=excluded.fetched_at
    """  # noqa: S608 — 表名與欄位名皆來自程式內常數與 normalize_*()，不含外部輸入
    for row in rows:
        conn.execute(
            sql,
            (athlete_id, row["date"], *(row[c] for c in columns),
             SYNC_SOURCE, SYNC_SOURCE_VERSION, now),
        )
    return len(rows)


def _upsert_wellness(conn: sqlite3.Connection, athlete_id: int, rows: list[dict]) -> int:
    return _upsert_daily_rows(conn, "daily_wellness", athlete_id, rows)


def _upsert_intraday(conn: sqlite3.Connection, athlete_id: int, rows: list[dict]) -> int:
    """寫入 wellness_intraday。與每日摘要不同，重抓時整串覆寫：
    當天稍早抓到的序列只到同步當下，晚點重抓的才完整。"""
    now = _utc_now_iso()
    for row in rows:
        conn.execute(
            """
            INSERT INTO wellness_intraday (
                athlete_id, date, kind, points_json, source, source_version, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (athlete_id, date, kind, source) DO UPDATE SET
                points_json=excluded.points_json,
                source_version=excluded.source_version,
                fetched_at=excluded.fetched_at
            """,
            (athlete_id, row["date"], row["kind"], row["points_json"],
             SYNC_SOURCE, SYNC_SOURCE_VERSION, now),
        )
    return len(rows)


def _upsert_health_status(conn: sqlite3.Connection, athlete_id: int, rows: list[dict]) -> int:
    """寫入 health_status。整列覆寫：Garmin 會在睡眠資料補齊後更新當日判定，
    晚點重抓的才是最終值。"""
    now = _utc_now_iso()
    for row in rows:
        conn.execute(
            """
            INSERT INTO health_status (
                athlete_id, date, metric, value, baseline_lower, baseline_upper,
                status, percentage, source, source_version, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (athlete_id, date, metric, source) DO UPDATE SET
                value=excluded.value,
                baseline_lower=excluded.baseline_lower,
                baseline_upper=excluded.baseline_upper,
                status=excluded.status,
                percentage=excluded.percentage,
                source_version=excluded.source_version,
                fetched_at=excluded.fetched_at
            """,
            (athlete_id, row["date"], row["metric"], row["value"], row["baseline_lower"],
             row["baseline_upper"], row["status"], row["percentage"],
             SYNC_SOURCE, SYNC_SOURCE_VERSION, now),
        )
    return len(rows)


def _sync_athlete_sex(conn: sqlite3.Connection, athlete_id: int, api: GarminApiClient) -> None:
    """athlete_profile.sex 尚未取得時，向 Garmin 個人資料取一次（之後不再打）。

    抓取失敗不影響同步——只是 VO2max 暫時沒有分級。
    """
    row = conn.execute(
        "SELECT sex FROM athlete_profile WHERE id = ?", (athlete_id,)
    ).fetchone()
    if row is None or row["sex"]:
        return
    try:
        sex = api.fetch_user_sex()
    except GarminFetchError:
        return
    if sex:
        conn.execute("UPDATE athlete_profile SET sex = ? WHERE id = ?", (sex, athlete_id))


def normalize_hr_settings(raw_zones: list[dict] | None) -> dict | None:
    """從心率區間設定取出手錶採用的 HRmax 與靜止心率（N-26）。

    每個運動各一筆設定，優先取 `RUNNING`（跑步專屬區間），沒有則取
    `DEFAULT`。兩個值都缺時回傳 None。
    """
    by_sport = {z.get("sport"): z for z in (raw_zones or []) if isinstance(z, dict)}
    zone = by_sport.get("RUNNING") or by_sport.get("DEFAULT")
    if not zone:
        return None
    max_hr = _round_or_none(zone.get("maxHeartRateUsed"))
    resting_hr = _round_or_none(zone.get("restingHeartRateUsed"))
    if max_hr is None and resting_hr is None:
        return None
    return {"max_hr_bpm": max_hr, "resting_hr_bpm": resting_hr}


def _sync_hr_settings(
    conn: sqlite3.Connection, athlete_id: int, api: GarminApiClient
) -> list[dict]:
    """把 Garmin 心率區間設定的 HRmax／靜止心率寫回 athlete_profile（N-26）。

    HRmax 是負荷（TRIMP）、強度符合度、VDOT 心率換算、區間 bpm 的共同輸入，
    學員在錶上校正後系統必須跟上，否則會一直用舊值且毫無提示。

    - HRmax：`max_hr_source='measured'` 時**不覆寫**——實測值比手錶設定可信；
      其餘來源一律以 Garmin 值覆寫並把來源標為 `watch_display`。
    - 靜止心率：一律覆寫。Garmin 預設自動更新（`restingHrAutoUpdateUsed`），
      2026-05～10 間 Fu 由約 57 降到約 48，固定手動值會讓 %HRR 一路偏低。

    回傳實際有變動的欄位清單 `[{field, old, new}]`，供同步結果告知使用者
    「所有負荷已依新值重算」。抓取失敗不影響同步。
    """
    row = conn.execute(
        "SELECT max_hr_bpm, max_hr_source, resting_hr_bpm FROM athlete_profile WHERE id = ?",
        (athlete_id,),
    ).fetchone()
    if row is None:
        return []
    try:
        settings = normalize_hr_settings(api.fetch_heart_rate_zones())
    except GarminRateLimited:
        raise
    except GarminFetchError:
        return []
    if settings is None:
        return []

    changes: list[dict] = []
    new_max = settings["max_hr_bpm"]
    if new_max is not None and row["max_hr_source"] != "measured":
        if new_max != row["max_hr_bpm"]:
            changes.append({"field": "max_hr_bpm", "old": row["max_hr_bpm"], "new": new_max})
        conn.execute(
            "UPDATE athlete_profile SET max_hr_bpm = ?, max_hr_source = 'watch_display' WHERE id = ?",
            (new_max, athlete_id),
        )
    new_rest = settings["resting_hr_bpm"]
    if new_rest is not None and new_rest != row["resting_hr_bpm"]:
        changes.append({"field": "resting_hr_bpm", "old": row["resting_hr_bpm"], "new": new_rest})
        conn.execute(
            "UPDATE athlete_profile SET resting_hr_bpm = ? WHERE id = ?", (new_rest, athlete_id)
        )
    if changes:
        conn.execute(
            "UPDATE athlete_profile SET updated_at = ? WHERE id = ?", (_utc_now_iso(), athlete_id)
        )
    return changes


def _upsert_training_status(conn: sqlite3.Connection, athlete_id: int, rows: list[dict]) -> int:
    """寫入 `training_status`。沿用 daily_wellness 的 ON CONFLICT + COALESCE 慣例
    ——重複同步同一天只補空值，不覆寫已有值。
    """
    return _upsert_daily_rows(conn, "training_status", athlete_id, rows)


def _upsert_performance_metrics(
    conn: sqlite3.Connection, athlete_id: int, row: dict | None
) -> int:
    """寫入 `performance_metrics`。每次同步只有一筆（快照，非逐日序列），
    沿用同樣的 ON CONFLICT + COALESCE 慣例，讓同一天內重複同步不覆寫已有值。
    """
    if row is None:
        return 0
    now = _utc_now_iso()
    conn.execute(
        """
        INSERT INTO performance_metrics (
            athlete_id, date, race_predict_5k_sec, race_predict_10k_sec,
            race_predict_half_sec, race_predict_marathon_sec,
            lactate_threshold_hr_bpm, lactate_threshold_pace_sec_per_km,
            functional_threshold_power, endurance_score, endurance_classification,
            endurance_contributors_json, fitness_age, chronological_age,
            achievable_fitness_age, hill_score,
            source, source_version, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (athlete_id, date, source) DO UPDATE SET
            race_predict_5k_sec=COALESCE(excluded.race_predict_5k_sec, race_predict_5k_sec),
            race_predict_10k_sec=COALESCE(excluded.race_predict_10k_sec, race_predict_10k_sec),
            race_predict_half_sec=COALESCE(excluded.race_predict_half_sec, race_predict_half_sec),
            race_predict_marathon_sec=COALESCE(
                excluded.race_predict_marathon_sec, race_predict_marathon_sec),
            lactate_threshold_hr_bpm=COALESCE(
                excluded.lactate_threshold_hr_bpm, lactate_threshold_hr_bpm),
            lactate_threshold_pace_sec_per_km=COALESCE(
                excluded.lactate_threshold_pace_sec_per_km,
                lactate_threshold_pace_sec_per_km),
            functional_threshold_power=COALESCE(
                excluded.functional_threshold_power, functional_threshold_power),
            endurance_score=COALESCE(excluded.endurance_score, endurance_score),
            endurance_classification=COALESCE(
                excluded.endurance_classification, endurance_classification),
            endurance_contributors_json=COALESCE(
                excluded.endurance_contributors_json, endurance_contributors_json),
            fitness_age=COALESCE(excluded.fitness_age, fitness_age),
            chronological_age=COALESCE(excluded.chronological_age, chronological_age),
            achievable_fitness_age=COALESCE(
                excluded.achievable_fitness_age, achievable_fitness_age),
            hill_score=COALESCE(excluded.hill_score, hill_score),
            source_version=excluded.source_version,
            fetched_at=excluded.fetched_at
        """,
        (
            athlete_id,
            row["date"],
            row["race_predict_5k_sec"],
            row["race_predict_10k_sec"],
            row["race_predict_half_sec"],
            row["race_predict_marathon_sec"],
            row["lactate_threshold_hr_bpm"],
            row["lactate_threshold_pace_sec_per_km"],
            row["functional_threshold_power"],
            row["endurance_score"],
            row["endurance_classification"],
            row["endurance_contributors_json"],
            row["fitness_age"],
            row["chronological_age"],
            row["achievable_fitness_age"],
            row["hill_score"],
            SYNC_SOURCE,
            SYNC_SOURCE_VERSION,
            now,
        ),
    )
    return 1


def _refresh_metric_coverage(conn: sqlite3.Connection, athlete_id: int) -> int:
    """依 `daily_wellness` 現況重算各指標的可用日期範圍。

    ⚠️ 這一步不可省略：`metric_coverage` 是 `get_meta()` 回報「各指標有資料
    到哪一天」的依據，也影響下游對可評估性的判斷。同步寫入新的 wellness
    卻沒更新此表，會讓 AI 以為資料只到上次匯出那天（實測確認過這個落差）。

    直接從已寫入的資料反算（而非沿用匯入器的解析結果），因此對「同步」與
    「匯出匯入」兩條路徑都成立，也不需要複製匯入器的欄位對應邏輯。
    """
    now = _utc_now_iso()
    count = 0
    for metric in _WELLNESS_METRIC_COLUMNS:
        row = conn.execute(
            f"SELECT MIN(date) AS lo, MAX(date) AS hi FROM daily_wellness "  # noqa: S608
            f"WHERE athlete_id = ? AND {metric} IS NOT NULL",
            (athlete_id,),
        ).fetchone()
        if not row or not row["lo"]:
            continue
        conn.execute(
            """
            INSERT INTO metric_coverage (
                athlete_id, metric_name, source, earliest_date, latest_date, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (athlete_id, metric_name, source) DO UPDATE SET
                earliest_date = MIN(earliest_date, excluded.earliest_date),
                latest_date = MAX(latest_date, excluded.latest_date),
                updated_at = excluded.updated_at
            """,
            (athlete_id, metric, SYNC_SOURCE, row["lo"], row["hi"], now),
        )
        count += 1
    return count


def _latest_date(conn: sqlite3.Connection, athlete_id: int) -> datetime.date | None:
    """取 activities／daily_wellness 兩表最新日期中**較舊**的那個。

    ⚠️ 取較舊者而非較新者是刻意的：兩表天生不同步——沒運動的日子沒有
    活動列，wellness 卻天天都有。若以較新的 wellness 日期為斷點，當天
    稍晚才上傳的活動會永遠落在下次同步區間之外，形成永久缺口。

    兩表皆空時回傳 None（全新資料庫，尚未匯入歷史）。
    """
    row = conn.execute(
        "SELECT MAX(started_at) AS latest FROM activities WHERE athlete_id = ?",
        (athlete_id,),
    ).fetchone()
    # started_at 是 ISO 時間戳（含 T 與時分秒），取日期部分即可
    activity_latest = row["latest"][:10] if row and row["latest"] else None

    row = conn.execute(
        "SELECT MAX(date) AS latest FROM daily_wellness WHERE athlete_id = ?",
        (athlete_id,),
    ).fetchone()
    wellness_latest = row["latest"] if row and row["latest"] else None

    candidates = [d for d in (activity_latest, wellness_latest) if d]
    if not candidates:
        return None
    return datetime.date.fromisoformat(min(candidates))


def resolve_sync_days(
    conn: sqlite3.Connection,
    athlete_id: int,
    today: datetime.date | None = None,
) -> int:
    """依資料庫現況回推需要同步幾天，避免長期未同步造成資料遺漏。

    演算法：
        斷點 = min(活動最新日, wellness 最新日)   ← 較舊者，理由見 _latest_date
        天數 = (今天 − 斷點) + 1 + OVERLAP_DAYS
        天數 = min(天數, MAX_AUTO_SYNC_DAYS)      ← 保護 API

    查無資料時回傳 `DEFAULT_SYNC_DAYS`——全新資料庫不該推導出巨大區間，
    歷史資料本就該走官方匯出包（`garmin_import_runner`）。
    """
    end_date = today or datetime.date.today()
    breakpoint_date = _latest_date(conn, athlete_id)
    if breakpoint_date is None:
        return DEFAULT_SYNC_DAYS

    # 資料日期可能超前今天（時區差），此時 gap 視為 0，仍要抓 overlap。
    gap = max((end_date - breakpoint_date).days, 0)
    return min(gap + 1 + OVERLAP_DAYS, MAX_AUTO_SYNC_DAYS)


def estimate_sync_cost(days: int, expected_activities: int = 0) -> dict:
    """預估同步的 API 請求數與耗時，供呼叫端在真的打 API 前告知使用者。

    成本結構：活動只打 1 次（`get_activities_by_date` 吃日期區間），
    wellness 則**逐日**各打 `_WELLNESS_ENDPOINTS_PER_DAY`（8）個端點，
    故基本總數 = 1 + days × 8。
    另每場跑步**各多 2 次**（`get_activity_splits` 分圈、`get_activity_details`
    逐點資料）——`expected_activities` 即該筆數，未知時傳 0（僅低估這部分）。
    每次呼叫之間有 `REQUEST_INTERVAL_SEC` 的節流間隔。

    預估值只計節流時間，未計網路往返，實際耗時會略長於此。
    """
    request_count = 1 + days * _WELLNESS_ENDPOINTS_PER_DAY + expected_activities * 2
    estimated_seconds = request_count * REQUEST_INTERVAL_SEC
    minutes = int(estimated_seconds // _SECONDS_PER_MINUTE)
    seconds = int(estimated_seconds % _SECONDS_PER_MINUTE)
    duration_text = f"約 {minutes} 分 {seconds} 秒" if minutes else f"約 {seconds} 秒"
    return {
        "request_count": request_count,
        "estimated_seconds": estimated_seconds,
        "summary": f"{days} 天／{request_count} 次 API 請求／{duration_text}",
    }


def sync(
    conn: sqlite3.Connection,
    athlete_id: int,
    days: int | None = None,
    client: GarminApiClient | None = None,
    today: datetime.date | None = None,
    dry_run: bool = False,
    fetch_splits: bool = True,
) -> dict:
    """同步最近 `days` 天的活動與每日身體數據。

    `days` 省略時**自動依資料庫現況回推**（見 `resolve_sync_days`），
    這是預設行為——避免長期未同步時只補固定天數而漏掉中間的缺口。
    明確給值則照給的走，不受自動推導與 `MAX_AUTO_SYNC_DAYS` 影響。

    `dry_run=True` 只回報計畫與成本估算，不打 API、不寫入資料庫。

    冪等：重複執行同一區間不會產生重複列（由資料庫 UNIQUE 約束保證）。

    Returns:
        含 `available`／筆數／日期區間的結果 dict，並一律附上 `days`、
        `days_auto_resolved`、`gap_truncated`、`estimate` 供呼叫端告知
        使用者預計耗時。認證或抓取失敗時 `available=False` 並附 `reason`，
        不拋例外——供 MCP tool 直接回傳。
    """
    days_auto_resolved = days is None
    if days_auto_resolved:
        days = resolve_sync_days(conn, athlete_id, today=today)

    if days < 1:
        raise ValueError(f"days 必須 >= 1（收到 {days}）")

    end_date = today or datetime.date.today()
    start_date = end_date - datetime.timedelta(days=days - 1)

    # 自動模式碰到上限代表更早的缺口補不到，須讓呼叫端提示改走匯出包。
    gap_truncated = days_auto_resolved and days == MAX_AUTO_SYNC_DAYS
    estimate = estimate_sync_cost(days)
    plan = {
        "days": days,
        "days_auto_resolved": days_auto_resolved,
        "gap_truncated": gap_truncated,
        "estimate": estimate,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }

    if dry_run:
        return {**plan, "available": True, "dry_run": True}

    try:
        api = client or GarminApiClient()
    except (GarminAuthRequired, GarminAuthError) as e:
        return {**plan, "available": False, "dry_run": False, "reason": str(e)}

    try:
        raw_activities = api.fetch_activities(start_date, end_date)
        activity_rows = [
            r for r in (normalize_activity(a) for a in raw_activities) if r is not None
        ]

        wellness_rows = []
        intraday_rows = []
        health_rows = []
        training_status_rows = []
        for offset in range(days):
            day = start_date + datetime.timedelta(days=offset)
            day_raw = api.fetch_daily_wellness_raw(day)
            normalized = normalize_wellness(day_raw)
            if normalized is not None:
                wellness_rows.append(normalized)
            intraday_rows.extend(normalize_intraday(day_raw))
            health_rows.extend(normalize_health_status(day_raw))
            status_row = normalize_training_status(
                day.isoformat(), day_raw.get("training_status")
            )
            if status_row is not None:
                training_status_rows.append(status_row)

        # 效能指標為「當前值快照」，每次同步只需打一次，落到今天（同步當下）
        # 的日期，非逐日序列（見 normalize_performance_metrics docstring）。
        performance_row = normalize_performance_metrics(
            end_date.isoformat(), api.fetch_performance_metrics_raw(end_date)
        )
        _sync_athlete_sex(conn, athlete_id, api)
        hr_settings_changes = _sync_hr_settings(conn, athlete_id, api)
    except GarminRateLimited as e:
        return {**plan, "available": False, "dry_run": False,
                "reason": str(e), "rate_limited": True}
    except GarminFetchError as e:
        return {**plan, "available": False, "dry_run": False, "reason": str(e)}

    n_act = _upsert_activities(conn, athlete_id, activity_rows)
    n_well = _upsert_wellness(conn, athlete_id, wellness_rows)
    n_intraday = _upsert_intraday(conn, athlete_id, intraday_rows)
    n_health = _upsert_health_status(conn, athlete_id, health_rows)
    n_status = _upsert_training_status(conn, athlete_id, training_status_rows)
    n_perf = _upsert_performance_metrics(conn, athlete_id, performance_row)
    # 先關聯暖身再分類：分類器的「近期平均」會排除已關聯的暖身（N-25）
    n_warmups = len(link_warmups(conn, athlete_id))

    n_laps = 0
    n_classified = 0
    record_stats = {"records_written": 0, "coords_filled": 0}
    if fetch_splits:
        try:
            n_laps, n_classified = _sync_splits(conn, athlete_id, api, activity_rows)
            synced_ids = [
                r["id"]
                for r in (
                    conn.execute(
                        "SELECT id FROM activities WHERE athlete_id = ? AND external_id = ?",
                        (athlete_id, a["external_id"]),
                    ).fetchone()
                    for a in activity_rows
                    if a["external_id"]
                )
                if r is not None
            ]
            record_stats = sync_activity_records(conn, api, synced_ids)
        except GarminRateLimited as e:
            # 分圈抓到一半觸發 429：已寫入的活動保留，但中止並回報。
            conn.commit()
            return {**plan, "available": False, "dry_run": False,
                    "reason": str(e), "rate_limited": True}

    n_cov = _refresh_metric_coverage(conn, athlete_id)
    conn.commit()

    return {
        **plan,
        "available": True,
        "dry_run": False,
        "activities_synced": n_act,
        "wellness_days_synced": n_well,
        "intraday_series_synced": n_intraday,
        "health_status_metrics_synced": n_health,
        "training_status_days_synced": n_status,
        "performance_metrics_synced": n_perf,
        "activities_skipped": len(raw_activities) - n_act,
        "metrics_coverage_updated": n_cov,
        "laps_synced": n_laps,
        "activities_classified": n_classified,
        "records_synced": record_stats["records_written"],
        "record_coords_filled": record_stats["coords_filled"],
        "warmup_links_changed": n_warmups,
        # HRmax／靜止心率有變動時列出新舊值：所有負荷與強度皆即時以新值重算。
        "hr_settings_changes": hr_settings_changes,
    }


def _sync_splits(
    conn: sqlite3.Connection,
    athlete_id: int,
    api: GarminApiClient,
    activity_rows: list[dict],
) -> tuple[int, int]:
    """為可分類的活動補抓逐圈明細，寫入 `activity_laps` 並套用分類器。

    ⚠️ 只處理 `DEFAULT_CLASSIFIABLE_SPORTS`（預設只有 running）——用跑步的
    配速門檻分類重訓會產生誤導標籤（比照 `fit_import_runner` 的既有決定）。

    ⚠️ **已有分圈的活動跳過不重抓**：省 API，也讓重複同步真正冪等。

    單場失敗（逾時、該場無分圈）只跳過該場，不中斷整批；但 429 往外拋
    ——限制以 IP 累積，繼續打只會延長封鎖。

    Returns:
        (寫入的分圈數, 完成分類的活動數)
    """
    n_laps = 0
    n_classified = 0
    for row in activity_rows:
        if row["activity_type"] not in DEFAULT_CLASSIFIABLE_SPORTS:
            continue
        if not row["external_id"]:
            continue

        existing = conn.execute(
            "SELECT id FROM activities WHERE athlete_id = ? AND external_id = ?",
            (athlete_id, row["external_id"]),
        ).fetchone()
        if not existing:
            continue
        activity_id = existing["id"]

        has_laps = conn.execute(
            "SELECT 1 FROM activity_laps WHERE activity_id = ? LIMIT 1", (activity_id,)
        ).fetchone()
        if has_laps:
            continue

        try:
            lap_dtos = api.fetch_activity_splits(row["external_id"])
        except GarminRateLimited:
            raise
        except GarminFetchError:
            continue

        laps = normalize_laps(lap_dtos)
        if not laps:
            continue

        n_laps += store_laps(conn, activity_id, laps)
        update_workout_type(conn, activity_id, laps)
        n_classified += 1
    return n_laps, n_classified


# 補座標時，FIT 逐點與 API 逐點的時間差在此秒數內才視為同一點。
# API 長跑約每 4 秒一點，最近點最多差 2 秒；留一點餘裕。
COORD_MATCH_TOLERANCE_SEC = 5


def _fill_record_coords(conn: sqlite3.Connection, activity_id: int, api_records: list[dict]) -> int:
    """既有 FIT 逐點資料只補經緯度，數值欄位不動。回傳補到座標的筆數。"""
    located = [r for r in api_records if r["lat"] is not None and r["lon"] is not None]
    if not located:
        return 0
    rows = conn.execute(
        "SELECT id, elapsed_sec FROM activity_records WHERE activity_id = ? ORDER BY elapsed_sec",
        (activity_id,),
    ).fetchall()
    filled = 0
    j = 0
    for row in rows:
        t = row["elapsed_sec"]
        # 兩邊都依時間排序，指標只往前走
        while j + 1 < len(located) and abs(located[j + 1]["elapsed_sec"] - t) <= abs(
            located[j]["elapsed_sec"] - t
        ):
            j += 1
        nearest = located[j]
        if abs(nearest["elapsed_sec"] - t) > COORD_MATCH_TOLERANCE_SEC:
            continue
        conn.execute(
            "UPDATE activity_records SET lat = ?, lon = ? WHERE id = ?",
            (nearest["lat"], nearest["lon"], row["id"]),
        )
        filled += 1
    return filled


def sync_activity_records(
    conn: sqlite3.Connection, api: GarminApiClient, activity_ids: list[int]
) -> dict:
    """為跑步活動補逐點資料與經緯度（N-32）。

    - 沒有逐點資料：寫入 API 的逐點序列（降到每 10 秒一點）
    - 有逐點資料但沒有座標（歷史 FIT 匯入）：只補經緯度
    - 已有座標：跳過，不打 API

    ⚠️ 只處理 `DEFAULT_CLASSIFIABLE_SPORTS`（跑步）；跑步機等室內活動不在其中。
    單場失敗只跳過該場；429 往外拋——限制以 IP 累積，繼續打只會延長封鎖。
    """
    stats = {"records_written": 0, "coords_filled": 0, "activities": 0, "skipped": 0}
    for activity_id in activity_ids:
        row = conn.execute(
            "SELECT external_id, activity_type FROM activities WHERE id = ?", (activity_id,)
        ).fetchone()
        if row is None or row["activity_type"] not in DEFAULT_CLASSIFIABLE_SPORTS:
            continue
        if not row["external_id"]:
            continue
        state = conn.execute(
            "SELECT COUNT(*) AS n, COUNT(lat) AS n_lat FROM activity_records WHERE activity_id = ?",
            (activity_id,),
        ).fetchone()
        if state["n_lat"]:
            continue

        try:
            raw = api.fetch_activity_details(row["external_id"])
        except GarminRateLimited:
            raise
        except GarminFetchError:
            stats["skipped"] += 1
            continue

        api_records = normalize_detail_records(raw)
        if not api_records:
            stats["skipped"] += 1
            continue
        if state["n"]:
            stats["coords_filled"] += _fill_record_coords(conn, activity_id, api_records)
        else:
            records = downsample_records(api_records, DEFAULT_SAMPLE_EVERY_SEC)
            stats["records_written"] += store_records(conn, activity_id, records)
        stats["activities"] += 1
        conn.commit()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "從 Garmin Connect 增量同步活動與每日身體數據到本機資料庫。"
            "需先執行 garmin_auth login 完成一次性認證。"
        )
    )
    ap.add_argument("--db-path", required=True, help="SQLite 資料庫路徑")
    ap.add_argument(
        "--days",
        type=int,
        default=None,
        help=f"回溯天數。省略時自動依資料庫現況回推（上限 {MAX_AUTO_SYNC_DAYS} 天）",
    )
    ap.add_argument("--athlete-id", type=int, default=None, help="學員 ID（預設第一位）")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只顯示同步計畫與預估耗時，不打 API、不寫入資料庫",
    )
    ap.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="略過耗時確認直接同步（供排程等非互動情境使用）",
    )
    ap.add_argument(
        "--backfill-records-since",
        metavar="YYYY-MM-DD",
        default=None,
        help="不做一般同步，改為此日期起的跑步活動補逐點資料與經緯度（N-32），每場 1 次 API 請求",
    )
    args = ap.parse_args()

    if not Path(args.db_path).exists():
        print(f"[!] 找不到資料庫檔案：{args.db_path}")
        return 1

    conn = get_connection(args.db_path)
    try:
        athlete_id = args.athlete_id
        if athlete_id is None:
            row = conn.execute("SELECT id FROM athlete_profile ORDER BY id LIMIT 1").fetchone()
            if row is None:
                print("[!] 資料庫中沒有任何學員資料")
                return 1
            athlete_id = row["id"]

        if args.backfill_records_since:
            return _backfill_records(conn, athlete_id, args)

        # 先跑一次 dry-run 取得計畫，把預計耗時告知使用者後再真的打 API。
        plan = sync(conn, athlete_id, args.days, today=None, dry_run=True)
        _print_plan(plan)

        if args.dry_run:
            print("[i] --dry-run：未打 API、未寫入資料庫。")
            return 0

        if not args.yes and not _confirm():
            print("[i] 已取消，未打 API、未寫入資料庫。")
            return 0

        result = sync(conn, athlete_id, args.days)
    finally:
        conn.close()

    if not result["available"]:
        print(f"[!] 同步失敗：{result['reason']}")
        return 1

    print(f"[OK] 同步區間 {result['start_date']} ~ {result['end_date']}")
    print(f"     活動 {result['activities_synced']} 筆"
          f"（略過 {result['activities_skipped']} 筆無法正規化）")
    print(f"     每日身體數據 {result['wellness_days_synced']} 天")
    print(f"     訓練狀態 {result['training_status_days_synced']} 天")
    if result.get("performance_metrics_synced"):
        print("     效能指標快照 1 筆（賽事預測／乳酸閾值／耐力分數／體能年齡／爬坡分數）")
    if result.get("laps_synced"):
        print(
            f"     分圈 {result['laps_synced']} 圈，"
            f"新完成分類 {result['activities_classified']} 場"
        )
    if result.get("records_synced") or result.get("record_coords_filled"):
        print(f"     逐點資料 {result['records_synced']} 筆，補經緯度 {result['record_coords_filled']} 筆")
    for change in result.get("hr_settings_changes") or []:
        print(
            f"[!] {change['field']} 依 Garmin 心率區間設定更新：{change['old']} → "
            f"{change['new']}（訓練負荷與強度判讀已全部以新值重算）"
        )
    print("[i] 重複執行同一區間不會產生重複列（由資料庫 UNIQUE 約束保證）。")
    return 0


def _backfill_records(conn: sqlite3.Connection, athlete_id: int, args) -> int:
    """`--backfill-records-since`：補指定日期起的跑步活動逐點資料與經緯度。"""
    since = datetime.date.fromisoformat(args.backfill_records_since)
    ids = [
        r["id"]
        for r in conn.execute(
            """
            SELECT a.id FROM activities a
            WHERE a.athlete_id = ? AND a.started_at >= ? AND a.external_id IS NOT NULL
              AND a.activity_type IN ({})
              AND NOT EXISTS (SELECT 1 FROM activity_records r
                              WHERE r.activity_id = a.id AND r.lat IS NOT NULL)
            ORDER BY a.started_at
            """.format(",".join("?" * len(DEFAULT_CLASSIFIABLE_SPORTS))),
            (athlete_id, since.isoformat(), *sorted(DEFAULT_CLASSIFIABLE_SPORTS)),
        )
    ]
    # 每場一次請求，回應較大（實測 1–3.5 秒）再加節流間隔
    print(f"[i] {since} 起有 {len(ids)} 場跑步缺經緯度，預估 {len(ids)} 次 API 請求、"
          f"約 {len(ids) * 3 // 60 + 1} 分鐘")
    if args.dry_run or not ids:
        return 0
    if not args.yes and not _confirm():
        print("[i] 已取消，未打 API、未寫入資料庫。")
        return 0
    try:
        api = GarminApiClient()
    except (GarminAuthRequired, GarminAuthError) as e:
        print(f"[!] {e}")
        return 1
    try:
        stats = sync_activity_records(conn, api, ids)
    except GarminRateLimited as e:
        print(f"[!] {e}（已寫入的場次保留，稍後重跑會從缺的場次繼續）")
        return 1
    print(f"[OK] 處理 {stats['activities']} 場：新寫入逐點 {stats['records_written']} 筆，"
          f"補經緯度 {stats['coords_filled']} 筆，略過 {stats['skipped']} 場")
    return 0


def _print_plan(plan: dict) -> None:
    """把同步計畫與預估成本印給使用者看。"""
    mode = "自動回推" if plan["days_auto_resolved"] else "手動指定"
    print(f"[i] 同步計畫（{mode}）：{plan['start_date']} ~ {plan['end_date']}")
    print(f"    預估成本：{plan['estimate']['summary']}")
    if plan["gap_truncated"]:
        print(
            f"[!] 距上次同步已超過 {MAX_AUTO_SYNC_DAYS} 天，本次只補最近 "
            f"{MAX_AUTO_SYNC_DAYS} 天。更早的缺口請下載官方匯出包後走 "
            "garmin_import_runner——一次拿完整歷史，且含分圈與 workout_type。"
        )


def _confirm() -> bool:
    """詢問是否繼續。非互動環境（無 stdin）視為同意，避免排程卡住。"""
    try:
        answer = input("    是否開始同步？[Y/n] ").strip().lower()
    except EOFError:
        return True
    return answer in ("", "y", "yes")


if __name__ == "__main__":
    raise SystemExit(main())
