"""mcp/server.py 的單元測試——直接呼叫 @mcp.tool() 裝飾後的函式本體
（不透過 MCP protocol/stdio），驗證薄封裝層正確轉呼叫既有 services 函式、
且回傳結構中的 datetime.date/datetime.datetime 已序列化成 ISO 字串。

全部使用合成假資料建立臨時 SQLite，不含真實個人資料。
"""

import datetime
import unittest
from types import SimpleNamespace

from src.main.python.models.db import get_connection
from src.main.python.mcp.server import (
    _serialize,
    generate_training_plan,
    get_active_training_plan,
    get_athlete_meta,
    get_readiness_status,
    get_recovery_impact,
    get_session_detail,
    get_wellness_trend,
    list_running_sessions,
)

START = datetime.date(2026, 8, 1)


def _fake_ctx(conn):
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=SimpleNamespace(db=conn))
    )


def _build_athlete(conn, max_hr_bpm=None, resting_hr_bpm=None) -> int:
    cursor = conn.execute(
        """
        INSERT INTO athlete_profile (name, max_hr_bpm, resting_hr_bpm, updated_at)
        VALUES ('測試學員', ?, ?, '2026-01-01T00:00:00')
        """,
        (max_hr_bpm, resting_hr_bpm),
    )
    return cursor.lastrowid


def _insert_activity(conn, athlete_id: int, date: datetime.date, avg_hr_bpm=140) -> int:
    cursor = conn.execute(
        """
        INSERT INTO activities
            (athlete_id, activity_type, started_at, distance_km, duration_sec,
             avg_hr_bpm, source, fetched_at)
        VALUES (?, 'running', ?, 5.0, 1800, ?, 'fit_manual', ?)
        """,
        (
            athlete_id,
            f"{date.isoformat()}T07:00:00",
            avg_hr_bpm,
            f"{date.isoformat()}T07:00:00",
        ),
    )
    return cursor.lastrowid


class TestSerialize(unittest.TestCase):
    def test_date_becomes_iso_string(self):
        self.assertEqual(_serialize(datetime.date(2026, 8, 20)), "2026-08-20")

    def test_datetime_becomes_iso_string(self):
        dt = datetime.datetime(2026, 8, 20, 10, 30, 0)
        self.assertEqual(_serialize(dt), dt.isoformat())

    def test_nested_dict_and_list_recursively_serialized(self):
        value = {
            "date": datetime.date(2026, 8, 20),
            "items": [{"d": datetime.date(2026, 8, 21)}, "plain"],
            "n": 5,
        }
        result = _serialize(value)
        self.assertEqual(result["date"], "2026-08-20")
        self.assertEqual(result["items"][0]["d"], "2026-08-21")
        self.assertEqual(result["items"][1], "plain")
        self.assertEqual(result["n"], 5)

    def test_none_passes_through(self):
        self.assertIsNone(_serialize(None))


class TestReadOnlyTools(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)
        self.conn.commit()

    def test_get_athlete_meta_returns_athlete_info(self):
        result = get_athlete_meta(_fake_ctx(self.conn))
        self.assertEqual(result["athlete"]["id"], self.athlete_id)
        self.assertEqual(result["athlete"]["name"], "測試學員")

    def test_list_running_sessions_returns_no_json_incompatible_types(self):
        _insert_activity(self.conn, self.athlete_id, START)
        self.conn.commit()

        result = list_running_sessions(_fake_ctx(self.conn), range_key="all")
        self.assertEqual(len(result["sessions"]), 1)
        # started_at 已是字串（DB 存字串），此處確認整體結構走過序列化無報錯
        self.assertIsInstance(result["sessions"][0]["started_at"], str)

    def test_get_session_detail_returns_none_for_missing_session(self):
        result = get_session_detail(_fake_ctx(self.conn), session_id=9999)
        self.assertIsNone(result)

    def test_get_session_detail_returns_dict_for_existing_session(self):
        activity_id = _insert_activity(self.conn, self.athlete_id, START)
        self.conn.commit()

        result = get_session_detail(_fake_ctx(self.conn), session_id=activity_id)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], activity_id)

    def test_get_wellness_trend_returns_dict_with_metrics(self):
        result = get_wellness_trend(_fake_ctx(self.conn), range_key="all")
        self.assertIn("metrics", result)

    def test_get_recovery_impact_returns_dict_with_impacts(self):
        result = get_recovery_impact(_fake_ctx(self.conn), range_key="all")
        self.assertIn("impacts", result)

    def test_get_readiness_status_returns_serialized_dates(self):
        _insert_activity(self.conn, self.athlete_id, START)
        self.conn.commit()

        result = get_readiness_status(
            _fake_ctx(self.conn),
            start_date="2026-08-01",
            end_date="2026-08-03",
        )
        self.assertEqual(len(result), 3)
        for day in result:
            self.assertIsInstance(day["date"], str)
            datetime.date.fromisoformat(day["date"])  # 不拋例外即代表格式正確

    def test_get_readiness_status_no_athlete_returns_empty_list(self):
        conn = get_connection(":memory:")
        result = get_readiness_status(
            _fake_ctx(conn), start_date="2026-08-01", end_date="2026-08-01"
        )
        self.assertEqual(result, [])

    def test_get_active_training_plan_empty_when_no_plan(self):
        result = get_active_training_plan(_fake_ctx(self.conn))
        self.assertEqual(result, [])


class TestGenerateTrainingPlanTool(unittest.TestCase):
    def test_returns_unavailable_when_no_vdot_candidate(self):
        conn = get_connection(":memory:")
        _build_athlete(conn)
        conn.commit()

        result = generate_training_plan(
            _fake_ctx(conn),
            start_date="2026-09-01",
            total_weeks=16,
            days_per_week=4,
        )
        self.assertFalse(result["available"])
        self.assertIn("reason", result)

    def test_no_athlete_returns_unavailable_without_error(self):
        conn = get_connection(":memory:")
        result = generate_training_plan(
            _fake_ctx(conn),
            start_date="2026-09-01",
            total_weeks=16,
            days_per_week=4,
        )
        self.assertFalse(result["available"])

    def test_success_writes_plan_and_serializes_schedule_dates(self):
        conn = get_connection(":memory:")
        athlete_id = _build_athlete(conn, max_hr_bpm=195, resting_hr_bpm=55)
        recent_race_date = datetime.date.today() - datetime.timedelta(days=10)
        conn.execute(
            """
            INSERT INTO activities
                (athlete_id, activity_type, started_at, distance_km, duration_sec,
                 avg_pace_sec_per_km, workout_type, source, fetched_at)
            VALUES (?, 'running', ?, 10.0, 3000, 300, 'race', 'fit_manual', ?)
            """,
            (
                athlete_id,
                f"{recent_race_date.isoformat()}T07:00:00",
                f"{recent_race_date.isoformat()}T07:00:00",
            ),
        )
        conn.commit()

        result = generate_training_plan(
            _fake_ctx(conn),
            start_date="2026-09-01",
            total_weeks=16,
            days_per_week=4,
        )
        self.assertTrue(result["available"])
        self.assertGreater(len(result["schedule"]), 0)
        for day in result["schedule"]:
            self.assertIsInstance(day["date"], str)
            datetime.date.fromisoformat(day["date"])

        active = get_active_training_plan(_fake_ctx(conn))
        self.assertEqual(len(active), len(result["training_plan_ids"]))


if __name__ == "__main__":
    unittest.main()
