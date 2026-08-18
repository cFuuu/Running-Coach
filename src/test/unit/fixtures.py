"""Shared synthetic test fixtures — never real personal export data.

Used by test_garmin_export_parser.py, test_garmin_import_runner.py and
test_dashboard_queries.py so the fixture shape only has to be defined
(and kept realistic) in one place.
"""

import json
import sqlite3
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


# ---------------------------------------------------------------------------
# Dashboard 查詢層用的合成資料庫
# ---------------------------------------------------------------------------

# 這組虛構資料的「最新一天」。dashboard_queries.resolve_range() 以資料庫裡的
# 最新日期作為 range 的結束日，所以測試斷言可以直接用這個常數推算視窗邊界。
FAKE_TODAY = "2026-03-10"

FAKE_ATHLETE_NAME = "測試跑者"


def _insert(conn: sqlite3.Connection, table: str, **values) -> int:
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    cur = conn.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(values.values())
    )
    return cur.lastrowid


def build_fixture_db(db_path: Path) -> sqlite3.Connection:
    """建立一個含虛構資料的 SQLite，供 dashboard 查詢層測試使用。

    完全虛構——姓名、日期、心率數字都是為了讓斷言好算而挑的，不是任何真人的資料。
    刻意涵蓋這些邊界情境：

    - activity 100：有 FIT 分圈 + 逐秒 + 心率區間 + 隔天 wellness（完整情境）
    - activity 200：無 FIT 分圈，raw_data_json 有 3 段手動分圈（退回 garmin_manual_lap）
    - activity 300：無 FIT 分圈，手動分圈濾掉雜訊後只剩 1 段（應為 available: false）
    - activity 400：非跑步（strength_training），完全沒有分圈/逐秒資料
    - activity 500：落在 30d 視窗外（用來驗證 range 邊界）
    """
    from src.main.python.models.db import get_connection

    conn = get_connection(db_path)

    athlete_id = _insert(
        conn, "athlete_profile", name=FAKE_ATHLETE_NAME, updated_at="2026-03-10T00:00:00"
    )
    _insert(
        conn,
        "athlete_source_identity",
        athlete_id=athlete_id,
        source="garmin_export",
        external_ref=FAKE_USER_ID,
        created_at="2026-03-10T00:00:00",
    )

    # metric_coverage：hrv_ms 起日刻意設在 30d 視窗之內偏晚，
    # 這樣 range=30d 不 clipped、range=90d 會 clipped，剛好測到旗標兩種狀態。
    for metric, earliest, latest in [
        ("activities", "2020-01-01", "2026-03-10"),
        ("hrv_ms", "2026-02-20", "2026-03-10"),
        ("resting_hr_bpm", "2020-01-01", "2026-03-10"),
        ("steps", "2020-01-01", "2026-03-10"),
    ]:
        _insert(
            conn,
            "metric_coverage",
            athlete_id=athlete_id,
            metric_name=metric,
            source="garmin_export",
            earliest_date=earliest,
            latest_date=latest,
            updated_at="2026-03-10T00:00:00",
        )

    common = {
        "athlete_id": athlete_id,
        "source": "garmin_export",
        "fetched_at": "2026-03-10T00:00:00",
    }

    # --- activity 100：完整資料的一場 10 公里 ---
    a100 = _insert(
        conn,
        "activities",
        **common,
        external_id="10000001",
        activity_type="running",
        title="測試晨跑",
        started_at="2026-03-09T07:00:00",
        distance_km=10.0,
        duration_sec=3000,
        avg_pace_sec_per_km=300,
        avg_hr_bpm=150,
        max_hr_bpm=170,
        avg_cadence_spm=172,
        aerobic_te=3.5,
        elevation_gain_m=20.0,
        calories=600,
        workout_type="easy",
        workout_type_source="auto",
        # hrTimeInZone_N 是 Garmin 匯出的原始單位：毫秒（不是欄位名字面看起來的秒）。
        # 反映真實生產資料形狀：zone 0（低於 Z1 的暖身）非 0、中間 zone 3 為 0
        # （驗證中間 0 值區間仍會保留）、zone 6 恆 0（驗證裝置固定 padding 被丟棄）。
        raw_data_json=json.dumps(
            {
                "hrTimeInZone_0": 300000.0,
                "hrTimeInZone_1": 600000.0,
                "hrTimeInZone_2": 1800000.0,
                "hrTimeInZone_3": 0.0,
                "hrTimeInZone_4": 300000.0,
                "hrTimeInZone_5": 0.0,
                "hrTimeInZone_6": 0.0,
            }
        ),
    )
    for i in range(1, 4):
        _insert(
            conn,
            "activity_laps",
            activity_id=a100,
            lap_index=i,
            distance_km=1.0,
            duration_sec=300.0 + i,
            pace_sec_per_km=300 + i,
            avg_hr_bpm=145 + i,
            max_hr_bpm=155 + i,
        )
    # 逐秒：前半心率固定 100、後半固定 110 → 漂移剛好 +10%（方便斷言）
    for idx, elapsed in enumerate(range(0, 40, 10)):
        _insert(
            conn,
            "activity_records",
            activity_id=a100,
            elapsed_sec=elapsed,
            distance_km=round(elapsed / 300, 4),
            hr_bpm=100 if elapsed <= 15 else 110,
            pace_sec_per_km=300,
            cadence_spm=170,
            altitude_m=10.0,
        )

    # --- activity 200：只有手動分圈可用 ---
    # splits 用 Garmin 匯出的真實結構：每段的數值不是扁平欄位，而是包在
    # measurements 陣列裡、用 fieldEnum 標記（實測 raw_data_json 驗證過）。
    # 單位仍是公分／毫秒：3 段各 3km / 900s，另有一段 type=3（整場總計）與
    # 一段 type=18（雜訊）必須被濾掉。
    def _split(type_, distance_cm, duration_ms, avg_hr=None, max_hr=None):
        measurements = [
            {"fieldEnum": "SUM_DISTANCE", "value": distance_cm},
            {"fieldEnum": "SUM_DURATION", "value": duration_ms},
        ]
        if avg_hr is not None:
            measurements.append({"fieldEnum": "WEIGHTED_MEAN_HEARTRATE", "value": avg_hr})
        if max_hr is not None:
            measurements.append({"fieldEnum": "MAX_HEARTRATE", "value": max_hr})
        return {"type": type_, "measurements": measurements}

    manual_splits = {
        "splits": [
            _split(3, 900000.0, 2700000.0),
            _split(17, 300000.0, 900000.0, avg_hr=140, max_hr=150),
            _split(17, 300000.0, 930000.0, avg_hr=145, max_hr=155),
            _split(17, 300000.0, 870000.0, avg_hr=150, max_hr=160),
            _split(18, 0.0, 0.0),
        ]
    }
    _insert(
        conn,
        "activities",
        **common,
        external_id="10000002",
        activity_type="running",
        title="測試手動分圈跑",
        started_at="2026-03-05T18:00:00",
        distance_km=9.0,
        duration_sec=2700,
        avg_pace_sec_per_km=300,
        avg_hr_bpm=145,
        max_hr_bpm=160,
        workout_type="tempo",
        workout_type_source="auto",
        raw_data_json=json.dumps(manual_splits),
    )

    # --- activity 300：手動分圈濾完只剩 1 段 → 視同無分圈 ---
    _insert(
        conn,
        "activities",
        **common,
        external_id="10000003",
        activity_type="running",
        title="測試單段跑",
        started_at="2026-03-03T06:30:00",
        distance_km=5.0,
        duration_sec=1500,
        avg_pace_sec_per_km=300,
        raw_data_json=json.dumps(
            {
                "splits": [
                    _split(3, 500000.0, 1500000.0),
                    _split(17, 500000.0, 1500000.0),
                ]
            }
        ),
    )

    # --- activity 400：非跑步活動，什麼附加資料都沒有 ---
    _insert(
        conn,
        "activities",
        **common,
        external_id="10000004",
        activity_type="strength_training",
        title="測試重訓",
        started_at="2026-03-02T20:00:00",
        distance_km=0.0,
        duration_sec=2400,
        raw_data_json=None,
    )

    # --- activity 500：30 天視窗之外（2026-03-10 往回 30 天 = 2026-02-09 起）---
    _insert(
        conn,
        "activities",
        **common,
        external_id="10000005",
        activity_type="running",
        title="測試舊資料跑",
        started_at="2026-01-15T07:00:00",
        distance_km=8.0,
        duration_sec=2400,
        avg_pace_sec_per_km=300,
    )

    # --- daily_wellness ---
    # 2026-03-09 是 activity 100 的訓練日，2026-03-10 是隔天：
    # hrv 50 -> 45（delta -5.0、-10.0%）、rhr 50 -> 53（+3）、readiness 80 -> 70（-10）。
    # 2026-03-05（activity 200 的訓練日）刻意讓隔天 2026-03-06 完全沒有 wellness 列，
    # 用來驗證 next_day.available:false。
    # 2026-03-07 的 hrv_ms 是 None，驗證缺值不會被補 0、也不會進 points。
    wellness_rows = [
        ("2026-03-04", 48.0, 51, 75, 20, 30, 9000, 78),
        ("2026-03-05", 49.0, 52, 78, 21, 31, 9500, 79),
        ("2026-03-07", None, 52, 76, 22, 32, 8800, 77),
        ("2026-03-09", 50.0, 50, 80, 19, 29, 12000, 82),
        ("2026-03-10", 45.0, 53, 70, 25, 35, 4000, 68),
    ]
    for date, hrv, rhr, readiness, stress, all_day_stress, steps, sleep_score in wellness_rows:
        _insert(
            conn,
            "daily_wellness",
            **common,
            date=date,
            hrv_ms=hrv,
            resting_hr_bpm=rhr,
            spo2_pct=97.0,
            sleep_score=sleep_score,
            stress_avg=stress,
            all_day_stress_avg=all_day_stress,
            steps=steps,
            training_readiness_score=readiness,
        )

    # --- training_plan：對照 activity 100 當天的計畫課表 ---
    _insert(
        conn,
        "training_plan",
        athlete_id=athlete_id,
        planned_date="2026-03-09",
        workout_type="lsd",
        planned_distance_km=12.0,
        plan_source="ai_coach",
        created_at="2026-03-01T00:00:00",
    )

    conn.commit()
    return conn
