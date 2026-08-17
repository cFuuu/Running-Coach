"""Unit tests for garmin_import_runner — DB write logic, multi-athlete identity
resolution, and idempotent re-import. Uses an in-memory SQLite DB and the same
synthetic fixtures as test_garmin_export_parser.py (never real personal data).
"""

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


if __name__ == "__main__":
    unittest.main()
