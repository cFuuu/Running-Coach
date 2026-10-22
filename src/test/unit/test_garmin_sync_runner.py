"""garmin_sync_runner 的單元測試——合成假資料，不發網路請求、不含真實憑證。

重點：
1. **單位換算**：API 用公尺／秒／大卡，與匯出包的公分／毫秒／千焦耳不同。
   誤用匯出包係數會產生 100～1000 倍錯誤，故特別加迴歸測試守住。
2. **冪等**：重複同步同一區間不得產生重複列。
3. **workout_type 不被覆蓋**：分類結果（尤其人工標註）須保留。
"""

from __future__ import annotations

import datetime
import json
import sqlite3
import unittest

from src.main.python.services import garmin_sync_runner as runner

# 取自 2026-09-01 對真實帳號的實測回傳（活動 23997807612），數值未改，
# 僅保留測試需要的欄位。DB 既有值：10.1586km / 3794s / avgHR 150 / maxHR 161。
REAL_ACTIVITY = {
    "activityId": 23997807612,
    "activityName": "士林區 跑步",
    "startTimeLocal": "2026-08-16 18:43:31",
    "activityType": {"typeKey": "running"},
    "distance": 10158.5703125,
    "duration": 3794.447021484375,
    "movingDuration": 3791.730987548828,
    "averageHR": 150.0,
    "maxHR": 161.0,
    "averageRunningCadenceInStepsPerMinute": 170.84375,
    "maxRunningCadenceInStepsPerMinute": 178.0,
    "maxSpeed": 3.6019999980926514,
    "elevationGain": 12.0,
    "elevationLoss": 8.0,
    "calories": 775.0,
    "aerobicTrainingEffect": 3.5,
}


class TestNormalizeActivityUnits(unittest.TestCase):
    """API 單位為公尺／秒／大卡——迴歸守門，防止誤用匯出包的換算係數。"""

    def setUp(self):
        self.row = runner.normalize_activity(REAL_ACTIVITY)

    def test_distance_metres_to_km(self):
        # 誤用 cm_to_km() 會得到 0.1016（差 100 倍）
        self.assertAlmostEqual(self.row["distance_km"], 10.1585703125, places=6)

    def test_duration_already_seconds(self):
        # 誤用 ms_to_sec() 會得到 4（差約 1000 倍）
        self.assertEqual(self.row["duration_sec"], 3794)

    def test_elevation_already_metres(self):
        # 誤用 cm_to_m() 會得到 0.12
        self.assertEqual(self.row["elevation_gain_m"], 12.0)
        self.assertEqual(self.row["elevation_loss_m"], 8.0)

    def test_calories_already_kcal(self):
        # 誤用 kj_to_kcal() 會得到 185
        self.assertEqual(self.row["calories"], 775)

    def test_matches_values_already_in_db(self):
        """與 DB 既有值一致（實測交叉比對的關鍵欄位）。"""
        self.assertAlmostEqual(self.row["distance_km"], 10.1586, places=3)
        self.assertEqual(self.row["duration_sec"], 3794)
        self.assertEqual(self.row["avg_hr_bpm"], 150)
        self.assertEqual(self.row["max_hr_bpm"], 161)

    def test_pace_uses_rounded_seconds_to_match_db(self):
        """★ 配速分子須為四捨五入後的整數秒，與 export parser 一致。

        本場真值 373.5 s/km 正好在進位邊界：用原始浮點秒（3794.447）得 374，
        用整數秒（3794）得 373，而 DB 既有值為 373。若不對齊，每次重新
        同步都會讓配速在 ±1 秒間跳動，產生假的資料變動。
        """
        self.assertEqual(self.row["avg_pace_sec_per_km"], 373)

    def test_best_pace_from_max_speed_mps(self):
        # 1000 / 3.602 m/s ≈ 278 s/km
        self.assertEqual(self.row["best_pace_sec_per_km"], 278)

    def test_timestamp_normalized_to_iso(self):
        self.assertEqual(self.row["started_at"], "2026-08-16T18:43:31")

    def test_returns_none_without_start_time(self):
        self.assertIsNone(runner.normalize_activity({"activityType": {"typeKey": "running"}}))

    def test_returns_none_without_activity_type(self):
        self.assertIsNone(runner.normalize_activity({"startTimeLocal": "2026-08-16 18:43:31"}))

    def test_handles_missing_optional_fields(self):
        row = runner.normalize_activity(
            {
                "activityId": 1,
                "startTimeLocal": "2026-08-16 18:43:31",
                "activityType": {"typeKey": "running"},
            }
        )
        self.assertIsNotNone(row)
        self.assertIsNone(row["distance_km"])
        self.assertIsNone(row["avg_pace_sec_per_km"])
        self.assertIsNone(row["best_pace_sec_per_km"])


class TestNormalizeWellness(unittest.TestCase):
    """每日身體數據的欄位對應（含黑盒衍生值）。"""

    def _raw(self):
        return {
            "date": "2026-08-31",
            "stats": {
                "restingHeartRate": 49,
                "averageStressLevel": 20,
                "bodyBatteryHighestValue": 77,
                "bodyBatteryLowestValue": 21,
                "totalSteps": 7226,
                "avgWakingRespirationValue": 14.0,
                "averageSpo2": 95.0,
            },
            "sleep": {
                "dailySleepDTO": {
                    "sleepTimeSeconds": "21540",
                    "sleepScores": {"overall": {"value": 74}},
                }
            },
            "hrv": {"hrvSummary": {"weeklyAvg": 53, "lastNightAvg": 49}},
            "training_readiness": [
                {"score": 75, "recoveryTime": 445, "acwrFactorPercent": 87}
            ],
        }

    def test_maps_core_fields(self):
        row = runner.normalize_wellness(self._raw())
        self.assertEqual(row["resting_hr_bpm"], 49)
        self.assertEqual(row["steps"], 7226)
        self.assertEqual(row["hrv_ms"], 49)
        self.assertEqual(row["hrv_weekly_avg_ms"], 53)

    def test_sleep_seconds_string_converted_to_int(self):
        """Garmin 的 sleepTimeSeconds 是字串，須轉為 int。"""
        row = runner.normalize_wellness(self._raw())
        self.assertEqual(row["sleep_duration_sec"], 21540)

    def test_sleep_score_read_from_nested_overall(self):
        row = runner.normalize_wellness(self._raw())
        self.assertEqual(row["sleep_score"], 74)

    def test_recovery_time_minutes_to_hours(self):
        """recoveryTime 是分鐘；DB 欄位為小時。"""
        row = runner.normalize_wellness(self._raw())
        self.assertAlmostEqual(row["recovery_time_hours"], 445 / 60, places=4)

    def test_acwr_stores_factor_percent_not_ratio(self):
        """⚠️ acwr 欄位存的是 0–100 貢獻度，不是古典 0.8–1.3 比值。"""
        row = runner.normalize_wellness(self._raw())
        self.assertEqual(row["acwr"], 87)

    def test_blackbox_values_are_stored_not_dropped(self):
        """黑盒衍生值照抓照存（標記由取用端負責，不在此丟棄）。"""
        row = runner.normalize_wellness(self._raw())
        self.assertEqual(row["training_readiness_score"], 75)
        self.assertEqual(row["body_battery_max"], 77)
        self.assertEqual(row["body_battery_min"], 21)

    def test_returns_none_when_day_has_no_data(self):
        self.assertIsNone(
            runner.normalize_wellness(
                {"date": "2026-08-31", "stats": None, "sleep": None,
                 "hrv": None, "training_readiness": []}
            )
        )

    def test_handles_all_sources_missing_gracefully(self):
        row = runner.normalize_wellness({"date": "2026-08-31", "stats": {"totalSteps": 100}})
        self.assertEqual(row["steps"], 100)
        self.assertIsNone(row["hrv_ms"])
        self.assertIsNone(row["training_readiness_score"])


