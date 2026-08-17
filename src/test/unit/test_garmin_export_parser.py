"""Unit tests for garmin_export_parser.

Uses synthetic fixture data only — never real personal exports — so these tests
are safe to commit and run for any contributor, per the project's "no personal
data in the repo" rule.
"""

import tempfile
import unittest
from pathlib import Path

from src.main.python.services import garmin_export_parser as parser
from src.test.unit.fixtures import FAKE_USER_ID, build_fixture_export


class TestDiscovery(unittest.TestCase):
    def test_discover_export_roots_finds_di_connect_under_random_uuid(self):
        with tempfile.TemporaryDirectory() as tmp:
            di_connect = build_fixture_export(Path(tmp))
            roots = parser.discover_export_roots(Path(tmp))
            self.assertEqual(roots, [di_connect])

    def test_discover_user_profile_id_from_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            di_connect = build_fixture_export(Path(tmp))
            self.assertEqual(parser.discover_user_profile_id(di_connect), FAKE_USER_ID)

    def test_discover_user_profile_id_falls_back_to_wellness_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            di_connect = build_fixture_export(Path(tmp))
            # Remove the fitness folder so discovery must use the wellness filename fallback.
            fitness_dir = di_connect / "DI-Connect-Fitness"
            for f in fitness_dir.glob("*"):
                f.unlink()
            self.assertEqual(parser.discover_user_profile_id(di_connect), FAKE_USER_ID)


class TestParseActivities(unittest.TestCase):
    def test_unit_conversions_are_correct(self):
        with tempfile.TemporaryDirectory() as tmp:
            di_connect = build_fixture_export(Path(tmp))
            rows = parser.parse_activities(di_connect)
            self.assertEqual(len(rows), 1)
            row = rows[0]

            self.assertAlmostEqual(row["distance_km"], 5.0)
            self.assertEqual(row["duration_sec"], 1500)
            self.assertEqual(row["moving_time_sec"], 1490)
            self.assertEqual(row["avg_pace_sec_per_km"], 300)  # 1500s / 5km
            self.assertEqual(row["best_pace_sec_per_km"], 250)  # 4 m/s -> 250 s/km
            self.assertAlmostEqual(row["elevation_gain_m"], 5.0)
            self.assertEqual(row["calories"], 100)  # 418.4 kJ / 4.184
            self.assertEqual(row["avg_cadence_spm"], 172.0)
            self.assertEqual(row["activity_type"], "running")
            self.assertEqual(row["has_wellness_data"], 1)

    def test_started_at_prefers_local_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            di_connect = build_fixture_export(Path(tmp))
            row = parser.parse_activities(di_connect)[0]
            self.assertEqual(row["started_at_local_epoch_ms"], 1700028800000)


class TestDailyWellness(unittest.TestCase):
    def test_health_status_maps_metric_types_to_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            di_connect = build_fixture_export(Path(tmp))
            by_date = parser.parse_health_status(di_connect)
            row = by_date["2026-01-05"]
            self.assertEqual(row["hrv_ms"], 45.0)
            self.assertEqual(row["resting_hr_bpm"], 55.0)
            self.assertEqual(row["spo2_pct"], 97.0)
            self.assertEqual(row["respiration_rate"], 14.5)
            self.assertNotIn("skin_temp_c", row)  # no "value" key present -> skipped, not crashed

    def test_sleep_duration_sums_stages_and_reads_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            di_connect = build_fixture_export(Path(tmp))
            by_date = parser.parse_sleep(di_connect)
            row = by_date["2026-01-05"]
            self.assertEqual(row["sleep_duration_sec"], 5400 + 12600 + 3600)
            self.assertEqual(row["sleep_score"], 80)
            self.assertEqual(row["sleep_quality"], "GOOD")
            self.assertEqual(row["stress_avg"], 20)

    def test_training_readiness_converts_recovery_minutes_to_hours(self):
        with tempfile.TemporaryDirectory() as tmp:
            di_connect = build_fixture_export(Path(tmp))
            by_date = parser.parse_training_readiness(di_connect)
            row = by_date["2026-01-05"]
            self.assertEqual(row["training_readiness_score"], 70)
            self.assertEqual(row["hrv_weekly_avg_ms"], 44.0)
            self.assertEqual(row["recovery_time_hours"], 10.0)
            self.assertEqual(row["acwr"], 85)

    def test_daily_summary_parses_steps_rhr_and_all_day_stress(self):
        with tempfile.TemporaryDirectory() as tmp:
            di_connect = build_fixture_export(Path(tmp))
            by_date = parser.parse_daily_summary(di_connect)
            row = by_date["2026-01-06"]
            self.assertEqual(row["steps"], 6000)
            self.assertEqual(row["all_day_rhr_bpm"], 58)
            self.assertEqual(row["all_day_stress_avg"], 25)

    def test_merge_daily_wellness_combines_all_three_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            di_connect = build_fixture_export(Path(tmp))
            rows = parser.merge_daily_wellness(di_connect)
            self.assertEqual(len(rows), 2)  # 2026-01-05（三種來源）+ 2026-01-06（僅 UDSFile）
            row = rows[0]
            self.assertEqual(row["date"], "2026-01-05")
            # fields from all three source files should be present on one merged row
            self.assertIn("hrv_ms", row)

    def test_merge_prefers_health_status_rhr_over_uds_fallback(self):
        """2026-01-05 兩邊都有 RHR：healthStatusData=55.0 應勝出，不被 UDSFile 的 999 蓋掉。"""
        with tempfile.TemporaryDirectory() as tmp:
            di_connect = build_fixture_export(Path(tmp))
            rows = {r["date"]: r for r in parser.merge_daily_wellness(di_connect)}
            self.assertEqual(rows["2026-01-05"]["resting_hr_bpm"], 55.0)
            self.assertEqual(rows["2026-01-05"]["steps"], 8000)
            self.assertEqual(rows["2026-01-05"]["all_day_stress_avg"], 30)

    def test_merge_falls_back_to_uds_rhr_when_health_status_missing(self):
        """2026-01-06 沒有 healthStatusData：resting_hr_bpm 應退回 UDSFile 的 58。"""
        with tempfile.TemporaryDirectory() as tmp:
            di_connect = build_fixture_export(Path(tmp))
            rows = {r["date"]: r for r in parser.merge_daily_wellness(di_connect)}
            self.assertEqual(rows["2026-01-06"]["resting_hr_bpm"], 58)
            self.assertEqual(rows["2026-01-06"]["steps"], 6000)


class TestMetricCoverage(unittest.TestCase):
    def test_coverage_spans_are_derived_per_metric(self):
        with tempfile.TemporaryDirectory() as tmp:
            di_connect = build_fixture_export(Path(tmp))
            activities = parser.parse_activities(di_connect)
            wellness = parser.merge_daily_wellness(di_connect)
            coverage = parser.compute_metric_coverage(activities, wellness)

            by_metric = {c["metric_name"]: c for c in coverage}
            self.assertIn("activities", by_metric)
            self.assertIn("hrv_ms", by_metric)
            self.assertEqual(by_metric["hrv_ms"]["earliest_date"], "2026-01-05")
            self.assertEqual(by_metric["hrv_ms"]["latest_date"], "2026-01-05")


if __name__ == "__main__":
    unittest.main()
