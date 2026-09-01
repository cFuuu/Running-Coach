"""Garmin Connect 增量同步：抓取 → 正規化 → 去重寫入 SQLite。

用法：
    python -m src.main.python.services.garmin_sync_runner \
        --db-path output/running_coach.db --days 7

需先完成一次性認證（見 `garmin_auth`）：
    python -m src.main.python.services.garmin_auth login

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

## ⚠️ 已知限制：同步進來的活動沒有 `workout_type`

API 的活動摘要**不含分圈資料**，而 `workout_type` 的自動分類
（`workout_classifier.classify_workout()`）需要分圈才能判斷配速結構。
故本模組寫入的活動 `workout_type` 為 `NULL`，**這是刻意不猜**：

依 PLAN.md §1「`workout_type` 為 unknown/NULL 時不輸出類型相關判讀」，
分類器缺前提時的正確行為是留空、讓下游拒絕判讀，而不是塞一個猜測值。
拿 easy 的標準去評一場其實是間歇的課，會產出完全錯誤的結論。

補齊方式（擇一，皆屬後續工作，非本模組職責）：
1. 用 `download_activity(id, ORIGINAL)` 取回 FIT 後走既有 `fit_import_runner`
   ——可同時補上分圈與逐秒資料，是完整解
2. 人工標註（`workout_type_source='manual'`，本模組的 upsert 已保證不覆蓋）

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
    GarminApiClient,
    GarminFetchError,
    GarminRateLimited,
)
from src.main.python.services.garmin_auth import GarminAuthError, GarminAuthRequired

# 寫入時採用的 source 值。沿用 garmin_export 的理由見模組 docstring。
SYNC_SOURCE = "garmin_export"
# 記在 source_version，讓「同步進來的」與「匯出包來的」仍可區分。
SYNC_SOURCE_VERSION = "api_sync"

_SECONDS_PER_MINUTE = 60

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
        # API 已是公尺，不需換算（匯出包才是公分）
        "elevation_gain_m": raw.get("elevationGain"),
        "elevation_loss_m": raw.get("elevationLoss"),
        # API 已是大卡，不需 kj_to_kcal（匯出包才是千焦耳）
        "calories": _round_or_none(raw.get("calories")),
        "raw_data_json": json.dumps(raw, ensure_ascii=False),
    }


def normalize_wellness(raw: dict) -> dict | None:
    """把單日各來源的原始回傳彙整成 `daily_wellness` 表的欄位。

    ⚠️ Training Readiness／Body Battery／HRV status 為 Garmin 黑盒衍生值，
    照存但取用時須帶 `garmin_blackbox` 標記。

    ⚠️ `acwr` 欄位存的是 `acwrFactorPercent`（0–100 貢獻度分數），
    **不是**古典的 0.8–1.3 急慢性負荷比——沿用既有欄位語意，
    信賴度標記已於 `data_confidence.KNOWN_BIASES` 定義。

    全日皆無資料時回傳 None（不寫入空列）。
    """
    stats = raw.get("stats") or {}
    sleep = raw.get("sleep") or {}
    hrv = raw.get("hrv") or {}
    readiness_list = raw.get("training_readiness") or []
    readiness = readiness_list[0] if readiness_list else {}

    sleep_dto = sleep.get("dailySleepDTO") or {}
    hrv_summary = hrv.get("hrvSummary") or {}

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
        "stress_avg": stats.get("averageStressLevel"),
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
    }
    # 除了日期以外全為 None 代表該日無任何資料，不寫入空列。
    if all(v is None for k, v in row.items() if k != "date"):
        return None
    return row


def _upsert_activities(conn: sqlite3.Connection, athlete_id: int, rows: list[dict]) -> int:
    """寫入活動。沿用 garmin_import_runner 的 ON CONFLICT 慣例。

    ⚠️ ON CONFLICT 刻意**不更新** `workout_type` / `workout_type_source`
    ——分類結果（尤其人工標註的 manual）不可被同步覆蓋。
    """
    now = _utc_now_iso()
    count = 0
    for row in rows:
        conn.execute(
            """
            INSERT INTO activities (
                athlete_id, external_id, activity_type, title, started_at,
                distance_km, duration_sec, moving_time_sec, avg_hr_bpm, max_hr_bpm,
                aerobic_te, avg_cadence_spm, max_cadence_spm, avg_pace_sec_per_km,
                best_pace_sec_per_km, elevation_gain_m, elevation_loss_m, calories,
                source, source_version, fetched_at, has_wellness_data, raw_data_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                fetched_at=excluded.fetched_at, source_version=excluded.source_version
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
                acwr, source, source_version, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                SYNC_SOURCE,
                SYNC_SOURCE_VERSION,
                now,
            ),
        )
        count += 1
    return count


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


def sync(
    conn: sqlite3.Connection,
    athlete_id: int,
    days: int,
    client: GarminApiClient | None = None,
    today: datetime.date | None = None,
) -> dict:
    """同步最近 `days` 天的活動與每日身體數據。

    冪等：重複執行同一區間不會產生重複列（由資料庫 UNIQUE 約束保證）。

    Returns:
        含 `available`／筆數／日期區間的結果 dict。認證或抓取失敗時
        `available=False` 並附 `reason`，不拋例外——供 MCP tool 直接回傳。
    """
    if days < 1:
        raise ValueError(f"days 必須 >= 1（收到 {days}）")

    end_date = today or datetime.date.today()
    start_date = end_date - datetime.timedelta(days=days - 1)

    try:
        api = client or GarminApiClient()
    except (GarminAuthRequired, GarminAuthError) as e:
        return {"available": False, "reason": str(e)}

    try:
        raw_activities = api.fetch_activities(start_date, end_date)
        activity_rows = [
            r for r in (normalize_activity(a) for a in raw_activities) if r is not None
        ]

        wellness_rows = []
        for offset in range(days):
            day = start_date + datetime.timedelta(days=offset)
            normalized = normalize_wellness(api.fetch_daily_wellness_raw(day))
            if normalized is not None:
                wellness_rows.append(normalized)
    except GarminRateLimited as e:
        return {"available": False, "reason": str(e), "rate_limited": True}
    except GarminFetchError as e:
        return {"available": False, "reason": str(e)}

    n_act = _upsert_activities(conn, athlete_id, activity_rows)
    n_well = _upsert_wellness(conn, athlete_id, wellness_rows)
    n_cov = _refresh_metric_coverage(conn, athlete_id)
    conn.commit()

    return {
        "available": True,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "activities_synced": n_act,
        "wellness_days_synced": n_well,
        "activities_skipped": len(raw_activities) - n_act,
        "metrics_coverage_updated": n_cov,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "從 Garmin Connect 增量同步活動與每日身體數據到本機資料庫。"
            "需先執行 garmin_auth login 完成一次性認證。"
        )
    )
    ap.add_argument("--db-path", required=True, help="SQLite 資料庫路徑")
    ap.add_argument("--days", type=int, default=7, help="回溯天數（預設 7）")
    ap.add_argument("--athlete-id", type=int, default=None, help="學員 ID（預設第一位）")
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
    print("[i] 重複執行同一區間不會產生重複列（由資料庫 UNIQUE 約束保證）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
