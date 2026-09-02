"""Orchestrates a Garmin export import: discover -> parse -> resolve athlete -> write to SQLite.

Usage:
    python -m src.main.python.services.garmin_import_runner \
        --input-dir input/garmin_export --db-path output/running_coach.db --athlete-name Fu

Neither --input-dir nor --db-path is hardcoded (see AGENTS.md: no hardcoding of
configurable values) — both must be passed explicitly, which also keeps the
personal export path and the personal database out of source code.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
from pathlib import Path

from src.main.python.models.db import get_connection
from src.main.python.services import garmin_export_parser as parser


def _utc_now_iso() -> str:
    """目前 UTC 時間（不帶時區標記），供 fetched_at / created_at 等欄位使用。"""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()


def _epoch_ms_to_iso(epoch_ms: float | None) -> str | None:
    if epoch_ms is None:
        return None
    dt = datetime.datetime.fromtimestamp(epoch_ms / 1000, datetime.timezone.utc)
    return dt.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")


def resolve_athlete_id(
    conn: sqlite3.Connection, source: str, external_ref: str, athlete_name: str
) -> int:
    """Map a source account identity (e.g. Garmin userProfileId) to an internal athlete_id.

    Reuses the existing athlete on repeat imports instead of creating a duplicate row.
    """
    row = conn.execute(
        "SELECT athlete_id FROM athlete_source_identity WHERE source = ? AND external_ref = ?",
        (source, external_ref),
    ).fetchone()
    if row:
        return row["athlete_id"]

    now = _utc_now_iso()
    cur = conn.execute(
        "INSERT INTO athlete_profile (name, updated_at) VALUES (?, ?)",
        (athlete_name, now),
    )
    athlete_id = cur.lastrowid
    conn.execute(
        "INSERT INTO athlete_source_identity (athlete_id, source, external_ref, created_at) "
        "VALUES (?, ?, ?, ?)",
        (athlete_id, source, external_ref, now),
    )
    return athlete_id


def _upsert_activities(conn: sqlite3.Connection, athlete_id: int, rows: list[dict]) -> int:
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
                reps, sets, source, source_version, fetched_at, has_wellness_data,
                raw_data_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (athlete_id, started_at, activity_type, distance_km) DO UPDATE SET
                title=excluded.title, duration_sec=excluded.duration_sec,
                moving_time_sec=excluded.moving_time_sec, avg_hr_bpm=excluded.avg_hr_bpm,
                max_hr_bpm=excluded.max_hr_bpm, aerobic_te=excluded.aerobic_te,
                avg_cadence_spm=excluded.avg_cadence_spm, max_cadence_spm=excluded.max_cadence_spm,
                avg_pace_sec_per_km=excluded.avg_pace_sec_per_km,
                best_pace_sec_per_km=excluded.best_pace_sec_per_km,
                elevation_gain_m=excluded.elevation_gain_m, elevation_loss_m=excluded.elevation_loss_m,
                calories=excluded.calories, has_wellness_data=excluded.has_wellness_data,
                raw_data_json=excluded.raw_data_json, fetched_at=excluded.fetched_at
            """,
            (
                athlete_id,
                row["external_id"],
                row["activity_type"],
                row["title"],
                _epoch_ms_to_iso(row["started_at_local_epoch_ms"]),
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
                row["reps"],
                row["sets"],
                "garmin_export",
                None,
                now,
                row["has_wellness_data"],
                row["raw_data_json"],
            ),
        )
        count += 1
    return count


def _upsert_daily_wellness(conn: sqlite3.Connection, athlete_id: int, rows: list[dict]) -> int:
    now = _utc_now_iso()
    count = 0
    for row in rows:
        conn.execute(
            """
            INSERT INTO daily_wellness (
                athlete_id, date, resting_hr_bpm, hrv_ms, hrv_weekly_avg_ms, spo2_pct,
                skin_temp_c, respiration_rate, sleep_duration_sec, sleep_quality,
                sleep_score, stress_avg, all_day_stress_avg, body_battery_max, body_battery_min, steps,
                training_readiness_score, recovery_time_hours, acwr, source,
                source_version, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (athlete_id, date, source) DO UPDATE SET
                resting_hr_bpm=excluded.resting_hr_bpm, hrv_ms=excluded.hrv_ms,
                hrv_weekly_avg_ms=excluded.hrv_weekly_avg_ms, spo2_pct=excluded.spo2_pct,
                skin_temp_c=excluded.skin_temp_c, respiration_rate=excluded.respiration_rate,
                sleep_duration_sec=excluded.sleep_duration_sec, sleep_quality=excluded.sleep_quality,
                sleep_score=excluded.sleep_score, stress_avg=excluded.stress_avg,
                all_day_stress_avg=excluded.all_day_stress_avg, steps=excluded.steps,
                training_readiness_score=excluded.training_readiness_score,
                recovery_time_hours=excluded.recovery_time_hours, acwr=excluded.acwr,
                fetched_at=excluded.fetched_at
            """,
            (
                athlete_id,
                row["date"],
                row.get("resting_hr_bpm"),
                row.get("hrv_ms"),
                row.get("hrv_weekly_avg_ms"),
                row.get("spo2_pct"),
                row.get("skin_temp_c"),
                row.get("respiration_rate"),
                row.get("sleep_duration_sec"),
                row.get("sleep_quality"),
                row.get("sleep_score"),
                row.get("stress_avg"),
                row.get("all_day_stress_avg"),
                row.get("body_battery_max"),
                row.get("body_battery_min"),
                row.get("steps"),
                row.get("training_readiness_score"),
                row.get("recovery_time_hours"),
                row.get("acwr"),
                "garmin_export",
                None,
                now,
            ),
        )
        count += 1
    return count


