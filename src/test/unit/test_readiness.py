"""readiness 的單元測試——全部使用合成 TSB/wellness/閾值資料，不含真實個人資料。"""

import datetime
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.main.python.models.db import get_connection
from src.main.python.services.readiness import (
    DEFAULT_HIGH_RISK_CONSECUTIVE_TRAINING_DAYS,
    TSB_LOW_THRESHOLD,
    assess_readiness,
    suggest_recovery_threshold,
)

START = datetime.date(2026, 8, 1)


def _load_series(
    loads: list[float], tsb_overrides: dict[int, float] | None = None
) -> list[dict]:
    """建立最小的 training_load_series 輸入：atl/ctl 不重要，測試只依賴 tsb。"""
    tsb_overrides = tsb_overrides or {}
    return [
        {
            "date": START + datetime.timedelta(days=i),
            "load": load,
            "atl": 0.0,
            "ctl": 0.0,
            "tsb": tsb_overrides.get(i, 0.0),
            "uncertain": False,
        }
        for i, load in enumerate(loads)
    ]


def _wellness(entries: dict[int, dict]) -> list[dict]:
    return [
        {"date": START + datetime.timedelta(days=i), **fields}
        for i, fields in entries.items()
    ]


class TestAssessReadinessConsecutiveTraining(unittest.TestCase):
    def test_default_threshold_triggers_low_at_boundary(self):
        loads = [100.0] * DEFAULT_HIGH_RISK_CONSECUTIVE_TRAINING_DAYS
        series = _load_series(loads)
        result = assess_readiness(series, [])
        last = result[-1]
        self.assertEqual(last["readiness"], "low")
        self.assertTrue(any(t["dimension"] == "consecutive_training_days" for t in last["triggers"]))
        self.assertEqual(last["threshold_source"], "default")

    def test_below_default_threshold_does_not_trigger_consecutive_dimension(self):
        loads = [100.0] * (DEFAULT_HIGH_RISK_CONSECUTIVE_TRAINING_DAYS - 1)
        series = _load_series(loads)
        result = assess_readiness(series, [])
        last = result[-1]
        self.assertFalse(any(t["dimension"] == "consecutive_training_days" for t in last["triggers"]))

    def test_rest_day_resets_consecutive_count(self):
        loads = [100.0] * 5 + [0.0] + [100.0] * 3
        series = _load_series(loads)
        result = assess_readiness(series, [])
        self.assertEqual(result[-1]["consecutive_training_days"], 3)

    def test_personalized_threshold_overrides_default(self):
        loads = [100.0] * 3
        series = _load_series(loads)
        result = assess_readiness(series, [], high_risk_consecutive_training_days=3)
        last = result[-1]
        self.assertEqual(last["readiness"], "low")
        self.assertEqual(last["threshold_source"], "personalized")

    def test_personalized_threshold_of_none_falls_back_to_default(self):
        loads = [100.0] * (DEFAULT_HIGH_RISK_CONSECUTIVE_TRAINING_DAYS - 1)
        series = _load_series(loads)
        result = assess_readiness(series, [], high_risk_consecutive_training_days=None)
        self.assertEqual(result[-1]["threshold_source"], "default")


class TestAssessReadinessTsb(unittest.TestCase):
    def test_tsb_below_threshold_triggers_low(self):
        series = _load_series([0.0], tsb_overrides={0: TSB_LOW_THRESHOLD - 5})
        result = assess_readiness(series, [])
        self.assertEqual(result[0]["readiness"], "low")
        self.assertTrue(any(t["dimension"] == "tsb" for t in result[0]["triggers"]))

    def test_stable_normal_tsb_does_not_trigger(self):
        series = _load_series([100.0] * 3, tsb_overrides={0: -5, 1: -8, 2: -6})
        result = assess_readiness(series, [])
        self.assertTrue(all(r["readiness"] == "normal" for r in result))
        self.assertTrue(all(r["triggers"] == [] for r in result))


