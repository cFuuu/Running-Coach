"""training_plan_store 的單元測試——全部使用合成課表與臨時 SQLite，不含真實個人資料。"""

import datetime
import unittest

from src.main.python.models.db import get_connection
from src.main.python.services.training_plan_store import (
    get_active_schedule,
    get_plan_history_for_date,
    save_schedule,
)

START = datetime.date(2026, 8, 1)


_DEFAULT_PACE_ZONE = {"fast_sec_per_km": 300, "slow_sec_per_km": 330}


def _day(
    date: datetime.date,
    workout_type: str = "easy",
    target_distance_km: float = 8.0,
    pace_zone: dict | None | object = "__default__",
) -> dict:
    resolved_pace_zone = _DEFAULT_PACE_ZONE if pace_zone == "__default__" else pace_zone
    return {
        "date": date,
        "phase": "base",
        "workout_type": workout_type,
        "target_distance_km": target_distance_km,
        "pace_zone": resolved_pace_zone,
        "fueling_rehearsal": False,
        "constraint_level": None,
    }


def _build_athlete(conn) -> int:
    cursor = conn.execute(
        "INSERT INTO athlete_profile (name, updated_at) VALUES ('測試學員', '2026-01-01T00:00:00')"
    )
    return cursor.lastrowid


class TestSaveScheduleFirstWrite(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)

    def test_writes_correct_fields(self):
        schedule = [_day(START, workout_type="tempo", target_distance_km=10.0)]
        save_schedule(self.conn, self.athlete_id, schedule, created_at="2026-08-01T00:00:00")

        row = self.conn.execute("SELECT * FROM training_plan").fetchone()
        self.assertEqual(row["planned_date"], "2026-08-01")
        self.assertEqual(row["workout_type"], "tempo")
        self.assertEqual(row["planned_distance_km"], 10.0)
        self.assertEqual(row["planned_pace_sec_per_km"], 315)  # midpoint of 300/330
        self.assertEqual(row["plan_source"], "generated")

    def test_first_write_is_active_with_no_superseded_by(self):
        schedule = [_day(START)]
        save_schedule(self.conn, self.athlete_id, schedule)

        row = self.conn.execute("SELECT * FROM training_plan").fetchone()
        self.assertEqual(row["is_active"], 1)
        self.assertIsNone(row["superseded_by"])

    def test_rest_day_has_null_pace(self):
        schedule = [_day(START, workout_type="rest", target_distance_km=0.0, pace_zone=None)]
        save_schedule(self.conn, self.athlete_id, schedule)

        row = self.conn.execute("SELECT * FROM training_plan").fetchone()
        self.assertIsNone(row["planned_pace_sec_per_km"])

    def test_returns_new_row_ids_in_order(self):
        schedule = [_day(START), _day(START + datetime.timedelta(days=1))]
        ids = save_schedule(self.conn, self.athlete_id, schedule)
        self.assertEqual(len(ids), 2)
        rows = self.conn.execute("SELECT id, planned_date FROM training_plan ORDER BY id").fetchall()
        self.assertEqual([r["id"] for r in rows], ids)