def _upsert_metric_coverage(conn: sqlite3.Connection, athlete_id: int, rows: list[dict]) -> int:
    now = _utc_now_iso()
    count = 0
    for row in rows:
        conn.execute(
            """
            INSERT INTO metric_coverage (athlete_id, metric_name, source, earliest_date, latest_date, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (athlete_id, metric_name, source) DO UPDATE SET
                earliest_date = MIN(earliest_date, excluded.earliest_date),
                latest_date = MAX(latest_date, excluded.latest_date),
                updated_at = excluded.updated_at
            """,
            (athlete_id, row["metric_name"], "garmin_export", row["earliest_date"], row["latest_date"], now),
        )
        count += 1
    return count


# Task 1B-9 新增的指標欄位在 `raw_data_json` 裡本來就有，只是舊版 ETL 沒接。
# 回填不需重打 API，但**必須分來源換算**——匯出包與 API 的單位慣例不同。
#
# ⚠️ 兩者單位相同的欄位（實測 2026-09-02 逐欄位比對同一批活動）：
# 步幅/觸地時間/垂直振幅/垂直比/功率三項/溫度兩項/訓練效果——直接取值。
# 只有下列兩項不同，見 `_backfill_activity_metrics()` 內的註解。
_BACKFILL_SAME_UNIT_FIELDS = {
    "avg_ground_contact_time_ms": "avgGroundContactTime",
    "avg_vertical_oscillation_cm": "avgVerticalOscillation",
    "avg_vertical_ratio_pct": "avgVerticalRatio",
    "avg_stride_length_cm": "avgStrideLength",
    "avg_power_watts": "avgPower",
    "max_power_watts": "maxPower",
    "norm_power_watts": "normPower",
    "min_temperature_c": "minTemperature",
    "max_temperature_c": "maxTemperature",
    "anaerobic_te": "anaerobicTrainingEffect",
}

#: 匯出包的 `avgGradeAdjustedSpeed` 單位是公寸/秒，需 ×10 才是公尺/秒。
#: 實測 2026-09-02：0.2655 ×10 → 2.655 m/s → 6:16/km，與該場實際配速一致；
#: 若當成公尺/秒會得到 62:46/km。API 來源則本來就是公尺/秒。
_EXPORT_GAP_TO_METRES_PER_SEC = 10.0