class TestAssessReadinessHrv(unittest.TestCase):
    def test_significant_hrv_drop_triggers_low(self):
        series = _load_series([0.0])
        wellness = _wellness({0: {"hrv_ms": 40.0, "hrv_weekly_avg_ms": 60.0}})  # -33%
        result = assess_readiness(series, wellness)
        self.assertEqual(result[0]["readiness"], "low")
        self.assertTrue(any(t["dimension"] == "hrv" for t in result[0]["triggers"]))

    def test_minor_hrv_dip_does_not_trigger(self):
        series = _load_series([0.0])
        wellness = _wellness({0: {"hrv_ms": 58.0, "hrv_weekly_avg_ms": 60.0}})  # -3.3%
        result = assess_readiness(series, wellness)
        self.assertEqual(result[0]["readiness"], "normal")

    def test_missing_hrv_data_degrades_gracefully_without_error(self):
        """部分身體數據缺失時，判斷邏輯仍可運作並給出結果，而非報錯或整體排除。"""
        series = _load_series([0.0, 0.0])
        wellness = _wellness({0: {"hrv_ms": None, "hrv_weekly_avg_ms": None}})  # day 1 無紀錄
        result = assess_readiness(series, wellness)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(r["readiness"] == "normal" for r in result))

    def test_missing_wellness_record_entirely_degrades_gracefully(self):
        series = _load_series([0.0])
        result = assess_readiness(series, [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["readiness"], "normal")


class TestAssessReadinessGeneral(unittest.TestCase):
    def test_empty_series_returns_empty_result(self):
        self.assertEqual(assess_readiness([], []), [])

    def test_covers_full_date_range(self):
        series = _load_series([50.0] * 10)
        result = assess_readiness(series, [])
        self.assertEqual([r["date"] for r in result], [d["date"] for d in series])

    def test_multiple_triggers_all_reported(self):
        loads = [100.0] * DEFAULT_HIGH_RISK_CONSECUTIVE_TRAINING_DAYS
        series = _load_series(
            loads, tsb_overrides={DEFAULT_HIGH_RISK_CONSECUTIVE_TRAINING_DAYS - 1: TSB_LOW_THRESHOLD - 1}
        )
        wellness = _wellness(
            {DEFAULT_HIGH_RISK_CONSECUTIVE_TRAINING_DAYS - 1: {"hrv_ms": 40.0, "hrv_weekly_avg_ms": 60.0}}
        )
        result = assess_readiness(series, wellness)
        last = result[-1]
        triggered_dimensions = {t["dimension"] for t in last["triggers"]}
        self.assertEqual(
            triggered_dimensions, {"consecutive_training_days", "tsb", "hrv"}
        )


class TestAssessReadinessDoesNotMutateTrainingPlan(unittest.TestCase):
    def test_training_plan_table_unchanged_after_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "readiness_test.db"
            conn = get_connection(db_path)
            conn.execute(
                "INSERT INTO athlete_profile (name, updated_at) VALUES ('測試學員', '2026-01-01T00:00:00')"
            )
            athlete_id = conn.execute("SELECT id FROM athlete_profile").fetchone()["id"]
            conn.execute(
                """
                INSERT INTO training_plan
                    (athlete_id, planned_date, workout_type, plan_source, created_at)
                VALUES (?, '2026-08-01', 'easy', 'generated', '2026-08-01T00:00:00')
                """,
                (athlete_id,),
            )
            conn.commit()

            before = conn.execute("SELECT * FROM training_plan").fetchall()
            before_rows = [dict(row) for row in before]

            series = _load_series([100.0] * DEFAULT_HIGH_RISK_CONSECUTIVE_TRAINING_DAYS)
            assess_readiness(series, [])

            after = conn.execute("SELECT * FROM training_plan").fetchall()
            after_rows = [dict(row) for row in after]
            conn.close()

            self.assertEqual(before_rows, after_rows)


def _seed_athlete(conn: sqlite3.Connection) -> int:
    conn.execute(
        "INSERT INTO athlete_profile (name, updated_at) VALUES ('測試學員', '2026-01-01T00:00:00')"
    )
    return conn.execute("SELECT id FROM athlete_profile").fetchone()["id"]


def _insert_activity(conn: sqlite3.Connection, athlete_id: int, date: datetime.date) -> None:
    conn.execute(
        """
        INSERT INTO activities
            (athlete_id, activity_type, started_at, distance_km, duration_sec,
             source, fetched_at)
        VALUES (?, 'running', ?, 5.0, 1800, 'fit_manual', ?)
        """,
        (athlete_id, f"{date.isoformat()}T07:00:00", f"{date.isoformat()}T07:00:00"),
    )


def _insert_wellness(
    conn: sqlite3.Connection,
    athlete_id: int,
    date: datetime.date,
    hrv_ms: float | None,
    hrv_weekly_avg_ms: float | None,
) -> None:
    conn.execute(
        """
        INSERT INTO daily_wellness
            (athlete_id, date, hrv_ms, hrv_weekly_avg_ms, source, fetched_at)
        VALUES (?, ?, ?, ?, 'fit_manual', ?)
        """,
        (athlete_id, date.isoformat(), hrv_ms, hrv_weekly_avg_ms, f"{date.isoformat()}T00:00:00"),
    )


class TestSuggestRecoveryThreshold(unittest.TestCase):
    def _build_db(self):
        conn = get_connection(":memory:")
        athlete_id = _seed_athlete(conn)
        return conn, athlete_id

    def test_insufficient_data_returns_unavailable(self):
        conn, athlete_id = self._build_db()
        # 只有 1 段連續訓練，遠低於 _SUGGESTION_MIN_TRAINING_STREAKS。
        for i in range(3):
            date = START + datetime.timedelta(days=i)
            _insert_activity(conn, athlete_id, date)
            _insert_wellness(conn, athlete_id, date, hrv_ms=50.0, hrv_weekly_avg_ms=52.0)
        conn.commit()

        result = suggest_recovery_threshold(conn, athlete_id)
        self.assertFalse(result["available"])
        self.assertIn("reason", result)

    def test_no_activities_returns_unavailable(self):
        conn, athlete_id = self._build_db()
        result = suggest_recovery_threshold(conn, athlete_id)
        self.assertFalse(result["available"])

    def test_sufficient_degraded_streaks_yields_suggestion(self):
        conn, athlete_id = self._build_db()
        # 建立 4 段連續訓練（分開的日期區塊，避免互相黏連），每段結束當天
        # HRV 明顯下降（-30%），確保資料量與訊號都足夠支持建議。
        streak_starts = [
            START,
            START + datetime.timedelta(days=10),
            START + datetime.timedelta(days=20),
            START + datetime.timedelta(days=30),
        ]
        streak_length = 5
        for streak_start in streak_starts:
            for i in range(streak_length):
                date = streak_start + datetime.timedelta(days=i)
                _insert_activity(conn, athlete_id, date)
                is_last_day = i == streak_length - 1
                hrv_ms = 40.0 if is_last_day else 55.0
                _insert_wellness(conn, athlete_id, date, hrv_ms=hrv_ms, hrv_weekly_avg_ms=60.0)
        conn.commit()

        result = suggest_recovery_threshold(conn, athlete_id)
        self.assertTrue(result["available"])
        self.assertEqual(result["suggested_threshold_days"], streak_length)
        self.assertEqual(result["basis"]["training_streaks_analyzed"], 4)
        self.assertEqual(result["basis"]["streaks_with_degradation"], 4)
        self.assertIn("explanation", result["basis"])

    def test_sufficient_streaks_but_no_degradation_returns_unavailable(self):
        conn, athlete_id = self._build_db()
        streak_starts = [
            START,
            START + datetime.timedelta(days=10),
            START + datetime.timedelta(days=20),
        ]
        for streak_start in streak_starts:
            for i in range(4):
                date = streak_start + datetime.timedelta(days=i)
                _insert_activity(conn, athlete_id, date)
                # HRV 穩定，沒有惡化訊號。
                _insert_wellness(conn, athlete_id, date, hrv_ms=58.0, hrv_weekly_avg_ms=60.0)
        conn.commit()

        result = suggest_recovery_threshold(conn, athlete_id)
        self.assertFalse(result["available"])

    def test_does_not_write_to_athlete_profile(self):
        conn, athlete_id = self._build_db()
        streak_starts = [
            START,
            START + datetime.timedelta(days=10),
            START + datetime.timedelta(days=20),
        ]
        for streak_start in streak_starts:
            for i in range(4):
                date = streak_start + datetime.timedelta(days=i)
                _insert_activity(conn, athlete_id, date)
                _insert_wellness(conn, athlete_id, date, hrv_ms=40.0, hrv_weekly_avg_ms=60.0)
        conn.commit()

        before = dict(
            conn.execute("SELECT * FROM athlete_profile WHERE id = ?", (athlete_id,)).fetchone()
        )
        suggest_recovery_threshold(conn, athlete_id)
        after = dict(
            conn.execute("SELECT * FROM athlete_profile WHERE id = ?", (athlete_id,)).fetchone()
        )
        self.assertEqual(before, after)

    def test_activities_without_matching_hrv_data_are_excluded_from_sample(self):
        """訓練段結束日沒有對應 HRV 紀錄的樣本不計入可分析樣本數。"""
        conn, athlete_id = self._build_db()
        for i in range(3):
            date = START + datetime.timedelta(days=i)
            _insert_activity(conn, athlete_id, date)
            # 完全不插入對應的 daily_wellness 紀錄。

        result = suggest_recovery_threshold(conn, athlete_id)
        self.assertFalse(result["available"])


if __name__ == "__main__":
    unittest.main()