# 供 TestSyncWritesAndIsIdempotent／TestSyncReportsPlanAndCost／
# TestDedupByExternalId（與繼承自它的 TestSyncFetchesSplitsAndClassifies）
# 共用的完整測試 schema。三個測試類別原本各自內嵌一份幾乎相同的 CREATE
# TABLE 字串，2026-09-02 新增 training_status／performance_metrics 兩表與
# activities／daily_wellness 各 14 個新欄位後，若繼續各自維護三份會違反
# CLAUDE.md「絕不複製貼上程式碼區塊」，故抽成模組級常數集中維護。
# 欄位名稱與型別對齊 schema.sql（不含 CHECK，測試不需要驗證約束合法性）。
FULL_TEST_SCHEMA_SQL = """
CREATE TABLE athlete_profile (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, sex TEXT);
CREATE TABLE activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER NOT NULL, external_id TEXT,
    activity_type TEXT NOT NULL, title TEXT, started_at TEXT NOT NULL,
    distance_km REAL, duration_sec INTEGER, moving_time_sec INTEGER,
    avg_hr_bpm INTEGER, max_hr_bpm INTEGER, aerobic_te REAL,
    avg_cadence_spm INTEGER, max_cadence_spm INTEGER,
    avg_pace_sec_per_km INTEGER, best_pace_sec_per_km INTEGER,
    anaerobic_te REAL, activity_training_load REAL, training_effect_label TEXT,
    avg_grade_adj_pace_sec_per_km INTEGER, avg_ground_contact_time_ms REAL,
    avg_vertical_oscillation_cm REAL, avg_vertical_ratio_pct REAL,
    avg_stride_length_cm REAL, avg_power_watts REAL, max_power_watts REAL,
    norm_power_watts REAL, min_temperature_c REAL, max_temperature_c REAL,
    hr_time_in_zone_json TEXT,
    elevation_gain_m REAL, elevation_loss_m REAL, calories INTEGER,
    reps INTEGER, sets INTEGER, source TEXT NOT NULL, source_version TEXT,
    fetched_at TEXT NOT NULL, has_wellness_data INTEGER NOT NULL DEFAULT 0,
    raw_data_json TEXT, workout_type TEXT, workout_type_source TEXT,
    UNIQUE (athlete_id, started_at, activity_type, distance_km)
);
CREATE UNIQUE INDEX idx_activities_external_id ON activities (athlete_id, external_id);
CREATE TABLE daily_wellness (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER NOT NULL, date TEXT NOT NULL,
    resting_hr_bpm INTEGER, hrv_ms REAL, hrv_weekly_avg_ms REAL,
    spo2_pct REAL, skin_temp_c REAL, respiration_rate REAL,
    sleep_duration_sec INTEGER, sleep_quality TEXT, sleep_score INTEGER,
    stress_avg INTEGER, body_battery_max INTEGER, body_battery_min INTEGER,
    steps INTEGER, training_readiness_score INTEGER,
    recovery_time_hours REAL, acwr REAL, all_day_stress_avg INTEGER,
    hrv_baseline_low_upper REAL, hrv_baseline_balanced_low REAL,
    hrv_baseline_balanced_upper REAL, hrv_status TEXT,
    hrv_last_night_5min_high REAL, acute_load REAL, rhr_7day_avg_bpm INTEGER,
    sleep_deep_sec INTEGER, sleep_light_sec INTEGER, sleep_rem_sec INTEGER,
    sleep_awake_sec INTEGER, body_battery_at_wake INTEGER,
    readiness_input_context TEXT, readiness_timestamp_local TEXT,
    readiness_level TEXT, readiness_feedback_short TEXT,
    sleep_start_local TEXT, sleep_end_local TEXT,
    respiration_sleep_avg REAL, respiration_low REAL, respiration_high REAL,
    spo2_lowest REAL, spo2_latest REAL,
    body_battery_latest INTEGER, body_battery_charged INTEGER, body_battery_drained INTEGER,
    calories_active INTEGER, calories_resting INTEGER,
    intensity_minutes_moderate INTEGER, intensity_minutes_vigorous INTEGER,
    intensity_minutes_weekly_goal INTEGER,
    floors_ascended REAL, floors_goal INTEGER, steps_goal INTEGER,
    source TEXT NOT NULL, source_version TEXT, fetched_at TEXT NOT NULL,
    UNIQUE (athlete_id, date, source)
);
CREATE TABLE wellness_intraday (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER NOT NULL, date TEXT NOT NULL, kind TEXT NOT NULL,
    points_json TEXT NOT NULL,
    source TEXT NOT NULL, source_version TEXT, fetched_at TEXT NOT NULL,
    UNIQUE (athlete_id, date, kind, source)
);
CREATE TABLE training_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER NOT NULL, date TEXT NOT NULL,
    training_status_code INTEGER, training_status_phrase TEXT, status_since_date TEXT,
    acute_load REAL, chronic_load REAL, acwr_ratio REAL,
    chronic_load_optimal_min REAL, chronic_load_optimal_max REAL,
    acwr_status TEXT, vo2max_precise REAL, vo2max_date TEXT,
    heat_acclimation_pct INTEGER, heat_trend TEXT,
    altitude_acclimation_pct INTEGER, fitness_trend INTEGER,
    monthly_load_aerobic_low REAL, monthly_load_aerobic_high REAL,
    monthly_load_anaerobic REAL, training_balance_feedback TEXT,
    source TEXT NOT NULL, source_version TEXT, fetched_at TEXT NOT NULL,
    UNIQUE (athlete_id, date, source)
);
CREATE TABLE performance_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER NOT NULL, date TEXT NOT NULL,
    race_predict_5k_sec INTEGER, race_predict_10k_sec INTEGER,
    race_predict_half_sec INTEGER, race_predict_marathon_sec INTEGER,
    lactate_threshold_hr_bpm INTEGER, lactate_threshold_pace_sec_per_km INTEGER,
    functional_threshold_power INTEGER, endurance_score INTEGER,
    endurance_classification INTEGER, endurance_contributors_json TEXT,
    fitness_age REAL, chronological_age INTEGER, achievable_fitness_age REAL,
    hill_score INTEGER,
    source TEXT NOT NULL, source_version TEXT, fetched_at TEXT NOT NULL,
    UNIQUE (athlete_id, date, source)
);
CREATE TABLE activity_laps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL REFERENCES activities(id),
    lap_index INTEGER NOT NULL, distance_km REAL, duration_sec REAL,
    pace_sec_per_km INTEGER, avg_hr_bpm INTEGER, max_hr_bpm INTEGER,
    UNIQUE (activity_id, lap_index)
);
CREATE TABLE metric_coverage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER NOT NULL, metric_name TEXT NOT NULL,
    source TEXT NOT NULL, earliest_date TEXT, latest_date TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (athlete_id, metric_name, source)
);
INSERT INTO athlete_profile (name) VALUES ('Test');
"""


class FakeApi:
    """假的 API client——回傳固定資料，不發網路請求。"""

    def __init__(self, activities=None, wellness=None, splits=None, performance=None, sex=None):
        self._activities = activities if activities is not None else [REAL_ACTIVITY]
        self._wellness = wellness or {}
        self._splits = splits or {}
        self._performance = performance if performance is not None else {}
        self._sex = sex
        self.activity_calls = 0
        self.split_calls = []
        self.sex_calls = 0

    def fetch_user_sex(self):
        self.sex_calls += 1
        return self._sex

    def fetch_activities(self, start_date, end_date):
        self.activity_calls += 1
        return list(self._activities)

    def fetch_daily_wellness_raw(self, cdate):
        return self._wellness.get(cdate.isoformat(), {"date": cdate.isoformat()})

    def fetch_activity_splits(self, activity_id):
        self.split_calls.append(str(activity_id))
        return list(self._splits.get(str(activity_id), []))

    def fetch_performance_metrics_raw(self, cdate):
        return dict(self._performance)