def _backfill_activity_metrics(
    conn: sqlite3.Connection, athlete_id: int, *, dry_run: bool = False
) -> dict:
    """從既有 `raw_data_json` 回填 Task 1B-9 新增的活動指標欄位。

    這些資料在匯入當下就已存在於 raw payload，只是舊版 ETL 沒有建模，
    故不需重打 API。**只填 NULL 欄位，不覆蓋既有值**——已由同步寫入的
    列（或人工修正過的值）不受影響，因此可重複執行。

    ## 為何要分來源

    匯出包與 API 的單位慣例不同（見 `garmin_export_parser` 的換算函式與
    `garmin_sync_runner` 模組 docstring）。本函式只在兩處需要分流：

    - `avgGradeAdjustedSpeed`：匯出包是公寸/秒（×10 才是公尺/秒）
    - `hrTimeInZone_N`：匯出包是毫秒，API 是秒

    其餘欄位兩來源單位相同，經 2026-09-02 對同一批活動逐欄位比對確認。

    Returns:
        各欄位實際回填的列數，另含 `activities_touched` 總計。
    """
    rows = conn.execute(
        """
        SELECT id, source_version, raw_data_json
        FROM activities
        WHERE athlete_id = ? AND raw_data_json IS NOT NULL
        """,
        (athlete_id,),
    ).fetchall()

    filled: dict[str, int] = {}
    touched = 0
    for row in rows:
        activity_id, source_version, raw_json = row[0], row[1], row[2]
        try:
            raw = json.loads(raw_json)
        except (ValueError, TypeError):
            continue
        is_api = source_version == "api_sync"

        updates: dict[str, object] = {}
        for column, key in _BACKFILL_SAME_UNIT_FIELDS.items():
            value = raw.get(key)
            if value is not None:
                updates[column] = value

        # 節奏：匯出包用 avgDoubleCadence（雙腳步頻，與跑者慣讀的 170-180 spm
        # 一致），API 用 averageRunningCadenceInStepsPerMinute。沿用
        # parse_activities() 既有的選鍵理由，不改語意。
        cadence = raw.get("averageRunningCadenceInStepsPerMinute") or raw.get("avgDoubleCadence")
        if cadence is not None:
            updates["avg_cadence_spm"] = cadence
        max_cadence = raw.get("maxRunningCadenceInStepsPerMinute") or raw.get("maxDoubleCadence")
        if max_cadence is not None:
            updates["max_cadence_spm"] = max_cadence

        gap_speed = raw.get("avgGradeAdjustedSpeed")
        if gap_speed:
            metres_per_sec = gap_speed if is_api else gap_speed * _EXPORT_GAP_TO_METRES_PER_SEC
            if metres_per_sec > 0:
                updates["avg_grade_adj_pace_sec_per_km"] = round(1000 / metres_per_sec)

        zones = _build_backfill_hr_zones(raw, is_api=is_api)
        if zones is not None:
            updates["hr_time_in_zone_json"] = zones

        label = raw.get("trainingEffectLabel") or raw.get("aerobicTrainingEffectMessage")
        if label is not None:
            updates["training_effect_label"] = label
        load = raw.get("activityTrainingLoad")
        if load is not None:
            updates["activity_training_load"] = load

        if not updates:
            continue

        # COALESCE 讓既有非 NULL 值原封不動——回填只補洞，不改寫歷史。
        assignments = ", ".join(f"{col} = COALESCE({col}, ?)" for col in updates)
        where = " OR ".join(f"{col} IS NULL" for col in updates)
        if not dry_run:
            cur = conn.execute(
                f"UPDATE activities SET {assignments} WHERE id = ? AND ({where})",
                (*updates.values(), activity_id),
            )
            if cur.rowcount:
                touched += 1
        else:
            existing = conn.execute(
                f"SELECT 1 FROM activities WHERE id = ? AND ({where})", (activity_id,)
            ).fetchone()
            if existing:
                touched += 1
        for col in updates:
            filled[col] = filled.get(col, 0) + 1

    if not dry_run:
        conn.commit()
    filled["activities_touched"] = touched
    return filled


#: 回填後需要 coverage 的活動類指標——欄位名即 metric_name，與 wellness
#: 類指標（由同步路徑自行登記）合用同一張 `metric_coverage` 表。
_ACTIVITY_COVERAGE_COLUMNS = (
    "avg_cadence_spm",
    "max_cadence_spm",
    "anaerobic_te",
    "avg_grade_adj_pace_sec_per_km",
    "avg_ground_contact_time_ms",
    "avg_vertical_oscillation_cm",
    "avg_vertical_ratio_pct",
    "avg_stride_length_cm",
    "avg_power_watts",
    "max_power_watts",
    "norm_power_watts",
    "min_temperature_c",
    "max_temperature_c",
    "hr_time_in_zone_json",
    "training_effect_label",
    "activity_training_load",
)


def refresh_activity_metric_coverage(conn: sqlite3.Connection, athlete_id: int) -> int:
    """依 `activities` 現況重算活動類指標的可用區間，寫入 `metric_coverage`。

    與 `garmin_export_parser.compute_metric_coverage()` 的分工：後者吃匯入
    當下的 parsed rows，用於首次匯入；本函式從資料庫反推，用於回填後或
    任何「欄位已在庫裡、但 coverage 未登記」的情況。

    各指標的歷史深度差異很大（跑步動態約 194 場、步幅 249 場、心率區間
    152 場），這是規則引擎需要知道的真實事實——不可假設一致。
    """
    rows: list[dict] = []
    for column in _ACTIVITY_COVERAGE_COLUMNS:
        row = conn.execute(
            f"""
            SELECT MIN(DATE(started_at)) AS earliest, MAX(DATE(started_at)) AS latest
            FROM activities
            WHERE athlete_id = ? AND {column} IS NOT NULL
            """,
            (athlete_id,),
        ).fetchone()
        if row and row[0]:
            rows.append(
                {"metric_name": column, "earliest_date": row[0], "latest_date": row[1]}
            )

    # `activities` 這個總覽指標同樣依現況更新——回填不會改變它的區間，
    # 但同步進來的新活動會，而舊值可能已過時。
    overall = conn.execute(
        "SELECT MIN(DATE(started_at)), MAX(DATE(started_at)) FROM activities WHERE athlete_id = ?",
        (athlete_id,),
    ).fetchone()
    if overall and overall[0]:
        rows.append(
            {"metric_name": "activities", "earliest_date": overall[0], "latest_date": overall[1]}
        )

    count = _upsert_metric_coverage(conn, athlete_id, rows)
    conn.commit()
    return count


