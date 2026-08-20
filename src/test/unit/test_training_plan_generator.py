"""training_plan_generator 的單元測試——全部使用合成假資料建立臨時 SQLite，不含真實個人資料。

含真實資料庫互動的整合測試（非僅純函式層），驗證「查活動→算 VDOT→產生課表→
寫入 training_plan」全流程真正串起來。
"""

import datetime
import unittest

from src.main.python.models.db import get_connection
from src.main.python.services.training_plan_generator import (
    generate_and_save_plan,
    get_vdot_candidate_activities,
)
from src.main.python.services.training_plan_store import get_active_schedule

REFERENCE_DATE = datetime.date(2026, 8, 20)


def _build_athlete(conn, max_hr_bpm=None, max_hr_source=None) -> int:
    cursor = conn.execute(
        """
        INSERT INTO athlete_profile (name, max_hr_bpm, max_hr_source, updated_at)
        VALUES ('測試學員', ?, ?, '2026-01-01T00:00:00')
        """,
        (max_hr_bpm, max_hr_source),
    )
    return cursor.lastrowid


def _insert_activity(
    conn,
    athlete_id: int,
    date: datetime.date,
    distance_km: float,
    avg_pace_sec_per_km: int | None = None,
    avg_hr_bpm: float | None = None,
    workout_type: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO activities
            (athlete_id, activity_type, started_at, distance_km, duration_sec,
             avg_hr_bpm, avg_pace_sec_per_km, workout_type, source, fetched_at)
        VALUES (?, 'running', ?, ?, 1800, ?, ?, ?, 'fit_manual', ?)
        """,
        (
            athlete_id,
            f"{date.isoformat()}T07:00:00",
            distance_km,
            avg_hr_bpm,
            avg_pace_sec_per_km,
            workout_type,
            f"{date.isoformat()}T07:00:00",
        ),
    )


class TestGetVdotCandidateActivities(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)

    def test_10k_distance_is_recognized(self):
        _insert_activity(self.conn, self.athlete_id, REFERENCE_DATE, distance_km=10.05, avg_pace_sec_per_km=300, avg_hr_bpm=175)
        self.conn.commit()

        candidates = get_vdot_candidate_activities(self.conn, self.athlete_id)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["distance_category"], "10k")

    def test_race_workout_type_marks_max_effort(self):
        _insert_activity(self.conn, self.athlete_id, REFERENCE_DATE, distance_km=21.1, workout_type="race")
        self.conn.commit()

        candidates = get_vdot_candidate_activities(self.conn, self.athlete_id)
        self.assertTrue(candidates[0]["is_max_effort"])
        self.assertEqual(candidates[0]["distance_category"], "half_marathon")

    def test_non_race_workout_type_is_not_max_effort(self):
        _insert_activity(self.conn, self.athlete_id, REFERENCE_DATE, distance_km=10.0, workout_type="tempo")
        self.conn.commit()

        candidates = get_vdot_candidate_activities(self.conn, self.athlete_id)
        self.assertFalse(candidates[0]["is_max_effort"])

    def test_distance_not_matching_any_standard_is_excluded(self):
        _insert_activity(self.conn, self.athlete_id, REFERENCE_DATE, distance_km=7.3)
        self.conn.commit()

        candidates = get_vdot_candidate_activities(self.conn, self.athlete_id)
        self.assertEqual(candidates, [])

    def test_null_distance_is_excluded(self):
        self.conn.execute(
            """
            INSERT INTO activities
                (athlete_id, activity_type, started_at, duration_sec, source, fetched_at)
            VALUES (?, 'strength_training', ?, 3600, 'fit_manual', ?)
            """,
            (self.athlete_id, f"{REFERENCE_DATE.isoformat()}T07:00:00", f"{REFERENCE_DATE.isoformat()}T07:00:00"),
        )
        self.conn.commit()

        candidates = get_vdot_candidate_activities(self.conn, self.athlete_id)
        self.assertEqual(candidates, [])


class TestGenerateAndSavePlanUnavailable(unittest.TestCase):
    def test_no_candidate_activities_returns_unavailable_and_writes_nothing(self):
        conn = get_connection(":memory:")
        athlete_id = _build_athlete(conn)
        conn.commit()

        result = generate_and_save_plan(
            conn, athlete_id,
            start_date=datetime.date(2026, 9, 1),
            total_weeks=16,
            days_per_week=4,
            reference_date=REFERENCE_DATE,
        )

        self.assertFalse(result["available"])
        self.assertIn("reason", result)
        self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM training_plan").fetchone()["c"], 0)

    def test_stale_candidate_beyond_freshness_gate_returns_unavailable(self):
        conn = get_connection(":memory:")
        athlete_id = _build_athlete(conn)
        stale_date = REFERENCE_DATE - datetime.timedelta(days=91)
        _insert_activity(conn, athlete_id, stale_date, distance_km=10.0, workout_type="race")
        conn.commit()

        result = generate_and_save_plan(
            conn, athlete_id,
            start_date=datetime.date(2026, 9, 1),
            total_weeks=16,
            days_per_week=4,
            reference_date=REFERENCE_DATE,
        )
        self.assertFalse(result["available"])


class TestGenerateAndSavePlanSuccess(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn, max_hr_bpm=190, max_hr_source="measured")
        recent_race_date = REFERENCE_DATE - datetime.timedelta(days=10)
        _insert_activity(
            self.conn, self.athlete_id, recent_race_date,
            distance_km=10.0, avg_pace_sec_per_km=300, workout_type="race",
        )
        self.conn.commit()

    def test_full_pipeline_writes_to_training_plan(self):
        result = generate_and_save_plan(
            self.conn, self.athlete_id,
            start_date=datetime.date(2026, 9, 1),
            total_weeks=16,
            days_per_week=4,
            reference_date=REFERENCE_DATE,
        )

        self.assertTrue(result["available"])
        self.assertGreater(len(result["training_plan_ids"]), 0)
        self.assertEqual(len(result["schedule"]), len(result["training_plan_ids"]))

        active = get_active_schedule(self.conn, self.athlete_id)
        self.assertEqual(len(active), len(result["training_plan_ids"]))

    def test_regenerating_supersedes_previous_plan(self):
        first = generate_and_save_plan(
            self.conn, self.athlete_id,
            start_date=datetime.date(2026, 9, 1),
            total_weeks=16,
            days_per_week=4,
            reference_date=REFERENCE_DATE,
        )
        second = generate_and_save_plan(
            self.conn, self.athlete_id,
            start_date=datetime.date(2026, 9, 1),
            total_weeks=16,
            days_per_week=5,
            reference_date=REFERENCE_DATE,
        )

        self.assertTrue(first["available"])
        self.assertTrue(second["available"])

        # 舊排程的 id 應已全部被取代（is_active=0），不再出現在目前生效清單。
        active_ids = {row["id"] for row in get_active_schedule(self.conn, self.athlete_id)}
        for old_id in first["training_plan_ids"]:
            self.assertNotIn(old_id, active_ids)

    def test_external_dates_are_excluded_from_written_plan(self):
        external_date = datetime.date(2026, 9, 3)
        result = generate_and_save_plan(
            self.conn, self.athlete_id,
            start_date=datetime.date(2026, 9, 1),
            total_weeks=16,
            days_per_week=4,
            external_dates=[external_date],
            reference_date=REFERENCE_DATE,
        )

        self.assertTrue(result["available"])
        written_dates = {day["date"] for day in result["schedule"]}
        self.assertNotIn(external_date, written_dates)

    def test_constraint_windows_are_applied(self):
        result = generate_and_save_plan(
            self.conn, self.athlete_id,
            start_date=datetime.date(2026, 9, 1),
            total_weeks=16,
            days_per_week=4,
            constraint_windows=[
                {
                    "start_date": datetime.date(2026, 9, 1),
                    "end_date": datetime.date(2026, 9, 7),
                    "level": "skip",
                }
            ],
            reference_date=REFERENCE_DATE,
        )

        self.assertTrue(result["available"])
        first_week = [day for day in result["schedule"] if day["date"] <= datetime.date(2026, 9, 7)]
        self.assertTrue(all(day["workout_type"] == "rest" for day in first_week))

    def test_missing_max_hr_does_not_block_when_max_effort_candidate_available(self):
        """max_hr_bpm 缺值時，只要有全力程度候選（is_max_effort=True，不需換算），
        VDOT 仍可成功推算，orchestrator 不應提前中止。"""
        conn = get_connection(":memory:")
        athlete_id = _build_athlete(conn)  # 無 max_hr_bpm
        recent_race_date = REFERENCE_DATE - datetime.timedelta(days=10)
        _insert_activity(conn, athlete_id, recent_race_date, distance_km=10.0, avg_pace_sec_per_km=300, workout_type="race")
        conn.commit()

        result = generate_and_save_plan(
            conn, athlete_id,
            start_date=datetime.date(2026, 9, 1),
            total_weeks=16,
            days_per_week=4,
            reference_date=REFERENCE_DATE,
        )
        self.assertTrue(result["available"])


if __name__ == "__main__":
    unittest.main()
