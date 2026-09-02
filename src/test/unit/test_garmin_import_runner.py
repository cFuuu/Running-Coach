"""Unit tests for garmin_import_runner — DB write logic, multi-athlete identity
resolution, and idempotent re-import. Uses an in-memory SQLite DB and the same
synthetic fixtures as test_garmin_export_parser.py (never real personal data).
"""

import json
import tempfile
import unittest
from pathlib import Path

from src.main.python.models.db import get_connection
from src.main.python.services import garmin_import_runner as runner
from src.test.unit.fixtures import build_fixture_export


class TestResolveAthleteId(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_same_external_ref_reuses_athlete(self):
        first = runner.resolve_athlete_id(self.conn, "garmin_export", "111", "Alice")
        second = runner.resolve_athlete_id(self.conn, "garmin_export", "111", "Alice")
        self.assertEqual(first, second)
        count = self.conn.execute("SELECT COUNT(*) FROM athlete_profile").fetchone()[0]
        self.assertEqual(count, 1)

    def test_different_external_ref_creates_new_athlete(self):
        alice = runner.resolve_athlete_id(self.conn, "garmin_export", "111", "Alice")
        bob = runner.resolve_athlete_id(self.conn, "garmin_export", "222", "Bob")
        self.assertNotEqual(alice, bob)
        count = self.conn.execute("SELECT COUNT(*) FROM athlete_profile").fetchone()[0]
        self.assertEqual(count, 2)


class TestImportExport(unittest.TestCase):
    def test_import_writes_expected_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            di_connect = build_fixture_export(Path(tmp))
            conn = get_connection(":memory:")
            summary = runner.import_export(conn, di_connect, athlete_name="Test Athlete")

            self.assertEqual(summary["activities"], 1)
            # 2026-01-05（三種來源都有）+ 2026-01-06（僅 UDSFile 的每日摘要）
            self.assertEqual(summary["daily_wellness"], 2)
            self.assertGreater(summary["metric_coverage"], 0)

            activity = conn.execute("SELECT * FROM activities").fetchone()
            self.assertEqual(activity["distance_km"], 5.0)
            self.assertEqual(activity["source"], "garmin_export")

            wellness = conn.execute(
                "SELECT * FROM daily_wellness WHERE date = '2026-01-05'"
            ).fetchone()
            self.assertEqual(wellness["hrv_ms"], 45.0)
            self.assertEqual(wellness["training_readiness_score"], 70)
            self.assertEqual(wellness["steps"], 8000)
            self.assertEqual(wellness["all_day_stress_avg"], 30)

            fallback = conn.execute(
                "SELECT * FROM daily_wellness WHERE date = '2026-01-06'"
            ).fetchone()
            self.assertEqual(fallback["resting_hr_bpm"], 58)  # 退回 UDSFile 的值
            conn.close()

    def test_reimport_is_idempotent(self):
        """Running the same import twice must not create duplicate rows or duplicate athletes."""
        with tempfile.TemporaryDirectory() as tmp:
            di_connect = build_fixture_export(Path(tmp))
            conn = get_connection(":memory:")
            runner.import_export(conn, di_connect, athlete_name="Test Athlete")
            runner.import_export(conn, di_connect, athlete_name="Test Athlete")

            athlete_count = conn.execute("SELECT COUNT(*) FROM athlete_profile").fetchone()[0]
            activity_count = conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
            wellness_count = conn.execute("SELECT COUNT(*) FROM daily_wellness").fetchone()[0]

            self.assertEqual(athlete_count, 1)
            self.assertEqual(activity_count, 1)
            self.assertEqual(wellness_count, 2)
            conn.close()

    def test_two_different_athletes_stay_isolated(self):
        """Multi-user requirement: two different Garmin accounts must not collide."""
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            di_connect_a = build_fixture_export(Path(tmp_a), user_profile_id="11111")
            di_connect_b = build_fixture_export(Path(tmp_b), user_profile_id="22222")
            conn = get_connection(":memory:")

            summary_a = runner.import_export(conn, di_connect_a, athlete_name="Athlete A")
            summary_b = runner.import_export(conn, di_connect_b, athlete_name="Athlete B")

            self.assertNotEqual(summary_a["athlete_id"], summary_b["athlete_id"])
            athlete_count = conn.execute("SELECT COUNT(*) FROM athlete_profile").fetchone()[0]
            self.assertEqual(athlete_count, 2)
            conn.close()


class TestBackfillActivityMetrics(unittest.TestCase):
    """★ Task 1B-9 回填：從既有 raw_data_json 補上新建模的指標欄位。

    重點是**分來源換算**——匯出包與 API 的單位慣例只在兩處不同，
    誤用會產生「有值但語意錯誤」的欄位，比缺值更危險。
    """

    #: 匯出包來源的 raw payload（單位：公寸/秒、毫秒）
    EXPORT_RAW = {
        "avgGradeAdjustedSpeed": 0.2655,
        "avgStrideLength": 95.06,
        "avgGroundContactTime": 277.8,
        "avgVerticalOscillation": 8.54,
        "avgVerticalRatio": 8.99,
        "avgPower": 278.0,
        "maxPower": 320.0,
        "normPower": 279.0,
        "minTemperature": 30.0,
        "maxTemperature": 31.0,
        "anaerobicTrainingEffect": 0.5,
        "avgDoubleCadence": 168.0,
        "maxDoubleCadence": 180.0,
        "trainingEffectLabel": "TEMPO",
        "activityTrainingLoad": 78.75,
        "hrTimeInZone_1": 203994.0,
        "hrTimeInZone_2": 2387465.0,
        "hrTimeInZone_3": 0.0,
    }

    #: API 來源的 raw payload（單位：公尺/秒、秒）
    API_RAW = {
        "avgGradeAdjustedSpeed": 2.671,
        "avgStrideLength": 95.8,
        "hrTimeInZone_1": 203.994,
        "hrTimeInZone_2": 5197.439,
    }

    def _seed(self, conn, raw, source_version, started_at="2026-08-11T07:00:00"):
        conn.execute(
            """
            INSERT INTO athlete_profile (id, name, updated_at)
            VALUES (1, 'T', '2026-01-01T00:00:00')
            ON CONFLICT (id) DO NOTHING
            """
        )
        conn.execute(
            """
            INSERT INTO activities
                (athlete_id, activity_type, started_at, distance_km, duration_sec,
                 source, source_version, fetched_at, has_wellness_data, raw_data_json)
            VALUES (1, 'running', ?, 7.3, 2746, 'garmin_export', ?, '2026-01-01T00:00:00', 0, ?)
            """,
            (started_at, source_version, json.dumps(raw)),
        )
        conn.commit()

    def test_export_grade_adjusted_speed_is_decimetres_per_sec(self):
        """★ 匯出包的 GAP 需 ×10 才是公尺/秒。

        實測：0.2655 ×10 → 2.655 m/s → 377 秒/km（6:17），與該場實際
        配速 6:16 相符；若當成公尺/秒會得到 3766 秒/km（62:46）。
        """
        conn = get_connection(":memory:")
        try:
            self._seed(conn, self.EXPORT_RAW, None)
            runner._backfill_activity_metrics(conn, 1)
            pace = conn.execute(
                "SELECT avg_grade_adj_pace_sec_per_km FROM activities"
            ).fetchone()[0]
            self.assertEqual(pace, 377)
        finally:
            conn.close()

    def test_api_grade_adjusted_speed_is_metres_per_sec(self):
        """API 來源不套 ×10——同一個欄位、不同來源、不同單位。"""
        conn = get_connection(":memory:")
        try:
            self._seed(conn, self.API_RAW, "api_sync")
            runner._backfill_activity_metrics(conn, 1)
            pace = conn.execute(
                "SELECT avg_grade_adj_pace_sec_per_km FROM activities"
            ).fetchone()[0]
            self.assertEqual(pace, 374)
        finally:
            conn.close()

    def test_grade_adjusted_pace_is_in_plausible_running_range(self):
        """★ 單位迴歸守門：兩來源算出的配速都須落在人類跑步的合理區間。"""
        for raw, version in ((self.EXPORT_RAW, None), (self.API_RAW, "api_sync")):
            conn = get_connection(":memory:")
            try:
                self._seed(conn, raw, version)
                runner._backfill_activity_metrics(conn, 1)
                pace = conn.execute(
                    "SELECT avg_grade_adj_pace_sec_per_km FROM activities"
                ).fetchone()[0]
                self.assertGreater(pace, 150, f"{version} 來源算出的配速過快")
                self.assertLess(pace, 900, f"{version} 來源算出的配速過慢")
            finally:
                conn.close()

    def test_export_hr_zones_are_milliseconds(self):
        """匯出包的 hrTimeInZone 是毫秒，須 ÷1000。"""
        zones = runner._build_backfill_hr_zones(self.EXPORT_RAW, is_api=False)
        self.assertEqual(json.loads(zones), {"1": 204, "2": 2387, "3": 0})

    def test_api_hr_zones_are_already_seconds(self):
        """★ API 的同名欄位卻是秒——2026-09-02 實測，勿套匯出包係數。"""
        zones = runner._build_backfill_hr_zones(self.API_RAW, is_api=True)
        self.assertEqual(json.loads(zones), {"1": 204, "2": 5197})

    def test_hr_zones_all_zero_returns_none(self):
        """全 0 不存 JSON——避免下游誤讀為「量過且確實是 0」。"""
        raw = {f"hrTimeInZone_{i}": 0.0 for i in range(1, 6)}
        self.assertIsNone(runner._build_backfill_hr_zones(raw, is_api=False))

    def test_same_unit_fields_copied_verbatim(self):
        """步幅/跑姿/功率/溫度兩來源單位相同，直接取值不換算。"""
        conn = get_connection(":memory:")
        try:
            self._seed(conn, self.EXPORT_RAW, None)
            runner._backfill_activity_metrics(conn, 1)
            row = conn.execute(
                """SELECT avg_stride_length_cm, avg_ground_contact_time_ms,
                          avg_power_watts, min_temperature_c, anaerobic_te,
                          avg_cadence_spm, training_effect_label
                   FROM activities"""
            ).fetchone()
            self.assertAlmostEqual(row[0], 95.06)
            self.assertAlmostEqual(row[1], 277.8)
            self.assertAlmostEqual(row[2], 278.0)
            self.assertAlmostEqual(row[3], 30.0)
            self.assertAlmostEqual(row[4], 0.5)
            self.assertAlmostEqual(row[5], 168.0)
            self.assertEqual(row[6], "TEMPO")
        finally:
            conn.close()

    def test_does_not_overwrite_existing_values(self):
        """★ 只填 NULL——已同步寫入或人工修正過的值不可被回填覆蓋。"""
        conn = get_connection(":memory:")
        try:
            self._seed(conn, self.EXPORT_RAW, None)
            conn.execute("UPDATE activities SET avg_power_watts = 999.0")
            conn.commit()
            runner._backfill_activity_metrics(conn, 1)
            row = conn.execute(
                "SELECT avg_power_watts, avg_stride_length_cm FROM activities"
            ).fetchone()
            self.assertEqual(row[0], 999.0)          # 既有值保留
            self.assertAlmostEqual(row[1], 95.06)    # NULL 欄位仍被補上
        finally:
            conn.close()

    def test_repeatable(self):
        """可重複執行：第二次不應再改動任何列。"""
        conn = get_connection(":memory:")
        try:
            self._seed(conn, self.EXPORT_RAW, None)
            runner._backfill_activity_metrics(conn, 1)
            second = runner._backfill_activity_metrics(conn, 1)
            self.assertEqual(second["activities_touched"], 0)
        finally:
            conn.close()

    def test_dry_run_writes_nothing(self):
        conn = get_connection(":memory:")
        try:
            self._seed(conn, self.EXPORT_RAW, None)
            runner._backfill_activity_metrics(conn, 1, dry_run=True)
            pace = conn.execute(
                "SELECT avg_grade_adj_pace_sec_per_km FROM activities"
            ).fetchone()[0]
            self.assertIsNone(pace)
        finally:
            conn.close()

    def test_malformed_raw_json_is_skipped(self):
        """壞掉的 raw_data_json 不應中斷整批回填。"""
        conn = get_connection(":memory:")
        try:
            self._seed(conn, self.EXPORT_RAW, None)
            conn.execute(
                """INSERT INTO activities
                   (athlete_id, activity_type, started_at, distance_km, duration_sec,
                    source, fetched_at, has_wellness_data, raw_data_json)
                   VALUES (1, 'running', '2026-08-12T07:00:00', 5.0, 1800,
                           'garmin_export', '2026-01-01T00:00:00', 0, 'not json')"""
            )
            conn.commit()
            summary = runner._backfill_activity_metrics(conn, 1)
            self.assertEqual(summary["activities_touched"], 1)
        finally:
            conn.close()


class TestRefreshActivityMetricCoverage(unittest.TestCase):
    """★ Task 1B-9：回填後由資料庫反推各指標的可用區間。"""

    def _seed_two_activities(self, conn):
        conn.execute(
            """INSERT INTO athlete_profile (id, name, updated_at)
               VALUES (1, 'T', '2026-01-01T00:00:00')"""
        )
        # 早的那場只有步幅，晚的那場步幅與功率都有——兩指標深度不同。
        conn.execute(
            """INSERT INTO activities
               (athlete_id, activity_type, started_at, distance_km, duration_sec,
                source, fetched_at, has_wellness_data, avg_stride_length_cm)
               VALUES (1, 'running', '2022-01-05T07:00:00', 10.0, 3600,
                       'garmin_export', '2026-01-01T00:00:00', 0, 90.0)"""
        )
        conn.execute(
            """INSERT INTO activities
               (athlete_id, activity_type, started_at, distance_km, duration_sec,
                source, fetched_at, has_wellness_data, avg_stride_length_cm, avg_power_watts)
               VALUES (1, 'running', '2026-08-30T07:00:00', 10.0, 3600,
                       'garmin_export', '2026-01-01T00:00:00', 0, 95.0, 280.0)"""
        )
        conn.commit()

    def test_records_per_metric_history_depth(self):
        """★ 各指標深度不同是真實事實，不可假設一致。"""
        conn = get_connection(":memory:")
        try:
            self._seed_two_activities(conn)
            runner.refresh_activity_metric_coverage(conn, 1)
            rows = dict(
                conn.execute(
                    """SELECT metric_name, earliest_date FROM metric_coverage
                       WHERE metric_name IN ('avg_stride_length_cm', 'avg_power_watts')"""
                ).fetchall()
            )
            self.assertEqual(rows["avg_stride_length_cm"], "2022-01-05")
            self.assertEqual(rows["avg_power_watts"], "2026-08-30")
        finally:
            conn.close()

    def test_absent_metric_gets_no_row(self):
        """完全沒資料的指標不建 coverage 列——空區間比沒有更誤導。"""
        conn = get_connection(":memory:")
        try:
            self._seed_two_activities(conn)
            runner.refresh_activity_metric_coverage(conn, 1)
            row = conn.execute(
                "SELECT 1 FROM metric_coverage WHERE metric_name = 'hr_time_in_zone_json'"
            ).fetchone()
            self.assertIsNone(row)
        finally:
            conn.close()

    def test_updates_overall_activities_range(self):
        conn = get_connection(":memory:")
        try:
            self._seed_two_activities(conn)
            runner.refresh_activity_metric_coverage(conn, 1)
            row = conn.execute(
                "SELECT earliest_date, latest_date FROM metric_coverage WHERE metric_name = 'activities'"
            ).fetchone()
            self.assertEqual(row[0], "2022-01-05")
            self.assertEqual(row[1], "2026-08-30")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