def _build_backfill_hr_zones(raw: dict, *, is_api: bool) -> str | None:
    """把 `hrTimeInZone_1..5` 整理成秒數 JSON，依來源套用不同單位。

    ⚠️ 匯出包是毫秒、API 是秒（後者於 2026-09-02 實測確認，見
    `garmin_sync_runner._build_hr_time_in_zone_json()` 的 docstring）。
    Zone 0（靜止）與 Zone 6（裝置 padding）不列入，與同步路徑一致。

    全部區間皆缺值或為 0 時回傳 None——不存全 0 的 JSON，那會讓下游
    誤以為「查過了、確實是 0」而非「根本沒量到」。
    """
    zones: dict[str, int] = {}
    for i in range(1, 6):
        value = raw.get(f"hrTimeInZone_{i}")
        if value is None:
            continue
        zones[str(i)] = round(value) if is_api else round(value / 1000)
    if not zones or all(v == 0 for v in zones.values()):
        return None
    return json.dumps(zones, ensure_ascii=False)


def import_export(conn: sqlite3.Connection, di_connect_dir: Path, athlete_name: str) -> dict:
    """Run the full import for one DI_CONNECT export directory. Returns row counts."""
    user_profile_id = parser.discover_user_profile_id(di_connect_dir)
    if not user_profile_id:
        raise ValueError(f"Could not discover a Garmin userProfileId under {di_connect_dir}")

    athlete_id = resolve_athlete_id(conn, "garmin_export", user_profile_id, athlete_name)

    activity_rows = parser.parse_activities(di_connect_dir)
    wellness_rows = parser.merge_daily_wellness(di_connect_dir)
    coverage_rows = parser.compute_metric_coverage(activity_rows, wellness_rows)

    n_activities = _upsert_activities(conn, athlete_id, activity_rows)
    n_wellness = _upsert_daily_wellness(conn, athlete_id, wellness_rows)
    n_coverage = _upsert_metric_coverage(conn, athlete_id, coverage_rows)
    conn.commit()

    return {
        "athlete_id": athlete_id,
        "user_profile_id": user_profile_id,
        "activities": n_activities,
        "daily_wellness": n_wellness,
        "metric_coverage": n_coverage,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Import a Garmin Connect data export into the local SQLite store.")
    ap.add_argument("--input-dir", help="Directory containing the extracted export (searched recursively for DI_CONNECT)")
    ap.add_argument("--db-path", required=True, help="Path to the SQLite database file (created if missing)")
    ap.add_argument("--athlete-name", help="Display name to use if this athlete is new to the database")
    ap.add_argument(
        "--backfill-metrics",
        action="store_true",
        help="不匯入新資料，改為從既有 raw_data_json 回填 Task 1B-9 的指標欄位（只填 NULL，可重複執行）",
    )
    ap.add_argument("--athlete-id", type=int, default=1, help="回填模式使用的學員 ID（預設 1）")
    ap.add_argument("--dry-run", action="store_true", help="回填模式：只統計會動到幾列，不寫入")
    args = ap.parse_args()

    if args.backfill_metrics:
        conn = get_connection(args.db_path)
        try:
            summary = _backfill_activity_metrics(
                conn, args.athlete_id, dry_run=args.dry_run
            )
            touched = summary.pop("activities_touched")
            prefix = "[dry-run] " if args.dry_run else ""
            print(f"{prefix}回填活動指標：{touched} 筆活動有欄位被補上")
            for column, count in sorted(summary.items()):
                print(f"    {column:34s} {count:4d} 筆有值可填")
            if not args.dry_run:
                n = refresh_activity_metric_coverage(conn, args.athlete_id)
                print(f"已更新 metric_coverage：{n} 個活動類指標的可用區間")
        finally:
            conn.close()
        return

    if not args.input_dir or not args.athlete_name:
        raise SystemExit("匯入模式需要 --input-dir 與 --athlete-name（或改用 --backfill-metrics）")

    base_dir = Path(args.input_dir)
    roots = parser.discover_export_roots(base_dir)
    if not roots:
        raise SystemExit(f"No DI_CONNECT folder found under {base_dir} — is this a Garmin export?")

    conn = get_connection(args.db_path)
    try:
        for di_connect_dir in roots:
            summary = import_export(conn, di_connect_dir, args.athlete_name)
            print(f"Imported {di_connect_dir}: {summary}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
