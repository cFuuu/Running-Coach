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

⚠️ 逐秒資料（`activity_records`）仍需 FIT，本模組不提供——故心率漂移與
配速穩定度兩個維度對同步進來的活動仍不可評。

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
from src.main.python.services.fit_import_runner import (
    DEFAULT_CLASSIFIABLE_SPORTS,
    store_laps,
    update_workout_type,
)
# ⚠️ 只重用 ms_to_sec()：這不是「套用匯出包的單位換算係數」——hrTimeInZone_N
# 這個特定欄位經 dashboard_queries.py 實測證實在 API 回傳裡就是毫秒（與同一
# payload 裡 distance/duration 的公尺/秒慣例不同），是欄位本身的例外，
# 不是本模組 docstring 警告的「誤用匯出包係數」情境。沿用同一顆已驗證過的
# 函式，而非另外複製一份 /1000 邏輯（CLAUDE.md：禁止複製貼上程式碼區塊）。
from src.main.python.services.garmin_export_parser import ms_to_sec

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
# 成本為 451 次請求／約 7.5 分鐘（見 estimate_sync_cost）。更早的缺口應走
# 官方匯出包——一次拿完整歷史，且含分圈與 workout_type，品質優於 API 摘要。
# ⚠️ 不可貿然調高：wellness 是逐日各打 5 個端點，天數與請求數成正比，
# garmin_api_client 的硬上限 365 天等於 1826 次請求／約 30 分鐘連續請求，
# 已超出正常使用者行為的樣子，有觸發 Cloudflare 風控的風險。
MAX_AUTO_SYNC_DAYS = 90

