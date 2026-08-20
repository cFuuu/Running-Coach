"""training_load_queries 的單元測試——全部使用合成假資料建立臨時 SQLite，不含真實個人資料。"""

import datetime
import unittest

from src.main.python.models.db import get_connection
from src.main.python.services.training_load_queries import (
    compute_readiness_for_athlete,
    get_activities_for_load,
    get_hr_params,
    get_wellness_for_readiness,
)

START = datetime.date(2026, 8, 1)


def _build_athlete(conn, max_hr_bpm=None, resting_hr_bpm=None, threshold_days=None) -> int:
    cursor = conn.execute(
        """
        INSERT INTO athlete_profile
            (name, max_hr_bpm, resting_hr_bpm, high_risk_consecutive_training_days, updated_at)
        VALUES ('測試學員', ?, ?, ?, '2026-01-01T00:00:00')
        """,
        (max_hr_bpm, resting_hr_bpm, threshold_days),
    )
    return cursor.lastrowid


def _insert_activity(
    conn,
    athlete_id: int,
    date: datetime.date,
    activity_type: str = "running",
    duration_sec: int = 1800,
    avg_hr_bpm: float | None = None,
    avg_pace_sec_per_km: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO activities
            (athlete_id, activity_type, started_at, distance_km, duration_sec,
             avg_hr_bpm, avg_pace_sec_per_km, source, fetched_at)
        VALUES (?, ?, ?, 5.0, ?, ?, ?, 'fit_manual', ?)
        """,
        (
            athlete_id,
            activity_type,
            f"{date.isoformat()}T07:00:00",
            duration_sec,
            avg_hr_bpm,
            avg_pace_sec_per_km,
            f"{date.isoformat()}T07:00:00",
        ),
    )


def _insert_wellness(
    conn, athlete_id: int, date: datetime.date, hrv_ms=None, hrv_weekly_avg_ms=None
) -> None:
    conn.execute(
        """
        INSERT INTO daily_wellness
            (athlete_id, date, hrv_ms, hrv_weekly_avg_ms, source, fetched_at)
        VALUES (?, ?, ?, ?, 'fit_manual', ?)
        """,
        (athlete_id, date.isoformat(), hrv_ms, hrv_weekly_avg_ms, f"{date.isoformat()}T00:00:00"),
    )


class TestGetActivitiesForLoad(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)

    def test_returns_activities_within_range(self):
        _insert_activity(self.conn, self.athlete_id, START, avg_hr_bpm=140)
        self.conn.commit()

        result = get_activities_for_load(self.conn, self.athlete_id, START, START)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["date"], START)
        self.assertEqual(result[0]["activity_type"], "running")
        self.assertEqual(result[0]["avg_hr_bpm"], 140)

    def test_excludes_activities_outside_range(self):
        _insert_activity(self.conn, self.athlete_id, START + datetime.timedelta(days=10))
        self.conn.commit()

        result = get_activities_for_load(self.conn, self.athlete_id, START, START)
        self.assertEqual(result, [])

    def test_excludes_non_running_non_strength_activity_types(self):
        _insert_activity(self.conn, self.athlete_id, START, activity_type="cycling")
        self.conn.commit()

        result = get_activities_for_load(self.conn, self.athlete_id, START, START)
        self.assertEqual(result, [])

    def test_includes_strength_training(self):
        _insert_activity(self.conn, self.athlete_id, START, activity_type="strength_training", duration_sec=3600)
        self.conn.commit()

        result = get_activities_for_load(self.conn, self.athlete_id, START, START)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["activity_type"], "strength_training")

    def test_excludes_other_athletes_activities(self):
        other_athlete_id = _build_athlete(self.conn)
        self.conn.commit()
        _insert_activity(self.conn, other_athlete_id, START)
        self.conn.commit()

        result = get_activities_for_load(self.conn, self.athlete_id, START, START)
        self.assertEqual(result, [])


class TestGetHrParams(unittest.TestCase):
    def test_returns_configured_values(self):
        conn = get_connection(":memory:")
        athlete_id = _build_athlete(conn, max_hr_bpm=190, resting_hr_bpm=50)
        conn.commit()

        max_hr, resting_hr = get_hr_params(conn, athlete_id)
        self.assertEqual(max_hr, 190)
        self.assertEqual(resting_hr, 50)

    def test_missing_values_return_none_without_error(self):
        conn = get_connection(":memory:")
        athlete_id = _build_athlete(conn)
        conn.commit()

        max_hr, resting_hr = get_hr_params(conn, athlete_id)
        self.assertIsNone(max_hr)
        self.assertIsNone(resting_hr)

    def test_nonexistent_athlete_returns_none_without_error(self):
        conn = get_connection(":memory:")
        max_hr, resting_hr = get_hr_params(conn, 999)
        self.assertIsNone(max_hr)
        self.assertIsNone(resting_hr)


class TestGetWellnessForReadiness(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)

    def test_returns_wellness_within_range(self):
        _insert_wellness(self.conn, self.athlete_id, START, hrv_ms=50.0, hrv_weekly_avg_ms=55.0)
        self.conn.commit()

        result = get_wellness_for_readiness(self.conn, self.athlete_id, START, START)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["date"], START)
        self.assertEqual(result[0]["hrv_ms"], 50.0)

    def test_missing_day_is_simply_absent_not_a_placeholder(self):
        result = get_wellness_for_readiness(self.conn, self.athlete_id, START, START)
        self.assertEqual(result, [])

    def test_excludes_outside_range(self):
        _insert_wellness(self.conn, self.athlete_id, START + datetime.timedelta(days=10))
        self.conn.commit()

        result = get_wellness_for_readiness(self.conn, self.athlete_id, START, START)
        self.assertEqual(result, [])


class TestComputeReadinessForAthlete(unittest.TestCase):
    def test_end_to_end_produces_nonempty_series(self):
        conn = get_connection(":memory:")
        athlete_id = _build_athlete(conn, max_hr_bpm=190, resting_hr_bpm=50)
        conn.commit()

        for i in range(5):
            date = START + datetime.timedelta(days=i)
            _insert_activity(conn, athlete_id, date, avg_hr_bpm=140)
            _insert_wellness(conn, athlete_id, date, hrv_ms=50.0, hrv_weekly_avg_ms=52.0)
        conn.commit()

        end_date = START + datetime.timedelta(days=4)
        result = compute_readiness_for_athlete(conn, athlete_id, START, end_date)

        self.assertEqual(len(result), 5)
        self.assertTrue(all("readiness" in day for day in result))

    def test_covers_full_range_even_with_no_data(self):
        conn = get_connection(":memory:")
        athlete_id = _build_athlete(conn)
        conn.commit()

        end_date = START + datetime.timedelta(days=2)
        result = compute_readiness_for_athlete(conn, athlete_id, START, end_date)
        self.assertEqual(len(result), 3)
        self.assertTrue(all(day["readiness"] == "normal" for day in result))

    def test_missing_hr_params_do_not_raise_and_use_pace_fallback_when_available(self):
        conn = get_connection(":memory:")
        athlete_id = _build_athlete(conn)  # 無 max_hr/resting_hr
        conn.commit()

        _insert_activity(conn, athlete_id, START, avg_pace_sec_per_km=300)
        conn.commit()

        result = compute_readiness_for_athlete(
            conn, athlete_id, START, START, easy_pace_fast_sec_per_km=330.0
        )
        self.assertEqual(len(result), 1)

    def test_personalized_threshold_is_picked_up_from_athlete_profile(self):
        conn = get_connection(":memory:")
        athlete_id = _build_athlete(conn, threshold_days=3)
        conn.commit()

        for i in range(3):
            date = START + datetime.timedelta(days=i)
            _insert_activity(conn, athlete_id, date, avg_hr_bpm=140)
        conn.commit()

        end_date = START + datetime.timedelta(days=2)
        result = compute_readiness_for_athlete(conn, athlete_id, START, end_date)
        # 無 max_hr/resting_hr 時心率強度無法計算，但配速也未提供，活動被排除，
        # 負荷序列全為 0；此測試只需確認個人化閾值有被正確查出並傳遞，不報錯。
        self.assertEqual(result[-1]["threshold_source"], "personalized")


if __name__ == "__main__":
    unittest.main()
