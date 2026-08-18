"""Parse a Garmin Connect official data export (GDPR "Export Your Data" bundle).

This module is intentionally I/O-light and side-effect-free: every function takes a
filesystem path (or already-loaded data) and returns plain dict/list structures ready
to be upserted into the local schema (see models/schema.sql). Database writes live in
garmin_import_runner.py, not here, so this module can be unit-tested without SQLite.

IMPORTANT — multi-user requirement: the export root folder is a random UUID
(e.g. "03e6a13a-6384-.../"), and filenames embed the requester's email and Garmin
userProfileId (e.g. "eter98832@gmail.com_0_summarizedActivities.json",
"90906086_healthStatusData.json"). Every path here must be discovered via glob, never
hardcoded to one person's identifiers, so this parser works for any user's export.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

# Garmin's export encodes distance/elevation in centimeters and duration in
# milliseconds. Verified empirically (2026-08-17) against a known real activity
# (10K, 52:30, 10.11 km): raw duration 3149739.01 / 1000 = 3149.7s = 52:29.7,
# raw distance 1010886.03 / 100000 = 10.109 km — both match. The per-lap
# `measurements[].unitEnum` field in the same record independently confirms
# CENTIMETER / MILLISECOND / CENTIMETERS_PER_MILLISECOND / KILOJOULE.
#
# 以下換算函式刻意是公開的（不加底線前綴）：dashboard_queries.py 讀
# activities.raw_data_json 裡的手動分圈時，需要同一套經真實資料驗證過的換算，
# 不可另外複製一份實作（AGENTS.md：不重複造輪子）。
_CM_PER_KM = 100_000
_CM_PER_M = 100
_MS_PER_SEC = 1000
_KJ_PER_KCAL = 4.184


def cm_to_km(value: float | None) -> float | None:
    return None if value is None else value / _CM_PER_KM


def cm_to_m(value: float | None) -> float | None:
    return None if value is None else value / _CM_PER_M


def ms_to_sec(value: float | None) -> int | None:
    return None if value is None else round(value / _MS_PER_SEC)


def kj_to_kcal(value: float | None) -> int | None:
    return None if value is None else round(value / _KJ_PER_KCAL)


def speed_cm_per_ms_to_pace_sec_per_km(value: float | None) -> int | None:
    """Convert a Garmin CENTIMETERS_PER_MILLISECOND speed field to sec/km pace."""
    if not value or value <= 0:
        return None
    m_per_s = value * 10  # cm/ms -> m/s
    return round(1000 / m_per_s)


def discover_export_roots(base_dir: Path) -> list[Path]:
    """Find every DI_CONNECT directory under base_dir.

    The export's top-level folder name is a random UUID that differs per export and
    per user, so we search for the DI_CONNECT marker rather than assuming a path.
    """
    return sorted({p for p in base_dir.glob("**/DI_CONNECT") if p.is_dir()})


def _load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def discover_user_profile_id(di_connect_dir: Path) -> str | None:
    """Find the Garmin userProfileId for this export, without assuming whose it is."""
    fitness_dir = di_connect_dir / "DI-Connect-Fitness"
    for path in fitness_dir.glob("*_summarizedActivities.json"):
        data = _load_json(path)
        for page in data if isinstance(data, list) else [data]:
            for activity in page.get("summarizedActivitiesExport", []):
                if activity.get("userProfileId"):
                    return str(activity["userProfileId"])

    # Fallback: wellness filenames embed the userProfileId as one underscore-separated
    # token, but its position varies — "<start>_<end>_<id>_<type>.json" for date-ranged
    # series (e.g. healthStatusData) vs "<id>_<type>.json" for latest-snapshot files
    # (e.g. bioMetrics_latest) — so scan all tokens rather than assuming a position.
    # Garmin userProfileIds are large integers (seen: 8 digits); the >=5 digit floor
    # avoids false-matching short numeric tokens.
    wellness_dir = di_connect_dir / "DI-Connect-Wellness"
    for path in wellness_dir.glob("*.json"):
        for token in path.stem.split("_"):
            if token.isdigit() and len(token) >= 5:
                return token
    return None


def parse_activities(di_connect_dir: Path) -> list[dict]:
    """Parse DI-Connect-Fitness/*_summarizedActivities.json into normalized rows."""
    fitness_dir = di_connect_dir / "DI-Connect-Fitness"
    rows: list[dict] = []
    for path in sorted(fitness_dir.glob("*_summarizedActivities.json")):
        data = _load_json(path)
        pages = data if isinstance(data, list) else [data]
        for page in pages:
            for a in page.get("summarizedActivitiesExport", []):
                distance_km = cm_to_km(a.get("distance")) or 0.0
                duration_sec = ms_to_sec(a.get("duration"))
                rows.append(
                    {
                        "external_id": str(a.get("activityId")) if a.get("activityId") else None,
                        "activity_type": a.get("activityType", "unknown"),
                        "title": a.get("name"),
                        "started_at_epoch_ms": a.get("beginTimestamp"),
                        # startTimeLocal reflects the device's local timezone at the time of
                        # the activity; prefer it so "Tuesday's run" means the athlete's Tuesday.
                        "started_at_local_epoch_ms": a.get("startTimeLocal") or a.get("beginTimestamp"),
                        "distance_km": round(distance_km, 4),
                        "duration_sec": duration_sec,
                        "moving_time_sec": ms_to_sec(a.get("movingDuration")),
                        "avg_hr_bpm": a.get("avgHr"),
                        "max_hr_bpm": a.get("maxHr"),
                        "aerobic_te": a.get("aerobicTrainingEffect"),
                        # avgDoubleCadence/maxDoubleCadence are full steps-per-minute
                        # (avgRunCadence is the single-foot half-cadence Garmin also
                        # reports); double-cadence matches how runners usually read
                        # "170-180 spm" cadence targets.
                        "avg_cadence_spm": a.get("avgDoubleCadence"),
                        "max_cadence_spm": a.get("maxDoubleCadence"),
                        "avg_pace_sec_per_km": (
                            round(duration_sec / distance_km)
                            if duration_sec and distance_km > 0
                            else None
                        ),
                        "best_pace_sec_per_km": speed_cm_per_ms_to_pace_sec_per_km(a.get("maxSpeed")),
                        "elevation_gain_m": cm_to_m(a.get("elevationGain")),
                        "elevation_loss_m": cm_to_m(a.get("elevationLoss")),
                        "calories": kj_to_kcal(a.get("calories")),
                        "reps": None,
                        "sets": None,
                        "has_wellness_data": 1 if a.get("avgHr") is not None else 0,
                        "raw_data_json": json.dumps(a, ensure_ascii=False),
                    }
                )
    return rows


def parse_health_status(di_connect_dir: Path) -> dict[str, dict]:
    """Parse DI-Connect-Wellness/*_healthStatusData.json (HRV/RHR/SpO2/skin temp/respiration)."""
    wellness_dir = di_connect_dir / "DI-Connect-Wellness"
    by_date: dict[str, dict] = {}
    metric_field = {
        "HRV": "hrv_ms",
        "HR": "resting_hr_bpm",
        "SPO2": "spo2_pct",
        "SKIN_TEMP_C": "skin_temp_c",
        "RESPIRATION": "respiration_rate",
    }
    for path in sorted(wellness_dir.glob("*_healthStatusData.json")):
        for day in _load_json(path):
            date = day.get("calendarDate")
            if not date:
                continue
            row = by_date.setdefault(date, {})
            for metric in day.get("metrics", []):
                field = metric_field.get(metric.get("type"))
                if field and metric.get("value") is not None:
                    row[field] = metric["value"]
    return by_date


def parse_sleep(di_connect_dir: Path) -> dict[str, dict]:
    """Parse DI-Connect-Wellness/*_sleepData.json into per-date sleep summaries."""
    wellness_dir = di_connect_dir / "DI-Connect-Wellness"
    by_date: dict[str, dict] = {}
    for path in sorted(wellness_dir.glob("*_sleepData.json")):
        for entry in _load_json(path):
            date = entry.get("calendarDate")
            if not date:
                continue
            duration_sec = (
                (entry.get("deepSleepSeconds") or 0)
                + (entry.get("lightSleepSeconds") or 0)
                + (entry.get("remSleepSeconds") or 0)
            )
            scores = entry.get("sleepScores") or {}
            by_date[date] = {
                "sleep_duration_sec": duration_sec or None,
                "sleep_score": scores.get("overallScore"),
                "sleep_quality": scores.get("feedback"),
                "stress_avg": round(entry["avgSleepStress"]) if entry.get("avgSleepStress") is not None else None,
            }
    return by_date


def parse_training_readiness(di_connect_dir: Path) -> dict[str, dict]:
    """Parse DI-Connect-Metrics/TrainingReadinessDTO_*.json.

    Note: TrainingReadinessDTO does not expose a raw ACWR ratio (the classic
    ~0.8-1.3 acute:chronic workload ratio) — only `acwrFactorPercent`, a 0-100
    "how much is ACWR helping/hurting your readiness score" contribution value.
    We store that under `acwr` as the closest available proxy; it is on a 0-100
    scale, not a ratio, so downstream logic must not treat it as literal ACWR.
    """
    metrics_dir = di_connect_dir / "DI-Connect-Metrics"
    by_date: dict[str, dict] = {}
    for path in sorted(metrics_dir.glob("TrainingReadinessDTO_*.json")):
        for entry in _load_json(path):
            date = entry.get("calendarDate")
            if not date:
                continue
            by_date[date] = {
                "training_readiness_score": entry.get("score"),
                "hrv_weekly_avg_ms": entry.get("hrvWeeklyAverage"),
                "recovery_time_hours": (
                    round(entry["recoveryTime"] / 60, 1) if entry.get("recoveryTime") is not None else None
                ),
                "acwr": entry.get("acwrFactorPercent"),
            }
    return by_date


def parse_daily_summary(di_connect_dir: Path) -> dict[str, dict]:
    """Parse DI-Connect-Aggregator/UDSFile_*.json (per-day step count, all-day RHR/stress).

    UDSFile covers a much longer history than healthStatusData (~7 years vs ~10 months
    — verified 2026-08-17), so its resting-heart-rate reading is used as a fallback to
    extend RHR coverage on dates healthStatusData doesn't have, while its stress reading
    is a genuinely different metric (all-day average, not just the sleep window) and is
    kept in its own `all_day_stress_avg` column rather than overwriting `stress_avg`.
    """
    aggregator_dir = di_connect_dir / "DI-Connect-Aggregator"
    by_date: dict[str, dict] = {}
    for path in sorted(aggregator_dir.glob("UDSFile_*.json")):
        for day in _load_json(path):
            date = day.get("calendarDate")
            if not date:
                continue
            all_day_stress = None
            for entry in (day.get("allDayStress") or {}).get("aggregatorList") or []:
                if entry.get("type") == "TOTAL":
                    all_day_stress = entry.get("averageStressLevel")
                    break
            by_date[date] = {
                "steps": day.get("totalSteps"),
                "all_day_rhr_bpm": day.get("restingHeartRate"),
                "all_day_stress_avg": all_day_stress,
            }
    return by_date


def merge_daily_wellness(di_connect_dir: Path) -> list[dict]:
    """Merge health-status, sleep, training-readiness, and daily-summary data into one row per date."""
    health = parse_health_status(di_connect_dir)
    sleep = parse_sleep(di_connect_dir)
    readiness = parse_training_readiness(di_connect_dir)
    daily_summary = parse_daily_summary(di_connect_dir)

    all_dates = set(health) | set(sleep) | set(readiness) | set(daily_summary)
    rows = []
    for date in sorted(all_dates):
        row = {"date": date}
        row.update(health.get(date, {}))
        row.update(sleep.get(date, {}))
        row.update(readiness.get(date, {}))

        summary = daily_summary.get(date, {})
        row["steps"] = summary.get("steps")
        row["all_day_stress_avg"] = summary.get("all_day_stress_avg")
        # resting_hr_bpm：healthStatusData（較精確但只回溯約10個月）優先，
        # 沒有的日期才退回 UDSFile 的全天 RHR（回溯約7年），藉此延長涵蓋範圍。
        if row.get("resting_hr_bpm") is None and summary.get("all_day_rhr_bpm") is not None:
            row["resting_hr_bpm"] = summary["all_day_rhr_bpm"]

        rows.append(row)
    return rows


def compute_metric_coverage(
    activity_rows: Iterable[dict], wellness_rows: Iterable[dict]
) -> list[dict]:
    """Derive earliest/latest available date per metric from parsed rows.

    Different metrics can have wildly different history depth on the same
    account (e.g. activities may go back 7 years while SpO2/HRV only a few
    months, because the underlying feature is newer) — this is a real,
    per-user fact the rule engine needs, not something to assume is uniform.
    """
    import datetime

    coverage: dict[str, list[str]] = {}

    def note(metric: str, date: str) -> None:
        bucket = coverage.setdefault(metric, [date, date])
        if date < bucket[0]:
            bucket[0] = date
        if date > bucket[1]:
            bucket[1] = date

    for a in activity_rows:
        ts = a.get("started_at_local_epoch_ms") or a.get("started_at_epoch_ms")
        if ts:
            date = datetime.datetime.fromtimestamp(ts / 1000, datetime.timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
            note("activities", date)

    wellness_metrics = [
        "hrv_ms",
        "resting_hr_bpm",
        "spo2_pct",
        "skin_temp_c",
        "respiration_rate",
        "sleep_duration_sec",
        "sleep_score",
        "training_readiness_score",
        "hrv_weekly_avg_ms",
        "recovery_time_hours",
        "acwr",
        "steps",
        "all_day_stress_avg",
    ]
    for row in wellness_rows:
        date = row.get("date")
        if not date:
            continue
        for metric in wellness_metrics:
            if row.get(metric) is not None:
                note(metric, date)

    return [
        {"metric_name": metric, "earliest_date": earliest, "latest_date": latest}
        for metric, (earliest, latest) in sorted(coverage.items())
    ]