# 每日身體數據的端點數（stats／sleep／hrv／training_readiness／
# training_status），與 garmin_api_client.fetch_daily_wellness_raw() 的
# 來源數一致。2026-09-02：Task 1B-9 新增 training_status，由 4 → 5
# ——若忘記同步更新，耗時預告會低估，違反與 Fu 議定的預告行為。
_WELLNESS_ENDPOINTS_PER_DAY = 5

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

    ⚠️ 欄位值單位是**毫秒**（與同一 payload 裡 distance/duration 的
    公尺/秒慣例不同），須用 `ms_to_sec()` 換算——見本模組頂部 import 註解
    與 `dashboard_queries._build_hr_zones()` 的既有實測佐證。

    全部區間皆缺值或為 0 視為無心率資料，回傳 None（不存一個全 0 的 JSON，
    那會讓下游誤以為「查過了、確實是 0」而非「根本沒量到」）。
    """
    zones: dict[str, int] = {}
    for i in range(_HR_ZONE_MIN, _HR_ZONE_MAX + 1):
        value = raw.get(f"{_HR_ZONE_PREFIX}{i}")
        if value is None:
            continue
        seconds = ms_to_sec(value)
        if seconds is not None:
            zones[str(i)] = seconds
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
    }
    # 除了日期以外全為 None 代表該日無任何資料，不寫入空列。
    if all(v is None for k, v in row.items() if k != "date"):
        return None
    return row


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

    ⚠️ 欄位巢狀路徑依審計報告 §2.1 記錄的真實帳號探測結果推導，**尚未**
    對真實帳號重新驗證過完整結構（本機驗證清單項目）。裝置維度的資料
    一律經 `_select_primary_device()` 挑選，見其 docstring。

    `raw` 為 None 或空 dict 時回傳 None（不寫入空列）。
    """
    if not raw:
        return None

    device = _select_primary_device(raw.get("latestTrainingStatusData"))
    load_balance = _select_primary_device(
        (raw.get("mostRecentTrainingLoadBalance") or {}).get(
            "metricsTrainingLoadBalanceDTOMap"
        )
    )
    acute_dto = device.get("acuteTrainingLoadDTO") or {}
    heat_dto = device.get("heatAltitudeAcclimation") or {}
    vo2max = (raw.get("mostRecentVO2Max") or {}).get("generic") or {}

    row = {
        "date": date_str,
        "training_status_code": device.get("trainingStatus"),
        "training_status_phrase": device.get("trainingStatusFeedbackPhrase"),
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

    ⚠️ 除 `endurance_contributors_json` 的巢狀鍵名（`activityTypeId`／
    `group`／`contribution`）已於審計時對真實帳號確認外，其餘欄位路徑
    （賽事預測、乳酸閾值、體能年齡的巢狀結構）為依 `garminconnect`
    慣例與審計報告文字描述的最佳推斷，**尚未**對真實帳號驗證，
    屬本機驗證清單的額外項目（見 TODO.md 1B-9）。若真實欄位路徑不同，
    對應欄位會落成 NULL（`data_confidence` 已要求信賴度必填，不會產生
    「看似有值但語意錯誤」的欄位），不影響其他已驗證欄位的寫入。

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

    # 乳酸閾值配速：speed 為公尺/秒，換算方式與 activities 的 best_pace 一致。
    lactate_speed = lactate.get("speed")
    lactate_pace = round(1000 / lactate_speed) if lactate_speed else None

    row = {
        "date": date_str,
        "race_predict_5k_sec": race.get("time5K"),
        "race_predict_10k_sec": race.get("time10K"),
        "race_predict_half_sec": race.get("timeHalfMarathon"),
        "race_predict_marathon_sec": race.get("timeMarathon"),
        # ⚠️ 依審計報告，garminconnect 函式庫在合併回傳時會處理 Garmin
        # 原始欄位的歷史拼字錯誤 `hearRate`（缺一個 t）；究竟是改名為
        # `heartRate` 還是原樣保留待本機驗證，故兩個鍵名都嘗試讀取。
        "lactate_threshold_hr_bpm": _round_or_none(
            lactate.get("heartRate") if lactate.get("heartRate") is not None
            else lactate.get("hearRate")
        ),
        "lactate_threshold_pace_sec_per_km": lactate_pace,
        "functional_threshold_power": _round_or_none(lactate.get("functionalThresholdPower")),
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


def _upsert_wellness(conn: sqlite3.Connection, athlete_id: int, rows: list[dict]) -> int:
    now = _utc_now_iso()
    count = 0
    for row in rows:
        conn.execute(
            """
            INSERT INTO daily_wellness (
                athlete_id, date, resting_hr_bpm, hrv_ms, hrv_weekly_avg_ms, spo2_pct,
                skin_temp_c, respiration_rate, sleep_duration_sec, sleep_quality,
                sleep_score, stress_avg, all_day_stress_avg, body_battery_max,
                body_battery_min, steps, training_readiness_score, recovery_time_hours,
                acwr, hrv_baseline_low_upper, hrv_baseline_balanced_low,
                hrv_baseline_balanced_upper, hrv_status, hrv_last_night_5min_high,
                acute_load, rhr_7day_avg_bpm, sleep_deep_sec, sleep_light_sec,
                sleep_rem_sec, sleep_awake_sec, body_battery_at_wake,
                readiness_input_context, readiness_timestamp_local,
                source, source_version, fetched_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT (athlete_id, date, source) DO UPDATE SET
                resting_hr_bpm=COALESCE(excluded.resting_hr_bpm, resting_hr_bpm),
                hrv_ms=COALESCE(excluded.hrv_ms, hrv_ms),
                hrv_weekly_avg_ms=COALESCE(excluded.hrv_weekly_avg_ms, hrv_weekly_avg_ms),
                spo2_pct=COALESCE(excluded.spo2_pct, spo2_pct),
                respiration_rate=COALESCE(excluded.respiration_rate, respiration_rate),
                sleep_duration_sec=COALESCE(excluded.sleep_duration_sec, sleep_duration_sec),
                sleep_score=COALESCE(excluded.sleep_score, sleep_score),
                stress_avg=COALESCE(excluded.stress_avg, stress_avg),
                all_day_stress_avg=COALESCE(excluded.all_day_stress_avg, all_day_stress_avg),
                body_battery_max=COALESCE(excluded.body_battery_max, body_battery_max),
                body_battery_min=COALESCE(excluded.body_battery_min, body_battery_min),
                steps=COALESCE(excluded.steps, steps),
                training_readiness_score=COALESCE(
                    excluded.training_readiness_score, training_readiness_score),
                recovery_time_hours=COALESCE(excluded.recovery_time_hours, recovery_time_hours),
                acwr=COALESCE(excluded.acwr, acwr),
                hrv_baseline_low_upper=COALESCE(
                    excluded.hrv_baseline_low_upper, hrv_baseline_low_upper),
                hrv_baseline_balanced_low=COALESCE(
                    excluded.hrv_baseline_balanced_low, hrv_baseline_balanced_low),
                hrv_baseline_balanced_upper=COALESCE(
                    excluded.hrv_baseline_balanced_upper, hrv_baseline_balanced_upper),
                hrv_status=COALESCE(excluded.hrv_status, hrv_status),
                hrv_last_night_5min_high=COALESCE(
                    excluded.hrv_last_night_5min_high, hrv_last_night_5min_high),
                acute_load=COALESCE(excluded.acute_load, acute_load),
                rhr_7day_avg_bpm=COALESCE(excluded.rhr_7day_avg_bpm, rhr_7day_avg_bpm),
                sleep_deep_sec=COALESCE(excluded.sleep_deep_sec, sleep_deep_sec),
                sleep_light_sec=COALESCE(excluded.sleep_light_sec, sleep_light_sec),
                sleep_rem_sec=COALESCE(excluded.sleep_rem_sec, sleep_rem_sec),
                sleep_awake_sec=COALESCE(excluded.sleep_awake_sec, sleep_awake_sec),
                body_battery_at_wake=COALESCE(
                    excluded.body_battery_at_wake, body_battery_at_wake),
                readiness_input_context=COALESCE(
                    excluded.readiness_input_context, readiness_input_context),
                readiness_timestamp_local=COALESCE(
                    excluded.readiness_timestamp_local, readiness_timestamp_local),
                source_version=excluded.source_version,
                fetched_at=excluded.fetched_at
            """,
            (
                athlete_id,
                row["date"],
                row["resting_hr_bpm"],
                row["hrv_ms"],
                row["hrv_weekly_avg_ms"],
                row["spo2_pct"],
                row["skin_temp_c"],
                row["respiration_rate"],
                row["sleep_duration_sec"],
                row["sleep_quality"],
                row["sleep_score"],
                row["stress_avg"],
                row["all_day_stress_avg"],
                row["body_battery_max"],
                row["body_battery_min"],
                row["steps"],
                row["training_readiness_score"],
                row["recovery_time_hours"],
                row["acwr"],
                row["hrv_baseline_low_upper"],
                row["hrv_baseline_balanced_low"],
                row["hrv_baseline_balanced_upper"],
                row["hrv_status"],
                row["hrv_last_night_5min_high"],
                row["acute_load"],
                row["rhr_7day_avg_bpm"],
                row["sleep_deep_sec"],
                row["sleep_light_sec"],
                row["sleep_rem_sec"],
                row["sleep_awake_sec"],
                row["body_battery_at_wake"],
                row["readiness_input_context"],
                row["readiness_timestamp_local"],
                SYNC_SOURCE,
                SYNC_SOURCE_VERSION,
                now,
            ),
        )
        count += 1
    return count


def _upsert_training_status(conn: sqlite3.Connection, athlete_id: int, rows: list[dict]) -> int:
    """寫入 `training_status`。沿用 daily_wellness 的 ON CONFLICT + COALESCE 慣例
    ——重複同步同一天只補空值，不覆寫已有值。
    """
    now = _utc_now_iso()
    count = 0
    for row in rows:
        conn.execute(
            """
            INSERT INTO training_status (
                athlete_id, date, training_status_code, training_status_phrase,
                acute_load, chronic_load, acwr_ratio, chronic_load_optimal_min,
                chronic_load_optimal_max, acwr_status, vo2max_precise, vo2max_date,
                heat_acclimation_pct, heat_trend, altitude_acclimation_pct,
                fitness_trend, monthly_load_aerobic_low, monthly_load_aerobic_high,
                monthly_load_anaerobic, training_balance_feedback,
                source, source_version, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (athlete_id, date, source) DO UPDATE SET
                training_status_code=COALESCE(
                    excluded.training_status_code, training_status_code),
                training_status_phrase=COALESCE(
                    excluded.training_status_phrase, training_status_phrase),
                acute_load=COALESCE(excluded.acute_load, acute_load),
                chronic_load=COALESCE(excluded.chronic_load, chronic_load),
                acwr_ratio=COALESCE(excluded.acwr_ratio, acwr_ratio),
                chronic_load_optimal_min=COALESCE(
                    excluded.chronic_load_optimal_min, chronic_load_optimal_min),
                chronic_load_optimal_max=COALESCE(
                    excluded.chronic_load_optimal_max, chronic_load_optimal_max),
                acwr_status=COALESCE(excluded.acwr_status, acwr_status),
                vo2max_precise=COALESCE(excluded.vo2max_precise, vo2max_precise),
                vo2max_date=COALESCE(excluded.vo2max_date, vo2max_date),
                heat_acclimation_pct=COALESCE(
                    excluded.heat_acclimation_pct, heat_acclimation_pct),
                heat_trend=COALESCE(excluded.heat_trend, heat_trend),
                altitude_acclimation_pct=COALESCE(
                    excluded.altitude_acclimation_pct, altitude_acclimation_pct),
                fitness_trend=COALESCE(excluded.fitness_trend, fitness_trend),
                monthly_load_aerobic_low=COALESCE(
                    excluded.monthly_load_aerobic_low, monthly_load_aerobic_low),
                monthly_load_aerobic_high=COALESCE(
                    excluded.monthly_load_aerobic_high, monthly_load_aerobic_high),
                monthly_load_anaerobic=COALESCE(
                    excluded.monthly_load_anaerobic, monthly_load_anaerobic),
                training_balance_feedback=COALESCE(
                    excluded.training_balance_feedback, training_balance_feedback),
                source_version=excluded.source_version,
                fetched_at=excluded.fetched_at
            """,
            (
                athlete_id,
                row["date"],
                row["training_status_code"],
                row["training_status_phrase"],
                row["acute_load"],
                row["chronic_load"],
                row["acwr_ratio"],
                row["chronic_load_optimal_min"],
                row["chronic_load_optimal_max"],
                row["acwr_status"],
                row["vo2max_precise"],
                row["vo2max_date"],
                row["heat_acclimation_pct"],
                row["heat_trend"],
                row["altitude_acclimation_pct"],
                row["fitness_trend"],
                row["monthly_load_aerobic_low"],
                row["monthly_load_aerobic_high"],
                row["monthly_load_anaerobic"],
                row["training_balance_feedback"],
                SYNC_SOURCE,
                SYNC_SOURCE_VERSION,
                now,
            ),
        )
        count += 1
    return count


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
    wellness 則**逐日**各打 `_WELLNESS_ENDPOINTS_PER_DAY`（5）個端點，
    故基本總數 = 1 + days × 5。
    另若要補抓分圈，**每場跑步各多 1 次**（`get_activity_splits`）——
    `expected_activities` 即該筆數，未知時傳 0（僅低估分圈部分）。
    每次呼叫之間有 `REQUEST_INTERVAL_SEC` 的節流間隔。

    預估值只計節流時間，未計網路往返，實際耗時會略長於此。
    """
    request_count = 1 + days * _WELLNESS_ENDPOINTS_PER_DAY + expected_activities
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
        training_status_rows = []
        for offset in range(days):
            day = start_date + datetime.timedelta(days=offset)
            day_raw = api.fetch_daily_wellness_raw(day)
            normalized = normalize_wellness(day_raw)
            if normalized is not None:
                wellness_rows.append(normalized)
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
    except GarminRateLimited as e:
        return {**plan, "available": False, "dry_run": False,
                "reason": str(e), "rate_limited": True}
    except GarminFetchError as e:
        return {**plan, "available": False, "dry_run": False, "reason": str(e)}

    n_act = _upsert_activities(conn, athlete_id, activity_rows)
    n_well = _upsert_wellness(conn, athlete_id, wellness_rows)
    n_status = _upsert_training_status(conn, athlete_id, training_status_rows)
    n_perf = _upsert_performance_metrics(conn, athlete_id, performance_row)

    n_laps = 0
    n_classified = 0
    if fetch_splits:
        try:
            n_laps, n_classified = _sync_splits(conn, athlete_id, api, activity_rows)
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
        "training_status_days_synced": n_status,
        "performance_metrics_synced": n_perf,
        "activities_skipped": len(raw_activities) - n_act,
        "metrics_coverage_updated": n_cov,
        "laps_synced": n_laps,
        "activities_classified": n_classified,
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
    print("[i] 重複執行同一區間不會產生重複列（由資料庫 UNIQUE 約束保證）。")
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
