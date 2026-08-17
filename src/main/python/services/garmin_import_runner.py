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
    ap.add_argument("--input-dir", required=True, help="Directory containing the extracted export (searched recursively for DI_CONNECT)")
    ap.add_argument("--db-path", required=True, help="Path to the SQLite database file (created if missing)")
    ap.add_argument("--athlete-name", required=True, help="Display name to use if this athlete is new to the database")
    args = ap.parse_args()

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