class TestSyncWritesAndIsIdempotent(unittest.TestCase):
    """同步寫入資料庫並保證冪等。"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(FULL_TEST_SCHEMA_SQL)
        self.today = datetime.date(2026, 8, 16)

    def tearDown(self):
        self.conn.close()

    def _count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_writes_activity(self):
        result = runner.sync(self.conn, 1, days=1, client=FakeApi(), today=self.today)
        self.assertTrue(result["available"])
        self.assertEqual(result["activities_synced"], 1)
        self.assertEqual(self._count("activities"), 1)

    def test_repeated_sync_does_not_duplicate(self):
        """★ 冪等性：同一區間跑兩次仍只有一列。"""
        api = FakeApi()
        runner.sync(self.conn, 1, days=1, client=api, today=self.today)
        first = self._count("activities")
        runner.sync(self.conn, 1, days=1, client=api, today=self.today)
        self.assertEqual(self._count("activities"), first)
        self.assertEqual(first, 1)

    def test_resync_does_not_overwrite_workout_type(self):
        """★ 分類結果（尤其人工標註）不可被同步覆蓋。"""
        api = FakeApi()
        runner.sync(self.conn, 1, days=1, client=api, today=self.today)
        self.conn.execute(
            "UPDATE activities SET workout_type='interval', workout_type_source='manual'"
        )
        self.conn.commit()
        runner.sync(self.conn, 1, days=1, client=api, today=self.today)
        row = self.conn.execute(
            "SELECT workout_type, workout_type_source FROM activities"
        ).fetchone()
        self.assertEqual(row["workout_type"], "interval")
        self.assertEqual(row["workout_type_source"], "manual")

    def test_writes_wellness_row(self):
        wellness = {
            "2026-08-16": {
                "date": "2026-08-16",
                "stats": {"totalSteps": 7226, "restingHeartRate": 49},
                "sleep": None, "hrv": None, "training_readiness": [],
            }
        }
        result = runner.sync(
            self.conn, 1, days=1, client=FakeApi(wellness=wellness), today=self.today
        )
        self.assertEqual(result["wellness_days_synced"], 1)
        row = self.conn.execute("SELECT steps, source FROM daily_wellness").fetchone()
        self.assertEqual(row["steps"], 7226)
        # source 沿用 garmin_export，避免 daily_wellness 同日出現兩列
        self.assertEqual(row["source"], "garmin_export")

    def test_wellness_resync_does_not_duplicate(self):
        wellness = {
            "2026-08-16": {
                "date": "2026-08-16",
                "stats": {"totalSteps": 7226}, "sleep": None,
                "hrv": None, "training_readiness": [],
            }
        }
        api = FakeApi(wellness=wellness)
        runner.sync(self.conn, 1, days=1, client=api, today=self.today)
        runner.sync(self.conn, 1, days=1, client=api, today=self.today)
        self.assertEqual(self._count("daily_wellness"), 1)

    def test_wellness_upsert_preserves_existing_value_when_new_is_null(self):
        """COALESCE：新資料缺值時不可把既有值洗成 NULL。"""
        self.conn.execute(
            "INSERT INTO daily_wellness (athlete_id, date, hrv_ms, source, fetched_at) "
            "VALUES (1, '2026-08-16', 42.0, 'garmin_export', 'x')"
        )
        self.conn.commit()
        wellness = {
            "2026-08-16": {
                "date": "2026-08-16",
                "stats": {"totalSteps": 100}, "sleep": None,
                "hrv": None, "training_readiness": [],
            }
        }
        runner.sync(self.conn, 1, days=1, client=FakeApi(wellness=wellness), today=self.today)
        row = self.conn.execute("SELECT hrv_ms, steps FROM daily_wellness").fetchone()
        self.assertEqual(row["hrv_ms"], 42.0)  # 既有值保留
        self.assertEqual(row["steps"], 100)  # 新值寫入

    def test_skips_unnormalizable_activities(self):
        api = FakeApi(activities=[REAL_ACTIVITY, {"activityId": 999}])
        result = runner.sync(self.conn, 1, days=1, client=api, today=self.today)
        self.assertEqual(result["activities_synced"], 1)
        self.assertEqual(result["activities_skipped"], 1)

    def test_refreshes_metric_coverage(self):
        """★ 迴歸：同步必須更新 metric_coverage。

        實測發現過的落差——wellness 已寫到 09-01，metric_coverage 卻仍停在
        上次匯出的 08-16，導致 get_meta() 回報的可用範圍過時。
        """
        wellness = {
            "2026-08-16": {
                "date": "2026-08-16",
                "stats": {"restingHeartRate": 49, "totalSteps": 100},
                "sleep": None, "hrv": None, "training_readiness": [],
            }
        }
        runner.sync(self.conn, 1, days=1, client=FakeApi(wellness=wellness), today=self.today)
        row = self.conn.execute(
            "SELECT earliest_date, latest_date FROM metric_coverage "
            "WHERE metric_name = 'resting_hr_bpm'"
        ).fetchone()
        self.assertIsNotNone(row, "resting_hr_bpm 應有 metric_coverage 列")
        self.assertEqual(row["latest_date"], "2026-08-16")

    def test_metric_coverage_widens_existing_range(self):
        """既有範圍應被擴張而非取代（MIN/MAX 合併）。"""
        self.conn.execute(
            "INSERT INTO metric_coverage (athlete_id, metric_name, source, "
            "earliest_date, latest_date, updated_at) "
            "VALUES (1, 'steps', 'garmin_export', '2020-01-01', '2020-06-01', 'x')"
        )
        self.conn.commit()
        wellness = {
            "2026-08-16": {
                "date": "2026-08-16", "stats": {"totalSteps": 100},
                "sleep": None, "hrv": None, "training_readiness": [],
            }
        }
        runner.sync(self.conn, 1, days=1, client=FakeApi(wellness=wellness), today=self.today)
        row = self.conn.execute(
            "SELECT earliest_date, latest_date FROM metric_coverage WHERE metric_name='steps'"
        ).fetchone()
        self.assertEqual(row["earliest_date"], "2020-01-01")  # 舊的起點保留
        self.assertEqual(row["latest_date"], "2026-08-16")  # 終點推進

    def test_rejects_non_positive_days(self):
        with self.assertRaises(ValueError):
            runner.sync(self.conn, 1, days=0, client=FakeApi(), today=self.today)


class TestResolveSyncDays(unittest.TestCase):
    """★ 自動補齊：依資料庫現況回推需要同步幾天，避免長期未同步時漏資料。

    設計依據（2026-09-01 與 Fu 議定）：
    1. 斷點取 activities／daily_wellness **兩者較舊**的最新日期——兩表天生
       不同步（沒運動的日子沒有活動列），取較新的會讓活動出現永久缺口。
    2. 往前多抓 `OVERLAP_DAYS` 天：Garmin 的睡眠分數／HRV／Training Readiness
       常隔日才算出，當天抓到的列欄位不全，需重抓補齊（upsert 用 COALESCE，
       重抓只補空值不覆寫，故無副作用）。
    3. 自動模式上限 `MAX_AUTO_SYNC_DAYS`，更早的缺口應走官方匯出包。
    """

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                athlete_id INTEGER NOT NULL, activity_type TEXT NOT NULL,
                started_at TEXT NOT NULL, source TEXT NOT NULL, fetched_at TEXT NOT NULL
            );
            CREATE TABLE daily_wellness (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                athlete_id INTEGER NOT NULL, date TEXT NOT NULL,
                source TEXT NOT NULL, fetched_at TEXT NOT NULL
            );
            """
        )
        self.today = datetime.date(2026, 9, 1)

    def tearDown(self):
        self.conn.close()

    def _add_activity(self, started_at):
        self.conn.execute(
            "INSERT INTO activities (athlete_id, activity_type, started_at, source, "
            "fetched_at) VALUES (1, 'running', ?, 'garmin_export', 'x')",
            (started_at,),
        )

    def _add_wellness(self, date):
        self.conn.execute(
            "INSERT INTO daily_wellness (athlete_id, date, source, fetched_at) "
            "VALUES (1, ?, 'garmin_export', 'x')",
            (date,),
        )

    def test_uses_older_of_two_tables_as_breakpoint(self):
        """★ 斷點取較舊者：活動停在 08-25、wellness 停在 09-01 → 從 08-25 起算。"""
        self._add_activity("2026-08-25T18:00:00")
        self._add_wellness("2026-09-01")
        self.conn.commit()
        days = runner.resolve_sync_days(self.conn, 1, today=self.today)
        # 08-25 往前 OVERLAP_DAYS 天為起點，到 09-01 共 8 + overlap 天
        expected = 8 + runner.OVERLAP_DAYS
        self.assertEqual(days, expected)

    def test_includes_overlap_days_for_delayed_metrics(self):
        """★ 即使資料已同步到今天，仍回抓 overlap 天補延遲寫入的指標。"""
        self._add_activity("2026-09-01T18:00:00")
        self._add_wellness("2026-09-01")
        self.conn.commit()
        days = runner.resolve_sync_days(self.conn, 1, today=self.today)
        self.assertEqual(days, 1 + runner.OVERLAP_DAYS)

    def test_clamps_to_max_auto_sync_days(self):
        """★ 長期未同步時夾到上限，不可一次打上千次 API。"""
        self._add_activity("2020-01-01T18:00:00")
        self._add_wellness("2020-01-01")
        self.conn.commit()
        days = runner.resolve_sync_days(self.conn, 1, today=self.today)
        self.assertEqual(days, runner.MAX_AUTO_SYNC_DAYS)

    def test_falls_back_to_default_when_no_data(self):
        """空資料庫（尚未匯入歷史）不應推導出巨大區間。"""
        days = runner.resolve_sync_days(self.conn, 1, today=self.today)
        self.assertEqual(days, runner.DEFAULT_SYNC_DAYS)

    def test_handles_only_wellness_present(self):
        """只有 wellness 有資料時以它為準，不因活動表為空而 fallback。"""
        self._add_wellness("2026-08-30")
        self.conn.commit()
        days = runner.resolve_sync_days(self.conn, 1, today=self.today)
        self.assertEqual(days, 3 + runner.OVERLAP_DAYS)

    def test_future_dated_row_does_not_produce_zero_or_negative(self):
        """資料日期超前今天（時區差）時仍須回傳有效天數。"""
        self._add_activity("2026-09-05T18:00:00")
        self._add_wellness("2026-09-05")
        self.conn.commit()
        days = runner.resolve_sync_days(self.conn, 1, today=self.today)
        self.assertGreaterEqual(days, 1)


class TestEstimateSyncCost(unittest.TestCase):
    """★ 同步前的耗時預告——讓使用者在打 API 前知道成本。

    成本結構：活動只打 1 次（吃日期區間），wellness 逐日打 7 個端點
    （stats／sleep／hrv／training_readiness／training_status／
    stress_intraday／heart_rate_intraday；2026-09-02 由 4 → 5，
    2026-10-22 由 5 → 7），每次間隔 1 秒節流。
    """

    def test_request_count_is_one_plus_seven_per_day(self):
        est = runner.estimate_sync_cost(7)
        self.assertEqual(est["request_count"], 1 + 7 * 7)

    def test_estimated_seconds_follows_throttle_interval(self):
        est = runner.estimate_sync_cost(30)
        expected_requests = 1 + 30 * 7
        self.assertEqual(est["request_count"], expected_requests)
        self.assertAlmostEqual(
            est["estimated_seconds"],
            expected_requests * runner.REQUEST_INTERVAL_SEC,
            places=3,
        )

    def test_ninety_days_stays_within_reasonable_bound(self):
        """90 天上限的實際成本：631 次請求、約 10.5 分鐘。"""
        est = runner.estimate_sync_cost(runner.MAX_AUTO_SYNC_DAYS)
        self.assertEqual(est["request_count"], 631)
        self.assertLess(est["estimated_seconds"], 11 * 60)

    def test_human_readable_text_included(self):
        est = runner.estimate_sync_cost(7)
        self.assertIn("50", est["summary"])