class TestSaveScheduleSupersedes(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)

    def test_regenerating_same_date_supersedes_old_row(self):
        first = save_schedule(
            self.conn, self.athlete_id, [_day(START, workout_type="easy")],
            created_at="2026-08-01T00:00:00",
        )
        second = save_schedule(
            self.conn, self.athlete_id, [_day(START, workout_type="tempo")],
            created_at="2026-08-02T00:00:00",
        )

        old_row = self.conn.execute(
            "SELECT * FROM training_plan WHERE id = ?", (first[0],)
        ).fetchone()
        new_row = self.conn.execute(
            "SELECT * FROM training_plan WHERE id = ?", (second[0],)
        ).fetchone()

        self.assertEqual(old_row["is_active"], 0)
        self.assertEqual(old_row["superseded_by"], second[0])
        self.assertEqual(old_row["workout_type"], "easy")  # 舊列內容不被覆寫
        self.assertEqual(new_row["is_active"], 1)
        self.assertIsNone(new_row["superseded_by"])
        self.assertEqual(new_row["workout_type"], "tempo")

    def test_old_row_is_not_deleted(self):
        first = save_schedule(self.conn, self.athlete_id, [_day(START)], created_at="2026-08-01T00:00:00")
        save_schedule(self.conn, self.athlete_id, [_day(START)], created_at="2026-08-02T00:00:00")

        count = self.conn.execute("SELECT COUNT(*) AS c FROM training_plan").fetchone()["c"]
        self.assertEqual(count, 2)
        still_there = self.conn.execute(
            "SELECT id FROM training_plan WHERE id = ?", (first[0],)
        ).fetchone()
        self.assertIsNotNone(still_there)

    def test_different_dates_do_not_supersede_each_other(self):
        save_schedule(self.conn, self.athlete_id, [_day(START)], created_at="2026-08-01T00:00:00")
        save_schedule(
            self.conn, self.athlete_id, [_day(START + datetime.timedelta(days=1))],
            created_at="2026-08-02T00:00:00",
        )
        rows = self.conn.execute("SELECT is_active FROM training_plan").fetchall()
        self.assertTrue(all(r["is_active"] == 1 for r in rows))

    def test_external_plan_source_row_is_never_superseded(self):
        self.conn.execute(
            """
            INSERT INTO training_plan
                (athlete_id, planned_date, workout_type, plan_source, is_active, created_at)
            VALUES (?, ?, 'easy', 'external', 1, '2026-07-01T00:00:00')
            """,
            (self.athlete_id, START.isoformat()),
        )
        self.conn.commit()
        external_id = self.conn.execute(
            "SELECT id FROM training_plan WHERE plan_source = 'external'"
        ).fetchone()["id"]

        save_schedule(self.conn, self.athlete_id, [_day(START)], created_at="2026-08-01T00:00:00")

        external_row = self.conn.execute(
            "SELECT is_active, superseded_by FROM training_plan WHERE id = ?", (external_id,)
        ).fetchone()
        self.assertEqual(external_row["is_active"], 1)
        self.assertIsNone(external_row["superseded_by"])

    def test_different_athlete_same_date_does_not_supersede(self):
        conn = self.conn
        other_athlete_id = _build_athlete(conn)
        # SQLite 遊標插入後才 commit，_build_athlete 內未 commit，這裡先手動 commit 確保可查。
        conn.commit()

        save_schedule(conn, self.athlete_id, [_day(START)], created_at="2026-08-01T00:00:00")
        save_schedule(conn, other_athlete_id, [_day(START)], created_at="2026-08-01T00:00:00")

        rows = conn.execute("SELECT athlete_id, is_active FROM training_plan").fetchall()
        self.assertTrue(all(r["is_active"] == 1 for r in rows))


class TestGetActiveSchedule(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)

    def test_only_active_rows_returned(self):
        save_schedule(self.conn, self.athlete_id, [_day(START, workout_type="easy")], created_at="2026-08-01T00:00:00")
        save_schedule(self.conn, self.athlete_id, [_day(START, workout_type="tempo")], created_at="2026-08-02T00:00:00")

        active = get_active_schedule(self.conn, self.athlete_id)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["workout_type"], "tempo")

    def test_ordered_by_date(self):
        schedule = [
            _day(START + datetime.timedelta(days=2)),
            _day(START),
            _day(START + datetime.timedelta(days=1)),
        ]
        save_schedule(self.conn, self.athlete_id, schedule)
        active = get_active_schedule(self.conn, self.athlete_id)
        self.assertEqual(
            [row["planned_date"] for row in active],
            [START.isoformat(), (START + datetime.timedelta(days=1)).isoformat(), (START + datetime.timedelta(days=2)).isoformat()],
        )

    def test_includes_external_rows(self):
        self.conn.execute(
            """
            INSERT INTO training_plan
                (athlete_id, planned_date, workout_type, plan_source, is_active, created_at)
            VALUES (?, ?, 'easy', 'external', 1, '2026-07-01T00:00:00')
            """,
            (self.athlete_id, START.isoformat()),
        )
        self.conn.commit()

        active = get_active_schedule(self.conn, self.athlete_id)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["plan_source"], "external")

    def test_empty_when_no_plan(self):
        self.assertEqual(get_active_schedule(self.conn, self.athlete_id), [])


class TestGetPlanHistoryForDate(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)

    def test_includes_superseded_and_active_rows_in_created_order(self):
        first = save_schedule(
            self.conn, self.athlete_id, [_day(START, workout_type="easy")],
            created_at="2026-08-01T00:00:00",
        )
        second = save_schedule(
            self.conn, self.athlete_id, [_day(START, workout_type="tempo")],
            created_at="2026-08-02T00:00:00",
        )

        history = get_plan_history_for_date(self.conn, self.athlete_id, START)
        self.assertEqual([row["id"] for row in history], [first[0], second[0]])
        self.assertEqual(history[0]["is_active"], 0)
        self.assertEqual(history[1]["is_active"], 1)

    def test_empty_for_date_with_no_plan(self):
        history = get_plan_history_for_date(self.conn, self.athlete_id, START)
        self.assertEqual(history, [])


if __name__ == "__main__":
    unittest.main()
