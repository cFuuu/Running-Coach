"""garmin_sync_runner 的單元測試——合成假資料，不發網路請求、不含真實憑證。

重點：
1. **單位換算**：API 用公尺／秒／大卡，與匯出包的公分／毫秒／千焦耳不同。
   誤用匯出包係數會產生 100～1000 倍錯誤，故特別加迴歸測試守住。
2. **冪等**：重複同步同一區間不得產生重複列。
3. **workout_type 不被覆蓋**：分類結果（尤其人工標註）須保留。
"""

from __future__ import annotations

import datetime
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


class FakeApi:
    """假的 API client——回傳固定資料，不發網路請求。"""

    def __init__(self, activities=None, wellness=None):
        self._activities = activities if activities is not None else [REAL_ACTIVITY]
        self._wellness = wellness or {}
        self.activity_calls = 0

    def fetch_activities(self, start_date, end_date):
        self.activity_calls += 1
        return list(self._activities)

    def fetch_daily_wellness_raw(self, cdate):
        return self._wellness.get(cdate.isoformat(), {"date": cdate.isoformat()})


class TestSyncWritesAndIsIdempotent(unittest.TestCase):
    """同步寫入資料庫並保證冪等。"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE athlete_profile (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
            CREATE TABLE activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                athlete_id INTEGER NOT NULL, external_id TEXT,
                activity_type TEXT NOT NULL, title TEXT, started_at TEXT NOT NULL,
                distance_km REAL, duration_sec INTEGER, moving_time_sec INTEGER,
                avg_hr_bpm INTEGER, max_hr_bpm INTEGER, aerobic_te REAL,
                avg_cadence_spm INTEGER, max_cadence_spm INTEGER,
                avg_pace_sec_per_km INTEGER, best_pace_sec_per_km INTEGER,
                elevation_gain_m REAL, elevation_loss_m REAL, calories INTEGER,
                reps INTEGER, sets INTEGER, source TEXT NOT NULL, source_version TEXT,
                fetched_at TEXT NOT NULL, has_wellness_data INTEGER NOT NULL DEFAULT 0,
                raw_data_json TEXT, workout_type TEXT, workout_type_source TEXT,
                UNIQUE (athlete_id, started_at, activity_type, distance_km)
            );
            CREATE TABLE daily_wellness (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                athlete_id INTEGER NOT NULL, date TEXT NOT NULL,
                resting_hr_bpm INTEGER, hrv_ms REAL, hrv_weekly_avg_ms REAL,
                spo2_pct REAL, skin_temp_c REAL, respiration_rate REAL,
                sleep_duration_sec INTEGER, sleep_quality TEXT, sleep_score INTEGER,
                stress_avg INTEGER, body_battery_max INTEGER, body_battery_min INTEGER,
                steps INTEGER, training_readiness_score INTEGER,
                recovery_time_hours REAL, acwr REAL, source TEXT NOT NULL,
                source_version TEXT, fetched_at TEXT NOT NULL, all_day_stress_avg INTEGER,
                UNIQUE (athlete_id, date, source)
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
        )
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

    成本結構：活動只打 1 次（吃日期區間），wellness 逐日打 4 個端點
    （stats／sleep／hrv／training_readiness），每次間隔 1 秒節流。
    """

    def test_request_count_is_one_plus_four_per_day(self):
        est = runner.estimate_sync_cost(7)
        self.assertEqual(est["request_count"], 1 + 7 * 4)

    def test_estimated_seconds_follows_throttle_interval(self):
        est = runner.estimate_sync_cost(30)
        expected_requests = 1 + 30 * 4
        self.assertEqual(est["request_count"], expected_requests)
        self.assertAlmostEqual(
            est["estimated_seconds"],
            expected_requests * runner.REQUEST_INTERVAL_SEC,
            places=3,
        )

    def test_ninety_days_stays_within_reasonable_bound(self):
        """90 天上限的實際成本：361 次請求、約 6 分鐘。"""
        est = runner.estimate_sync_cost(runner.MAX_AUTO_SYNC_DAYS)
        self.assertEqual(est["request_count"], 361)
        self.assertLess(est["estimated_seconds"], 7 * 60)

    def test_human_readable_text_included(self):
        est = runner.estimate_sync_cost(7)
        self.assertIn("29", est["summary"])


class TestSyncReportsPlanAndCost(unittest.TestCase):
    """sync() 須回報實際採用的天數與預估成本，供 CLI／MCP 告知使用者。"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE athlete_profile (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
            CREATE TABLE activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                athlete_id INTEGER NOT NULL, external_id TEXT,
                activity_type TEXT NOT NULL, title TEXT, started_at TEXT NOT NULL,
                distance_km REAL, duration_sec INTEGER, moving_time_sec INTEGER,
                avg_hr_bpm INTEGER, max_hr_bpm INTEGER, aerobic_te REAL,
                avg_cadence_spm INTEGER, max_cadence_spm INTEGER,
                avg_pace_sec_per_km INTEGER, best_pace_sec_per_km INTEGER,
                elevation_gain_m REAL, elevation_loss_m REAL, calories INTEGER,
                reps INTEGER, sets INTEGER, source TEXT NOT NULL, source_version TEXT,
                fetched_at TEXT NOT NULL, has_wellness_data INTEGER NOT NULL DEFAULT 0,
                raw_data_json TEXT, workout_type TEXT, workout_type_source TEXT,
                UNIQUE (athlete_id, started_at, activity_type, distance_km)
            );
            CREATE TABLE daily_wellness (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                athlete_id INTEGER NOT NULL, date TEXT NOT NULL,
                resting_hr_bpm INTEGER, hrv_ms REAL, hrv_weekly_avg_ms REAL,
                spo2_pct REAL, skin_temp_c REAL, respiration_rate REAL,
                sleep_duration_sec INTEGER, sleep_quality TEXT, sleep_score INTEGER,
                stress_avg INTEGER, body_battery_max INTEGER, body_battery_min INTEGER,
                steps INTEGER, training_readiness_score INTEGER,
                recovery_time_hours REAL, acwr REAL, source TEXT NOT NULL,
                source_version TEXT, fetched_at TEXT NOT NULL, all_day_stress_avg INTEGER,
                UNIQUE (athlete_id, date, source)
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
        )
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
        self.assertEqual(result["estimate"]["request_count"], 1 + 2 * 4)

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


if __name__ == "__main__":
    unittest.main()