class TestSyncReportsPlanAndCost(unittest.TestCase):
    """sync() 須回報實際採用的天數與預估成本，供 CLI／MCP 告知使用者。"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(FULL_TEST_SCHEMA_SQL)
        self.today = datetime.date(2026, 8, 16)

    def tearDown(self):
        self.conn.close()

    def test_days_omitted_triggers_auto_resolution(self):
        """★ 不給 days 時自動算 gap，而非沿用固定 7 天。"""
        self.conn.execute(
            "INSERT INTO daily_wellness (athlete_id, date, source, fetched_at) "
            "VALUES (1, '2026-08-10', 'garmin_export', 'x')"
        )
        self.conn.commit()
        result = runner.sync(self.conn, 1, client=FakeApi(), today=self.today)
        self.assertTrue(result["days_auto_resolved"])
        self.assertEqual(result["days"], 7 + runner.OVERLAP_DAYS)

    def test_explicit_days_overrides_auto(self):
        result = runner.sync(self.conn, 1, days=3, client=FakeApi(), today=self.today)
        self.assertFalse(result["days_auto_resolved"])
        self.assertEqual(result["days"], 3)

    def test_result_carries_cost_estimate(self):
        result = runner.sync(self.conn, 1, days=2, client=FakeApi(), today=self.today)
        self.assertEqual(result["estimate"]["request_count"], 1 + 2 * 7)

    def test_truncated_flag_when_gap_exceeds_max(self):
        """★ gap 超過上限時須明確標記，讓呼叫端提示改走匯出包。"""
        self.conn.execute(
            "INSERT INTO daily_wellness (athlete_id, date, source, fetched_at) "
            "VALUES (1, '2020-01-01', 'garmin_export', 'x')"
        )
        self.conn.commit()
        result = runner.sync(self.conn, 1, client=FakeApi(), today=self.today)
        self.assertTrue(result["gap_truncated"])
        self.assertEqual(result["days"], runner.MAX_AUTO_SYNC_DAYS)

    def test_no_truncation_flag_within_limit(self):
        self.conn.execute(
            "INSERT INTO daily_wellness (athlete_id, date, source, fetched_at) "
            "VALUES (1, '2026-08-14', 'garmin_export', 'x')"
        )
        self.conn.commit()
        result = runner.sync(self.conn, 1, client=FakeApi(), today=self.today)
        self.assertFalse(result["gap_truncated"])

    def test_dry_run_does_not_touch_api_or_db(self):
        """★ dry-run：只回報計畫與成本，不打 API、不寫入。"""
        api = FakeApi()
        result = runner.sync(self.conn, 1, days=5, client=api, today=self.today, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["days"], 5)
        self.assertEqual(api.activity_calls, 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0], 0
        )


# 取自 2026-09-01 對真實帳號 get_activity_splits(24171213902) 的回傳，
# 保留測試需要的欄位。該場為 15.1km、16 圈整齊每公里分圈。
REAL_LAP_DTOS = [
    {"lapIndex": i, "distance": 1000.0, "duration": d, "averageHR": hr, "maxHR": hr + 8}
    for i, (d, hr) in enumerate(
        [
            (378.066, 130.0), (389.0, 141.0), (372.0, 146.0), (372.0, 147.0),
            (378.0, 148.0), (382.0, 150.0), (373.0, 151.0), (367.0, 153.0),
            (372.0, 154.0), (376.0, 155.0), (373.0, 151.0), (365.0, 149.0),
            (372.0, 155.0), (371.0, 155.0), (368.0, 159.0),
        ],
        start=1,
    )
] + [{"lapIndex": 16, "distance": 112.0, "duration": 40.2, "averageHR": 157.0, "maxHR": 160.0}]


class TestNormalizeLaps(unittest.TestCase):
    """★ 逐圈明細正規化——API 的 lapDTOs 轉為 activity_laps 的欄位。

    ⚠️ 分圈明細**不在**活動摘要端點內：`get_activities_by_date` 只回
    `splitSummaries`（按跑/走/站類型彙總，給總和與平均，無逐圈序列），
    需另打 `get_activity_splits`。實測 2026-08-30 那場 `lapCount=16`，
    摘要僅告知圈數存在，明細要另一個端點才拿得到。
    """

    def test_converts_metres_and_seconds(self):
        rows = runner.normalize_laps(REAL_LAP_DTOS)
        self.assertEqual(len(rows), 16)
        self.assertAlmostEqual(rows[0]["distance_km"], 1.0)
        self.assertAlmostEqual(rows[0]["duration_sec"], 378.066, places=3)

    def test_pace_computed_per_lap(self):
        rows = runner.normalize_laps(REAL_LAP_DTOS)
        self.assertEqual(rows[0]["pace_sec_per_km"], 378)

    def test_lap_index_preserved(self):
        rows = runner.normalize_laps(REAL_LAP_DTOS)
        self.assertEqual([r["lap_index"] for r in rows][:3], [1, 2, 3])

    def test_hr_rounded_to_int(self):
        rows = runner.normalize_laps(REAL_LAP_DTOS)
        self.assertEqual(rows[0]["avg_hr_bpm"], 130)
        self.assertEqual(rows[0]["max_hr_bpm"], 138)

    def test_zero_distance_lap_has_no_pace(self):
        rows = runner.normalize_laps(
            [{"lapIndex": 1, "distance": 0.0, "duration": 30.0, "averageHR": 120.0}]
        )
        self.assertIsNone(rows[0]["pace_sec_per_km"])

    def test_empty_input(self):
        self.assertEqual(runner.normalize_laps([]), [])

    def test_real_laps_classify_as_lsd(self):
        """★ 端到端：真實分圈餵進既有分類器應得 lsd（15.1km 為近期均值 1.5 倍以上）。

        這是本功能的目的——讓同步進來的活動走**與歷史 243 場相同**的分類標準，
        而非另建一套依心率區間推算的分類器（那會在同一欄位混入兩種語意）。
        """
        from src.main.python.services.workout_classifier import classify_workout

        laps = runner.normalize_laps(REAL_LAP_DTOS)
        self.assertEqual(
            classify_workout(
                laps,
                total_distance_km=15.11,
                recent_avg_distance_km=10.0,
                recent_avg_pace_sec_per_km=365,
            ),
            "lsd",
        )


class TestDedupByExternalId(unittest.TestCase):
    """★ 迴歸：同一場活動不得因浮點距離精度差異而寫成兩列。

    實測發現的 bug（2026-09-01）：UNIQUE 約束用
    `(athlete_id, started_at, activity_type, distance_km)`，但 API 回傳完整
    浮點數（10.1585703125），匯出包存四捨五入值（10.1586）——distance_km
    不相等，UNIQUE 擋不住，同一場跑步出現兩列。實測有 2 組（8/13、8/16），
    正落在 API 與匯出包的來源重疊區。

    修法：改以 `external_id`（Garmin activityId，穩定唯一鍵）為去重依據。
    實測 530 筆活動 external_id 全部非 NULL，且兩組重複列的 external_id 相同。
    """

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(FULL_TEST_SCHEMA_SQL)
        self.today = datetime.date(2026, 8, 16)

    def tearDown(self):
        self.conn.close()

    def test_rounded_existing_row_is_updated_not_duplicated(self):
        """★ 匯出包已存四捨五入距離，API 帶完整浮點數來時應更新同一列。"""
        self.conn.execute(
            """INSERT INTO activities (athlete_id, external_id, activity_type, started_at,
               distance_km, source, fetched_at, workout_type, workout_type_source)
               VALUES (1, '23997807612', 'running', '2026-08-16T18:43:31', 10.1586,
                       'garmin_export', 'x', 'easy', 'auto')"""
        )
        self.conn.commit()
        runner.sync(self.conn, 1, days=1, client=FakeApi(), today=self.today)
        rows = self.conn.execute("SELECT * FROM activities").fetchall()
        self.assertEqual(len(rows), 1, "同一 external_id 不得產生第二列")
        # 既有的人工/自動分類不得被同步覆蓋
        self.assertEqual(rows[0]["workout_type"], "easy")

    def test_manual_workout_type_survives_dedup_update(self):
        self.conn.execute(
            """INSERT INTO activities (athlete_id, external_id, activity_type, started_at,
               distance_km, source, fetched_at, workout_type, workout_type_source)
               VALUES (1, '23997807612', 'running', '2026-08-16T18:43:31', 10.1586,
                       'garmin_export', 'x', 'interval', 'manual')"""
        )
        self.conn.commit()
        runner.sync(self.conn, 1, days=1, client=FakeApi(), today=self.today)
        row = self.conn.execute("SELECT workout_type, workout_type_source FROM activities").fetchone()
        self.assertEqual(row["workout_type"], "interval")
        self.assertEqual(row["workout_type_source"], "manual")


class TestSyncFetchesSplitsAndClassifies(unittest.TestCase):
    """★ 同步時補抓分圈並套用既有分類器。"""

    def setUp(self):
        TestDedupByExternalId.setUp(self)

    def tearDown(self):
        self.conn.close()

    def test_laps_written_for_running_activity(self):
        api = FakeApi(splits={"23997807612": REAL_LAP_DTOS})
        result = runner.sync(
            self.conn, 1, days=1, client=api, today=self.today, fetch_splits=True
        )
        self.assertEqual(result["laps_synced"], 16)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM activity_laps").fetchone()[0], 16
        )

    def test_workout_type_classified_from_fetched_laps(self):
        """★ 這是整個功能的目的：同步進來的活動不再是 workout_type NULL。"""
        api = FakeApi(splits={"23997807612": REAL_LAP_DTOS})
        runner.sync(self.conn, 1, days=1, client=api, today=self.today, fetch_splits=True)
        row = self.conn.execute(
            "SELECT workout_type, workout_type_source FROM activities"
        ).fetchone()
        self.assertIsNotNone(row["workout_type"])
        self.assertEqual(row["workout_type_source"], "auto")

    def test_splits_not_fetched_when_disabled(self):
        api = FakeApi(splits={"23997807612": REAL_LAP_DTOS})
        runner.sync(self.conn, 1, days=1, client=api, today=self.today, fetch_splits=False)
        self.assertEqual(api.split_calls, [])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM activity_laps").fetchone()[0], 0
        )

    def test_only_running_activities_get_splits(self):
        """重訓/健走不抓分圈——配速結構對它們無意義（比照 fit_import_runner 慣例）。"""
        strength = {
            "activityId": 999,
            "startTimeLocal": "2026-08-16 07:00:00",
            "activityType": {"typeKey": "strength_training"},
            "distance": 0.0,
            "duration": 3600.0,
        }
        api = FakeApi(activities=[strength], splits={"999": REAL_LAP_DTOS})
        runner.sync(self.conn, 1, days=1, client=api, today=self.today, fetch_splits=True)
        self.assertEqual(api.split_calls, [])

    def test_split_fetch_failure_does_not_abort_sync(self):
        """單場分圈抓不到不應讓整批同步失敗——活動摘要仍要寫入。"""

        class FailingSplits(FakeApi):
            def fetch_activity_splits(self, activity_id):
                raise runner.GarminFetchError("抓取失敗（Timeout）")

        result = runner.sync(
            self.conn, 1, days=1, client=FailingSplits(), today=self.today, fetch_splits=True
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["activities_synced"], 1)
        self.assertEqual(result["laps_synced"], 0)

    def test_rate_limit_during_splits_aborts(self):
        """★ 429 一律中止整批——不可繼續打，限制以 IP 累積。"""

        class RateLimited(FakeApi):
            def fetch_activity_splits(self, activity_id):
                raise runner.GarminRateLimited("觸發 Garmin rate limit")

        result = runner.sync(
            self.conn, 1, days=1, client=RateLimited(), today=self.today, fetch_splits=True
        )
        self.assertFalse(result["available"])
        self.assertTrue(result["rate_limited"])

    def test_existing_laps_not_refetched(self):
        """★ 已有分圈的活動不重抓——省 API，也是冪等的一部分。"""
        api = FakeApi(splits={"23997807612": REAL_LAP_DTOS})
        runner.sync(self.conn, 1, days=1, client=api, today=self.today, fetch_splits=True)
        first_calls = len(api.split_calls)
        runner.sync(self.conn, 1, days=1, client=api, today=self.today, fetch_splits=True)
        self.assertEqual(len(api.split_calls), first_calls, "第二次不應重抓分圈")

    def test_cost_estimate_accounts_for_splits(self):
        """耗時預告須把分圈請求算進去，否則低估。"""
        est = runner.estimate_sync_cost(7, expected_activities=5)
        self.assertEqual(est["request_count"], 1 + 7 * 7 + 5)


# ============================================================================
# 2026-09-02：Task 1B-9 Garmin 指標擴充——以下為新增測試
# ============================================================================


class TestNormalizeActivityNewFields(unittest.TestCase):
    """★ Task 1B-9：activities 新增 14 欄的正規化。"""

    def _raw(self, **overrides):
        raw = dict(REAL_ACTIVITY)
        raw.update(overrides)
        return raw

    def test_anaerobic_te_and_training_load(self):
        row = runner.normalize_activity(
            self._raw(anaerobicTrainingEffect=2.1, activityTrainingLoad=180.5,
                      trainingEffectLabel="TEMPO")
        )
        self.assertEqual(row["anaerobic_te"], 2.1)
        self.assertEqual(row["activity_training_load"], 180.5)
        self.assertEqual(row["training_effect_label"], "TEMPO")

    def test_grade_adjusted_pace_from_speed(self):
        # 4.0 m/s -> 1000/4.0 = 250 s/km
        row = runner.normalize_activity(self._raw(avgGradeAdjustedSpeed=4.0))
        self.assertEqual(row["avg_grade_adj_pace_sec_per_km"], 250)

    def test_grade_adjusted_pace_none_when_speed_missing(self):
        row = runner.normalize_activity(self._raw())
        self.assertIsNone(row["avg_grade_adj_pace_sec_per_km"])

    def test_running_dynamics_passed_through_unconverted(self):
        row = runner.normalize_activity(
            self._raw(
                avgGroundContactTime=245.0,
                avgVerticalOscillation=8.9,
                avgVerticalRatio=7.2,
                avgStrideLength=118.4,
            )
        )
        self.assertEqual(row["avg_ground_contact_time_ms"], 245.0)
        self.assertEqual(row["avg_vertical_oscillation_cm"], 8.9)
        self.assertEqual(row["avg_vertical_ratio_pct"], 7.2)
        self.assertEqual(row["avg_stride_length_cm"], 118.4)

    def test_power_fields_passed_through_unconverted(self):
        row = runner.normalize_activity(
            self._raw(avgPower=250.0, maxPower=410.0, normPower=265.0)
        )
        self.assertEqual(row["avg_power_watts"], 250.0)
        self.assertEqual(row["max_power_watts"], 410.0)
        self.assertEqual(row["norm_power_watts"], 265.0)

    def test_temperature_stored_raw_without_transformation(self):
        """⚠️ 手腕體溫污染值，正規化層只照存，不做任何加工／判讀。"""
        row = runner.normalize_activity(self._raw(minTemperature=27.0, maxTemperature=29.0))
        self.assertEqual(row["min_temperature_c"], 27.0)
        self.assertEqual(row["max_temperature_c"], 29.0)

    def test_missing_new_fields_default_to_none(self):
        row = runner.normalize_activity(self._raw())
        for key in (
            "anaerobic_te", "activity_training_load", "training_effect_label",
            "avg_grade_adj_pace_sec_per_km", "avg_ground_contact_time_ms",
            "avg_vertical_oscillation_cm", "avg_vertical_ratio_pct",
            "avg_stride_length_cm", "avg_power_watts", "max_power_watts",
            "norm_power_watts", "min_temperature_c", "max_temperature_c",
            "hr_time_in_zone_json",
        ):
            self.assertIsNone(row[key], f"{key} 應為 None")


class TestBuildHrTimeInZoneJson(unittest.TestCase):
    """★ hrTimeInZone_1..5：此 payload 的欄位值**已經是秒**，不可再換算。

    2026-09-02 對真實帳號實測推翻了先前「是毫秒」的假設——那是官方匯出包
    才需要的換算。誤套 `ms_to_sec()` 會把 5197 秒壓成 5 秒。
    """

    def test_values_are_already_seconds(self):
        """真實 payload（2026-08-30 該場，duration=5648 秒）。

        區間總和 5517 秒與時長相符；若誤除以 1000 總和只剩 5 秒。
        """
        raw = {
            "hrTimeInZone_1": 203.994,
            "hrTimeInZone_2": 5197.439,
            "hrTimeInZone_3": 116.002,
            "hrTimeInZone_4": 0.0,
            "hrTimeInZone_5": 0.0,
        }
        result = json.loads(runner._build_hr_time_in_zone_json(raw))
        self.assertEqual(result["1"], 204)
        self.assertEqual(result["2"], 5197)
        self.assertEqual(result["3"], 116)

    def test_zone_total_matches_activity_duration(self):
        """★ 單位迴歸守門：區間總和須與活動時長同一數量級。

        這是能抓出 1000 倍單位錯誤的斷言——先前的實作通過了逐欄位測試，
        卻在這個關係上失敗。
        """
        raw = {
            "hrTimeInZone_1": 203.994,
            "hrTimeInZone_2": 5197.439,
            "hrTimeInZone_3": 116.002,
            "hrTimeInZone_4": 0.0,
            "hrTimeInZone_5": 0.0,
        }
        duration_sec = 5648
        total = sum(json.loads(runner._build_hr_time_in_zone_json(raw)).values())
        self.assertGreater(total, duration_sec * 0.5)
        self.assertLessEqual(total, duration_sec)

    def test_zone_0_and_6_excluded(self):
        """Zone 0（暖身/靜止）與 Zone 6（裝置 padding）不列入，只收 1~5。"""
        raw = {
            "hrTimeInZone_0": 50.0,
            "hrTimeInZone_1": 10.0,
            "hrTimeInZone_6": 0.0,
        }
        result = json.loads(runner._build_hr_time_in_zone_json(raw))
        self.assertNotIn("0", result)
        self.assertNotIn("6", result)
        self.assertIn("1", result)

    def test_all_zero_returns_none(self):
        raw = {f"hrTimeInZone_{i}": 0.0 for i in range(1, 6)}
        self.assertIsNone(runner._build_hr_time_in_zone_json(raw))

    def test_missing_returns_none(self):
        self.assertIsNone(runner._build_hr_time_in_zone_json({}))


class TestSelectReadinessSnapshot(unittest.TestCase):
    """★ 缺陷 C：從多筆快照中選出起床時的那筆，沒有時要有明確 fallback。"""

    def test_picks_after_wakeup_reset_snapshot(self):
        snapshots = [
            {"score": 75, "inputContext": "UPDATE_REALTIME_VARIABLES"},
            {"score": 60, "inputContext": "AFTER_WAKEUP_RESET"},
            {"score": 73, "inputContext": "AFTER_POST_EXERCISE_RESET"},
        ]
        picked = runner._select_readiness_snapshot(snapshots)
        self.assertEqual(picked["score"], 60)
        self.assertEqual(picked["inputContext"], "AFTER_WAKEUP_RESET")

    def test_falls_back_to_first_when_no_wakeup_snapshot(self):
        """★ 沒有起床快照時不靜默取錯：退回 [0]，但實際 context 仍會存下。"""
        snapshots = [
            {"score": 75, "inputContext": "UPDATE_REALTIME_VARIABLES"},
            {"score": 73, "inputContext": "AFTER_POST_EXERCISE_RESET"},
        ]
        picked = runner._select_readiness_snapshot(snapshots)
        self.assertEqual(picked["score"], 75)
        self.assertEqual(picked["inputContext"], "UPDATE_REALTIME_VARIABLES")

    def test_empty_list_returns_empty_dict(self):
        self.assertEqual(runner._select_readiness_snapshot([]), {})


class TestNormalizeWellnessNewFields(unittest.TestCase):
    """★ Task 1B-9：daily_wellness 新增 14 欄，含缺陷 A／C 修正。"""

    def _raw(self, readiness=None, sleep_dto_extra=None, stats_extra=None,
              hrv_extra=None):
        sleep_dto = {
            "sleepTimeSeconds": "21540",
            "avgSleepStress": 18.0,
            "deepSleepSeconds": 3000,
            "lightSleepSeconds": 15900,
            "remSleepSeconds": 2640,
            "awakeSleepSeconds": 180,
        }
        sleep_dto.update(sleep_dto_extra or {})
        stats = {
            "averageStressLevel": 20,
            "lastSevenDaysAvgRestingHeartRate": 49.0,
            "bodyBatteryAtWakeTime": 75,
        }
        stats.update(stats_extra or {})
        hrv_summary = {
            "lastNightAvg": 49,
            "weeklyAvg": 53,
            "status": "BALANCED",
            "lastNight5MinHigh": 95,
            "baseline": {"lowUpper": 44, "balancedLow": 47, "balancedUpper": 61},
        }
        hrv_summary.update(hrv_extra or {})
        return {
            "date": "2026-08-31",
            "stats": stats,
            "sleep": {"dailySleepDTO": sleep_dto},
            "hrv": {"hrvSummary": hrv_summary},
            "training_readiness": (
                readiness if readiness is not None
                else [{"score": 60, "recoveryTime": 445, "acwrFactorPercent": 87,
                       "acuteLoad": 302, "inputContext": "AFTER_WAKEUP_RESET",
                       "timestampLocal": "2026-08-31T08:14:00"}]
            ),
        }

    def test_defect_a_stress_avg_reads_sleep_stress_not_all_day(self):
        """★ 缺陷 A：stress_avg 須來自 avgSleepStress，不可再跟
        all_day_stress_avg 寫入同一個 averageStressLevel。"""
        row = runner.normalize_wellness(self._raw())
        self.assertEqual(row["stress_avg"], 18.0)
        self.assertEqual(row["all_day_stress_avg"], 20)
        self.assertNotEqual(row["stress_avg"], row["all_day_stress_avg"])

    def test_defect_c_readiness_snapshot_metadata_stored(self):
        row = runner.normalize_wellness(self._raw())
        self.assertEqual(row["readiness_input_context"], "AFTER_WAKEUP_RESET")
        self.assertEqual(row["readiness_timestamp_local"], "2026-08-31T08:14:00")
        self.assertEqual(row["training_readiness_score"], 60)

    def test_defect_c_fallback_when_no_wakeup_snapshot(self):
        row = runner.normalize_wellness(
            self._raw(readiness=[{"score": 75, "acuteLoad": 280,
                                   "inputContext": "UPDATE_REALTIME_VARIABLES"}])
        )
        # 沒有起床快照：退回第一筆，但 context 誠實記錄為非起床時點。
        self.assertEqual(row["training_readiness_score"], 75)
        self.assertEqual(row["readiness_input_context"], "UPDATE_REALTIME_VARIABLES")

    def test_hrv_baseline_three_values(self):
        row = runner.normalize_wellness(self._raw())
        self.assertEqual(row["hrv_baseline_low_upper"], 44)
        self.assertEqual(row["hrv_baseline_balanced_low"], 47)
        self.assertEqual(row["hrv_baseline_balanced_upper"], 61)
        self.assertEqual(row["hrv_status"], "BALANCED")
        self.assertEqual(row["hrv_last_night_5min_high"], 95)

    def test_acute_load_from_readiness_payload(self):
        """零額外 API 成本：acuteLoad 已在 readiness payload 內。"""
        row = runner.normalize_wellness(self._raw())
        self.assertEqual(row["acute_load"], 302)

    def test_rhr_7day_avg_rounded_to_int(self):
        row = runner.normalize_wellness(self._raw())
        self.assertEqual(row["rhr_7day_avg_bpm"], 49)

    def test_sleep_stage_breakdown(self):
        row = runner.normalize_wellness(self._raw())
        self.assertEqual(row["sleep_deep_sec"], 3000)
        self.assertEqual(row["sleep_light_sec"], 15900)
        self.assertEqual(row["sleep_rem_sec"], 2640)
        self.assertEqual(row["sleep_awake_sec"], 180)

    def test_body_battery_at_wake(self):
        row = runner.normalize_wellness(self._raw())
        self.assertEqual(row["body_battery_at_wake"], 75)


def _gmt_ms(iso: str) -> int:
    return int(datetime.datetime.fromisoformat(iso)
               .replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


class TestNormalizeWellnessGarminCardFields(unittest.TestCase):
    """★ 2026-10-22：Garmin 網頁卡片所需欄位。key 名稱經真實帳號實測確認
    （避免測試與實作共用同一個錯誤猜測）。"""

    def _raw(self):
        return {
            "date": "2026-10-21",
            "stats": {
                "lowestRespirationValue": 7.0, "highestRespirationValue": 20.0,
                "lowestSpo2": 85, "latestSpo2": 90,
                "bodyBatteryMostRecentValue": 84, "bodyBatteryChargedValue": 61,
                "bodyBatteryDrainedValue": 12,
                "activeKilocalories": 42.0, "bmrKilocalories": 891.0,
                "moderateIntensityMinutes": 10, "vigorousIntensityMinutes": 20,
                "intensityMinutesGoal": 150,
                "floorsAscended": 1.2, "userFloorsAscendedGoal": 10,
                "dailyStepGoal": 12360,
            },
            "sleep": {"dailySleepDTO": {
                # 當地 01:37 入睡、08:05 起床（Local 毫秒以 UTC 解讀即為牆上時間）
                "sleepStartTimestampLocal": _gmt_ms("2026-10-21T01:37:00"),
                "sleepEndTimestampLocal": _gmt_ms("2026-10-21T08:05:00"),
                "averageRespirationValue": 14.0,
            }},
            "training_readiness": [{"score": 72, "level": "MODERATE",
                                    "feedbackShort": "GOOD_SLEEP_HISTORY",
                                    "inputContext": "AFTER_WAKEUP_RESET"}],
        }

    def test_readiness_level_and_feedback(self):
        row = runner.normalize_wellness(self._raw())
        self.assertEqual(row["readiness_level"], "MODERATE")
        self.assertEqual(row["readiness_feedback_short"], "GOOD_SLEEP_HISTORY")

    def test_sleep_window_is_local_wall_clock(self):
        row = runner.normalize_wellness(self._raw())
        self.assertEqual(row["sleep_start_local"], "2026-10-21T01:37:00")
        self.assertEqual(row["sleep_end_local"], "2026-10-21T08:05:00")

    def test_respiration_and_spo2(self):
        row = runner.normalize_wellness(self._raw())
        self.assertEqual(row["respiration_sleep_avg"], 14.0)
        self.assertEqual((row["respiration_low"], row["respiration_high"]), (7.0, 20.0))
        self.assertEqual((row["spo2_lowest"], row["spo2_latest"]), (85, 90))

    def test_body_battery_calories_intensity_floors_steps(self):
        row = runner.normalize_wellness(self._raw())
        self.assertEqual((row["body_battery_latest"], row["body_battery_charged"],
                          row["body_battery_drained"]), (84, 61, 12))
        self.assertEqual((row["calories_active"], row["calories_resting"]), (42, 891))
        self.assertEqual((row["intensity_minutes_moderate"], row["intensity_minutes_vigorous"],
                          row["intensity_minutes_weekly_goal"]), (10, 20, 150))
        self.assertEqual((row["floors_ascended"], row["floors_goal"]), (1.2, 10))
        self.assertEqual(row["steps_goal"], 12360)


class TestNormalizeIntraday(unittest.TestCase):
    """★ 日內序列：GMT 時間戳須換成當地時間，負值（無資料）存 null。"""

    def _raw(self):
        # 台北 UTC+8：當地 00:00 = GMT 前一天 16:00
        stress = {
            "startTimestampGMT": "2026-10-20T16:00:00.0",
            "startTimestampLocal": "2026-10-21T00:00:00.0",
            "stressValueDescriptorsDTOList": [{"index": 0, "key": "timestamp"},
                                              {"index": 1, "key": "stressLevel"}],
            "stressValuesArray": [[_gmt_ms("2026-10-20T16:03:00"), 25],
                                  [_gmt_ms("2026-10-20T16:06:00"), -1]],
            "bodyBatteryValueDescriptorsDTOList": [
                {"bodyBatteryValueDescriptorIndex": 0, "bodyBatteryValueDescriptorKey": "timestamp"},
                {"bodyBatteryValueDescriptorIndex": 1, "bodyBatteryValueDescriptorKey": "bodyBatteryStatus"},
                {"bodyBatteryValueDescriptorIndex": 2, "bodyBatteryValueDescriptorKey": "bodyBatteryLevel"},
                {"bodyBatteryValueDescriptorIndex": 3, "bodyBatteryValueDescriptorKey": "bodyBatteryVersion"}],
            "bodyBatteryValuesArray": [[_gmt_ms("2026-10-21T00:00:00"), "MEASURED", 70, 3.0]],
        }
        heart = {
            "startTimestampGMT": "2026-10-20T16:00:00.0",
            "startTimestampLocal": "2026-10-21T00:00:00.0",
            "heartRateValueDescriptors": [{"index": 0, "key": "timestamp"},
                                          {"index": 1, "key": "heartrate"}],
            "heartRateValues": [[_gmt_ms("2026-10-21T04:00:00"), 62], [_gmt_ms("2026-10-21T04:02:00"), None]],
        }
        sleep = {
            "dailySleepDTO": {"sleepStartTimestampLocal": _gmt_ms("2026-10-21T00:43:00"),
                              "sleepStartTimestampGMT": _gmt_ms("2026-10-20T16:43:00")},
            "sleepLevels": [{"startGMT": "2026-10-20T16:43:24.0", "endGMT": "2026-10-20T16:56:24.0",
                             "activityLevel": 1.0}],
        }
        return {"date": "2026-10-21", "stress_intraday": stress,
                "heart_rate_intraday": heart, "sleep": sleep}

    def _by_kind(self):
        return {r["kind"]: json.loads(r["points_json"]) for r in runner.normalize_intraday(self._raw())}

    def test_stress_minutes_are_local_and_negative_is_null(self):
        self.assertEqual(self._by_kind()["stress"], [[3, 25], [6, None]])

    def test_body_battery_reads_level_column(self):
        # GMT 00:00 = 當地 08:00 = 第 480 分鐘
        self.assertEqual(self._by_kind()["body_battery"], [[480, 70]])

    def test_heart_rate_local_minutes(self):
        self.assertEqual(self._by_kind()["heart_rate"], [[720, 62], [722, None]])

    def test_sleep_levels_local_hhmm(self):
        self.assertEqual(self._by_kind()["sleep_levels"], [["00:43", "00:56", 1]])

    def test_missing_payloads_produce_no_rows(self):
        self.assertEqual(runner.normalize_intraday({"date": "2026-10-21"}), [])


class TestSyncWritesIntradayAndSex(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(FULL_TEST_SCHEMA_SQL)
        self.today = datetime.date(2026, 10, 21)
        self.wellness = {"2026-10-21": TestNormalizeIntraday()._raw()}

    def tearDown(self):
        self.conn.close()

    def test_intraday_written_and_resync_overwrites(self):
        api = FakeApi(wellness=self.wellness)
        runner.sync(self.conn, 1, days=1, client=api, today=self.today, fetch_splits=False)
        runner.sync(self.conn, 1, days=1, client=api, today=self.today, fetch_splits=False)
        kinds = [r["kind"] for r in self.conn.execute(
            "SELECT kind FROM wellness_intraday ORDER BY kind")]
        self.assertEqual(kinds, ["body_battery", "heart_rate", "sleep_levels", "stress"])

    def test_sex_fetched_once_then_cached(self):
        api = FakeApi(sex="MALE")
        runner.sync(self.conn, 1, days=1, client=api, today=self.today, fetch_splits=False)
        runner.sync(self.conn, 1, days=1, client=api, today=self.today, fetch_splits=False)
        self.assertEqual(self.conn.execute("SELECT sex FROM athlete_profile").fetchone()[0], "MALE")
        self.assertEqual(api.sex_calls, 1)


class TestSelectPrimaryDevice(unittest.TestCase):
    """★ 結構陷阱：deviceId 為 key 的 dict，須挑 primaryTrainingDevice=True。"""

    def test_picks_primary_device_among_multiple(self):
        devices = {
            "1111111": {"primaryTrainingDevice": False, "trainingStatus": 1},
            "2222222": {"primaryTrainingDevice": True, "trainingStatus": 4},
        }
        picked = runner._select_primary_device(devices)
        self.assertEqual(picked["trainingStatus"], 4)

    def test_falls_back_to_first_when_no_primary_flag(self):
        devices = {"3453515234": {"trainingStatus": 4}}
        picked = runner._select_primary_device(devices)
        self.assertEqual(picked["trainingStatus"], 4)

    def test_empty_or_none_returns_empty_dict(self):
        self.assertEqual(runner._select_primary_device(None), {})
        self.assertEqual(runner._select_primary_device({}), {})


class TestNormalizeTrainingStatus(unittest.TestCase):
    """★ 新增：get_training_status() → training_status 表。

    fixture 結構於 2026-09-02 對真實帳號 dump 校正過：三個 deviceId-keyed
    的 map 各自埋在自己的頂層區塊下，且 `heatAltitudeAcclimation` 掛在
    `mostRecentVO2Max` 而非裝置節點下——初版 fixture 把兩者都放錯層級，
    與實作的錯誤路徑「剛好對上」，因此測試全過卻抓不到 bug。
    """

    def _raw(self):
        return {
            "mostRecentTrainingStatus": {
                "latestTrainingStatusData": {
                    "3453515234": {
                        "primaryTrainingDevice": True,
                        "trainingStatus": 4,
                        "trainingStatusFeedbackPhrase": "MAINTAINING_2",
                        "sinceDate": "2026-08-25",
                        "fitnessTrend": 2,
                        "acuteTrainingLoadDTO": {
                            "dailyTrainingLoadAcute": 302.0,
                            "dailyTrainingLoadChronic": 275.0,
                            "dailyAcuteChronicWorkloadRatio": 1.0,
                            "minTrainingLoadChronic": 220.0,
                            "maxTrainingLoadChronic": 412.5,
                            "acwrStatus": "OPTIMAL",
                        },
                    }
                }
            },
            "mostRecentVO2Max": {
                "generic": {"vo2MaxPreciseValue": 52.8, "calendarDate": "2026-08-30"},
                "heatAltitudeAcclimation": {
                    "heatAcclimationPercentage": 73,
                    "previousHeatAcclimationPercentage": 84,
                    "heatTrend": "DEACCLIMATIZING",
                    "altitudeAcclimation": 0,
                },
            },
            "mostRecentTrainingLoadBalance": {
                "metricsTrainingLoadBalanceDTOMap": {
                    "3453515234": {
                        "primaryTrainingDevice": True,
                        "monthlyLoadAerobicLow": 1117.7,
                        "monthlyLoadAerobicHigh": 0.0,
                        "monthlyLoadAnaerobic": 0.0,
                        "trainingBalanceFeedbackPhrase": "AEROBIC_HIGH_SHORTAGE",
                    }
                }
            },
        }

    def test_maps_true_acwr_and_loads(self):
        """★ 真 ACWR（急慢性負荷比）與急/慢性負荷，修缺陷 B 的關鍵欄位。"""
        row = runner.normalize_training_status("2026-08-31", self._raw())
        self.assertEqual(row["acute_load"], 302.0)
        self.assertEqual(row["chronic_load"], 275.0)
        self.assertEqual(row["acwr_ratio"], 1.0)
        self.assertEqual(row["chronic_load_optimal_min"], 220.0)
        self.assertEqual(row["chronic_load_optimal_max"], 412.5)
        self.assertEqual(row["acwr_status"], "OPTIMAL")

    def test_maps_training_status_and_vo2max(self):
        row = runner.normalize_training_status("2026-08-31", self._raw())
        self.assertEqual(row["training_status_code"], 4)
        self.assertEqual(row["training_status_phrase"], "MAINTAINING_2")
        self.assertEqual(row["status_since_date"], "2026-08-25")
        self.assertEqual(row["vo2max_precise"], 52.8)
        self.assertEqual(row["vo2max_date"], "2026-08-30")

    def test_maps_heat_acclimation_and_load_balance(self):
        row = runner.normalize_training_status("2026-08-31", self._raw())
        self.assertEqual(row["heat_acclimation_pct"], 73)
        self.assertEqual(row["heat_trend"], "DEACCLIMATIZING")
        self.assertEqual(row["monthly_load_aerobic_low"], 1117.7)
        self.assertEqual(row["monthly_load_anaerobic"], 0.0)
        self.assertEqual(row["training_balance_feedback"], "AEROBIC_HIGH_SHORTAGE")

    def test_selects_primary_device_not_first_key(self):
        """★ 結構陷阱迴歸：非 primary 的裝置排在字典前面時不可誤取。"""
        raw = self._raw()
        primary = raw["mostRecentTrainingStatus"]["latestTrainingStatusData"]["3453515234"]
        raw["mostRecentTrainingStatus"]["latestTrainingStatusData"] = {
            "9999999": {"primaryTrainingDevice": False, "trainingStatus": 1},
            "3453515234": primary,
        }
        row = runner.normalize_training_status("2026-08-31", raw)
        self.assertEqual(row["training_status_code"], 4)

    def test_wrong_nesting_level_yields_nothing(self):
        """★ 迴歸守門：把 map 放在頂層（初版實作預期的錯誤層級）應取不到值。

        這個斷言正是初版缺少的——它讓「路徑少一層」這類錯誤在測試階段
        就現形，而不是等到對真實帳號同步後才發現整欄 NULL。
        """
        raw = self._raw()
        raw["latestTrainingStatusData"] = raw["mostRecentTrainingStatus"].pop(
            "latestTrainingStatusData"
        )
        row = runner.normalize_training_status("2026-08-31", raw)
        self.assertIsNone(row["training_status_code"])
        self.assertIsNone(row["acwr_ratio"])

    def test_none_raw_returns_none(self):
        self.assertIsNone(runner.normalize_training_status("2026-08-31", None))

    def test_empty_raw_returns_none(self):
        self.assertIsNone(runner.normalize_training_status("2026-08-31", {}))


class TestNormalizePerformanceMetrics(unittest.TestCase):
    """★ 新增：五個效能指標端點 → performance_metrics 表的快照。"""

    def _raw(self, **overrides):
        raw = {
            "race_predictions": {
                "time5K": 1403, "time10K": 2994,
                "timeHalfMarathon": 6796, "timeMarathon": 11565,
            },
            # 真實結構為兩個子區塊（2026-09-02 dump 校正）：配速與心率在
            # speed_and_heart_rate，FTP 在 power。初版 fixture 誤作平坦結構。
            "lactate_threshold": {
                "speed_and_heart_rate": {"heartRate": 179.0, "speed": 0.3583},
                "power": {"functionalThresholdPower": 372.0},
            },
            "endurance_score": {
                "overallScore": 5905,
                "classification": 3,
                "contributors": [
                    {"activityTypeId": 13, "group": None, "contribution": 81.77},
                    {"activityTypeId": 14, "group": None, "contribution": 9.84},
                ],
            },
            "fitness_age": {
                "fitnessAge": 19.8, "chronologicalAge": 27,
                "achievableFitnessAge": 18.0,
            },
            "hill_score": {"overallScore": 27},
        }
        raw.update(overrides)
        return raw

    def test_maps_race_predictions(self):
        row = runner.normalize_performance_metrics("2026-09-01", self._raw())
        self.assertEqual(row["race_predict_5k_sec"], 1403)
        self.assertEqual(row["race_predict_10k_sec"], 2994)
        self.assertEqual(row["race_predict_half_sec"], 6796)
        self.assertEqual(row["race_predict_marathon_sec"], 11565)

    def test_maps_lactate_threshold_and_converts_speed_to_pace(self):
        """★ `speed` 單位是 10 公尺/秒，秒/公里 = 100 / speed。

        以 Garmin Connect 網頁為準（2026-10-22）：0.3583 → 279 秒/km（4:39/km），
        網頁同一個閾值顯示「4:39 /公里」。先前的 speed×1000（358 秒）與
        1000/speed（2791 秒）兩種猜法都錯。
        """
        row = runner.normalize_performance_metrics("2026-09-01", self._raw())
        self.assertEqual(row["lactate_threshold_hr_bpm"], 179)
        self.assertEqual(row["lactate_threshold_pace_sec_per_km"], 279)
        self.assertEqual(row["functional_threshold_power"], 372)

    def test_lactate_pace_is_in_plausible_running_range(self):
        """★ 單位迴歸守門：閾值配速須落在人類跑步的合理區間。

        能抓出方向顛倒的換算——這是初版缺少的斷言。
        """
        pace = runner.normalize_performance_metrics("2026-09-01", self._raw())[
            "lactate_threshold_pace_sec_per_km"
        ]
        self.assertGreater(pace, 150)   # 快於 2:30/km 不可能
        self.assertLess(pace, 600)      # 慢於 10:00/km 不是閾值配速

    def test_lactate_hr_falls_back_to_misspelled_key(self):
        """⚠️ Garmin 歷史拼字錯誤 hearRate（缺一個 t），見 fetch_lactate_threshold docstring。"""
        row = runner.normalize_performance_metrics(
            "2026-09-01",
            self._raw(
                lactate_threshold={
                    "speed_and_heart_rate": {"hearRate": 179.0, "speed": 0.3583}
                }
            ),
        )
        self.assertEqual(row["lactate_threshold_hr_bpm"], 179)

    def test_maps_endurance_score_and_contributors_json(self):
        row = runner.normalize_performance_metrics("2026-09-01", self._raw())
        self.assertEqual(row["endurance_score"], 5905)
        self.assertEqual(row["endurance_classification"], 3)
        contributors = json.loads(row["endurance_contributors_json"])
        self.assertEqual(
            contributors,
            [
                {"activity_type_id": 13, "group": None, "contribution": 81.77},
                {"activity_type_id": 14, "group": None, "contribution": 9.84},
            ],
        )

    def test_maps_fitness_age_and_hill_score(self):
        row = runner.normalize_performance_metrics("2026-09-01", self._raw())
        self.assertEqual(row["fitness_age"], 19.8)
        self.assertEqual(row["chronological_age"], 27)
        self.assertEqual(row["achievable_fitness_age"], 18.0)
        self.assertEqual(row["hill_score"], 27)

    def test_all_sources_missing_returns_none(self):
        self.assertIsNone(
            runner.normalize_performance_metrics(
                "2026-09-01",
                {
                    "race_predictions": None, "lactate_threshold": None,
                    "endurance_score": None, "fitness_age": None, "hill_score": None,
                },
            )
        )

    def test_no_contributors_json_when_absent(self):
        row = runner.normalize_performance_metrics(
            "2026-09-01", self._raw(endurance_score={"overallScore": 5905})
        )
        self.assertIsNone(row["endurance_contributors_json"])


class TestSyncWritesTrainingStatusAndPerformanceMetrics(unittest.TestCase):
    """★ 整合測試：sync() 須把兩張新表也寫進資料庫。"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(FULL_TEST_SCHEMA_SQL)
        self.today = datetime.date(2026, 8, 16)

    def tearDown(self):
        self.conn.close()

    def _training_status_raw(self):
        """真實結構：deviceId-keyed 的 map 埋在 `mostRecentTrainingStatus` 下。"""
        return {
            "mostRecentTrainingStatus": {
                "latestTrainingStatusData": {
                    "111": {
                        "primaryTrainingDevice": True,
                        "trainingStatus": 4,
                        "trainingStatusFeedbackPhrase": "MAINTAINING_2",
                        "acuteTrainingLoadDTO": {
                            "dailyTrainingLoadAcute": 302.0,
                            "dailyTrainingLoadChronic": 275.0,
                            "dailyAcuteChronicWorkloadRatio": 1.0,
                        },
                    }
                }
            }
        }

    def test_training_status_written_from_daily_wellness_raw(self):
        wellness = {
            "2026-08-16": {
                "date": "2026-08-16",
                "stats": None, "sleep": None, "hrv": None, "training_readiness": [],
                "training_status": self._training_status_raw(),
            }
        }
        result = runner.sync(
            self.conn, 1, days=1, client=FakeApi(wellness=wellness), today=self.today
        )
        self.assertEqual(result["training_status_days_synced"], 1)
        row = self.conn.execute(
            "SELECT acwr_ratio, training_status_phrase FROM training_status"
        ).fetchone()
        self.assertEqual(row["acwr_ratio"], 1.0)
        self.assertEqual(row["training_status_phrase"], "MAINTAINING_2")

    def test_training_status_resync_does_not_duplicate(self):
        wellness = {
            "2026-08-16": {
                "date": "2026-08-16",
                "stats": None, "sleep": None, "hrv": None, "training_readiness": [],
                "training_status": self._training_status_raw(),
            }
        }
        api = FakeApi(wellness=wellness)
        runner.sync(self.conn, 1, days=1, client=api, today=self.today)
        runner.sync(self.conn, 1, days=1, client=api, today=self.today)
        count = self.conn.execute("SELECT COUNT(*) FROM training_status").fetchone()[0]
        self.assertEqual(count, 1)

    def test_performance_metrics_written_once_per_sync(self):
        performance = {
            "race_predictions": {"time5K": 1403},
            "lactate_threshold": {"speed_and_heart_rate": {"heartRate": 179.0}},
        }
        result = runner.sync(
            self.conn, 1, days=1,
            client=FakeApi(performance=performance), today=self.today,
        )
        self.assertEqual(result["performance_metrics_synced"], 1)
        row = self.conn.execute(
            "SELECT race_predict_5k_sec, lactate_threshold_hr_bpm FROM performance_metrics"
        ).fetchone()
        self.assertEqual(row["race_predict_5k_sec"], 1403)
        self.assertEqual(row["lactate_threshold_hr_bpm"], 179)

    def test_performance_metrics_resync_does_not_duplicate(self):
        performance = {"race_predictions": {"time5K": 1403}}
        api = FakeApi(performance=performance)
        runner.sync(self.conn, 1, days=1, client=api, today=self.today)
        runner.sync(self.conn, 1, days=1, client=api, today=self.today)
        count = self.conn.execute("SELECT COUNT(*) FROM performance_metrics").fetchone()[0]
        self.assertEqual(count, 1)

    def test_no_performance_metrics_row_when_all_endpoints_empty(self):
        result = runner.sync(self.conn, 1, days=1, client=FakeApi(), today=self.today)
        self.assertEqual(result["performance_metrics_synced"], 0)
        count = self.conn.execute("SELECT COUNT(*) FROM performance_metrics").fetchone()[0]
        self.assertEqual(count, 0)

    def test_no_training_status_row_when_endpoint_empty(self):
        result = runner.sync(self.conn, 1, days=1, client=FakeApi(), today=self.today)
        self.assertEqual(result["training_status_days_synced"], 0)
        count = self.conn.execute("SELECT COUNT(*) FROM training_status").fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
