"""Shared synthetic test fixtures — never real personal export data.

Used by test_garmin_export_parser.py and test_garmin_import_runner.py so the
fixture shape only has to be defined (and kept realistic) in one place.
"""

import json
from pathlib import Path

FAKE_USER_ID = "12345"
FAKE_EMAIL = "athlete@example.com"


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def build_fixture_export(root: Path, user_profile_id: str = FAKE_USER_ID) -> Path:
    """Build a minimal synthetic DI_CONNECT export tree under root and return its path."""
    export_root = root / "11111111-2222-3333-4444-555555555555_1"
    di_connect = export_root / "DI_CONNECT"

    # A 5.00 km run in exactly 1500s (25:00) -> known pace 300 sec/km (5:00/km).
    # distance/duration use Garmin's real units: CENTIMETER and MILLISECOND.
    activity = {
        "activityId": 999001,
        "activityType": "running",
        "userProfileId": int(user_profile_id),
        "name": "測試晨跑",
        "beginTimestamp": 1700000000000,
        "startTimeLocal": 1700028800000,  # +8h offset, like Taiwan local time
        "duration": 1500000.0,  # ms -> 1500s
        "distance": 500000.0,  # cm -> 5.0km
        "movingDuration": 1490000.0,
        "avgHr": 150.0,
        "maxHr": 165.0,
        "aerobicTrainingEffect": 3.2,
        "avgDoubleCadence": 172.0,
        "maxDoubleCadence": 180.0,
        "maxSpeed": 0.4,  # cm/ms -> 4 m/s -> 250 sec/km
        "elevationGain": 500.0,  # cm -> 5m
        "elevationLoss": 500.0,
        "calories": 418.4,  # kJ -> 100 kcal
    }
    _write_json(
        di_connect / "DI-Connect-Fitness" / f"{FAKE_EMAIL}_0_summarizedActivities.json",
        [{"summarizedActivitiesExport": [activity]}],
    )

    health_status = [
        {
            "calendarDate": "2026-01-05",
            "metrics": [
                {"type": "HRV", "value": 45.0},
                {"type": "HR", "value": 55.0},
                {"type": "SPO2", "value": 97.0},
                {"type": "RESPIRATION", "value": 14.5},
                {"type": "SKIN_TEMP_C", "status": "UNKNOWN"},  # no "value" key, as seen in real data
            ],
        }
    ]
    _write_json(
        di_connect / "DI-Connect-Wellness" / f"2026-01-01_2026-01-10_{user_profile_id}_healthStatusData.json",
        health_status,
    )

    sleep_data = [
        {
            "calendarDate": "2026-01-05",
            "deepSleepSeconds": 5400,
            "lightSleepSeconds": 12600,
            "remSleepSeconds": 3600,
            "avgSleepStress": 20.0,
            "sleepScores": {"overallScore": 80, "feedback": "GOOD"},
        }
    ]
    _write_json(
        di_connect / "DI-Connect-Wellness" / f"2026-01-01_2026-01-10_{user_profile_id}_sleepData.json",
        sleep_data,
    )

    readiness = [
        {
            "calendarDate": "2026-01-05",
            "score": 70,
            "hrvWeeklyAverage": 44.0,
            "recoveryTime": 600,  # minutes -> 10.0 hours
            "acwrFactorPercent": 85,
        }
    ]
    _write_json(
        di_connect / "DI-Connect-Metrics" / f"TrainingReadinessDTO_20260101_20260110_{user_profile_id}.json",
        readiness,
    )

    # UDSFile 涵蓋兩天：2026-01-05（healthStatusData 也有 HR，驗證優先用 healthStatusData
    # 而非覆蓋）與 2026-01-06（healthStatusData 沒有這天，驗證會退回用 UDSFile 的 RHR 補值）。
    daily_summary = [
        {
            "calendarDate": "2026-01-05",
            "totalSteps": 8000,
            "restingHeartRate": 999,  # 刻意設一個不合理值，驗證不會蓋掉 healthStatusData 的 55.0
            "allDayStress": {"aggregatorList": [{"type": "TOTAL", "averageStressLevel": 30}]},
        },
        {
            "calendarDate": "2026-01-06",
            "totalSteps": 6000,
            "restingHeartRate": 58,
            "allDayStress": {"aggregatorList": [{"type": "TOTAL", "averageStressLevel": 25}]},
        },
    ]
    _write_json(
        di_connect / "DI-Connect-Aggregator" / f"UDSFile_2026-01-01_2026-01-10.json",
        daily_summary,
    )

    return di_connect
