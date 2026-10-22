"""readiness 的單元測試——全部使用合成 TSB/wellness/閾值資料，不含真實個人資料。"""

import datetime
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.main.python.models.db import get_connection
from src.main.python.services.readiness import (
    DEFAULT_HIGH_RISK_CONSECUTIVE_TRAINING_DAYS,
    MIN_TSB_SAMPLES_FOR_PERCENTILE,
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


def _tsb_series_with_low_day(
    low_tsb: float = -100.0, load: float = 0.0
) -> list[dict]:
    """建立樣本數足夠讓百分位法生效的序列，最後一天為明顯偏低的 TSB。

    TSB 門檻改為取個人分布的 P10（見 readiness.TSB_LOW_PERCENTILE），
    因此測試不能再只給一兩天——樣本不足時該維度會被跳過而非觸發。
    """
    n = MIN_TSB_SAMPLES_FOR_PERCENTILE + 10
    overrides = {i: 0.0 for i in range(n - 1)}
    overrides[n - 1] = low_tsb
    return _load_series([load] * n, tsb_overrides=overrides)


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
    def test_tsb_below_personal_percentile_triggers_low(self):
        series = _tsb_series_with_low_day()
        result = assess_readiness(series, [])
        self.assertEqual(result[-1]["readiness"], "low")
        self.assertTrue(any(t["dimension"] == "tsb" for t in result[-1]["triggers"]))

    def test_stable_normal_tsb_does_not_trigger(self):
        series = _load_series([100.0] * 3, tsb_overrides={0: -5, 1: -8, 2: -6})
        result = assess_readiness(series, [])
        self.assertTrue(all(r["readiness"] == "normal" for r in result))
        self.assertTrue(all(r["triggers"] == [] for r in result))

    def test_insufficient_samples_skips_tsb_instead_of_guessing(self):
        """樣本不足以建立個人分布時不做 TSB 判斷，而非退回一個猜的絕對值。"""
        series = _load_series([0.0] * 5, tsb_overrides={0: -9999.0})
        result = assess_readiness(series, [])
        self.assertTrue(all(not r["triggers"] for r in result))

    def test_threshold_is_scale_free(self):
        """同一形狀的分布放大 1000 倍，觸發結果不應改變——這正是舊的絕對值
        門檻 (-30) 做不到的事（負荷單位換尺度就整組失效）。
        """
        small = assess_readiness(_tsb_series_with_low_day(-100.0), [])
        large = assess_readiness(_tsb_series_with_low_day(-100_000.0), [])
        self.assertEqual(
            [bool(r["triggers"]) for r in small],
            [bool(r["triggers"]) for r in large],
        )


class TestAssessReadinessTriggerConfidence(unittest.TestCase):
    """每個觸發都要帶信賴度標記（PLAN.md §7.3 三欄位契約）。"""

    def _tsb_trigger(self, max_hr_source):
        series = _tsb_series_with_low_day()
        result = assess_readiness(series, [], max_hr_source=max_hr_source)
        return next(t for t in result[-1]["triggers"] if t["dimension"] == "tsb")

    def test_every_trigger_carries_confidence(self):
        series = _tsb_series_with_low_day()
        result = assess_readiness(series, [])
        self.assertTrue(result[-1]["triggers"])
        for trigger in result[-1]["triggers"]:
            with self.subTest(dimension=trigger["dimension"]):
                self.assertIn("confidence", trigger)
                self.assertIn(trigger["confidence"]["level"], ("high", "low"))

    def test_tsb_confidence_degrades_when_hrmax_not_measured(self):
        """負荷以 %HRR 推算——HRmax 非實測則整條 ATL/CTL/TSB 同步偏移。"""
        self.assertEqual(self._tsb_trigger("watch_display")["confidence"]["level"], "low")

    def test_tsb_stays_low_confidence_even_when_hrmax_measured(self):
        """即使 HRmax 實測，P10 門檻本身仍是無文獻背書的工程決策。"""
        trigger = self._tsb_trigger("measured")
        self.assertEqual(trigger["confidence"]["level"], "low")
        codes = {b["code"] for b in trigger["confidence"]["biases"]}
        self.assertIn("tsb_percentile_engineering", codes)

    def test_omitted_hrmax_source_is_conservatively_degraded(self):
        """省略參數不等於實測——保守視為未實測。"""
        self.assertEqual(self._tsb_trigger(None)["confidence"]["level"], "low")

    def test_consecutive_training_days_has_no_known_bias(self):
        """純粹數活動日，不經估算。"""
        series = _load_series([100.0] * 10)
        result = assess_readiness(series, [], high_risk_consecutive_training_days=3)
        trigger = next(
            t
            for t in result[-1]["triggers"]
            if t["dimension"] == "consecutive_training_days"
        )
        self.assertEqual(trigger["confidence"]["level"], "high")
        self.assertEqual(trigger["confidence"]["biases"], [])


class TestAssessReadinessHrv(unittest.TestCase):
    def test_significant_hrv_drop_triggers_low(self):
        series = _load_series([0.0] * 3)
        # 需要 7 天內至少 3 個有效測量點，否則 7 日均值不可靠（Plews et al.）。
        wellness = _wellness(
            {
                0: {"hrv_ms": 60.0, "hrv_weekly_avg_ms": 60.0},
                1: {"hrv_ms": 60.0, "hrv_weekly_avg_ms": 60.0},
                2: {"hrv_ms": 40.0, "hrv_weekly_avg_ms": 60.0},  # -33%
            }
        )
        result = assess_readiness(series, wellness)
        self.assertEqual(result[2]["readiness"], "low")
        self.assertTrue(any(t["dimension"] == "hrv" for t in result[2]["triggers"]))

    def test_hrv_skipped_when_too_few_measurements_that_week(self):
        """7 日均值由太少的點算出時不硬算——文獻要求每週至少 3 個測量點。"""
        series = _load_series([0.0])
        wellness = _wellness({0: {"hrv_ms": 40.0, "hrv_weekly_avg_ms": 60.0}})
        result = assess_readiness(series, wellness)
        self.assertEqual(result[0]["readiness"], "normal")
        self.assertEqual(result[0]["triggers"], [])

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
        """wellness 缺紀錄但當天仍有負荷資料（不在 dates_without_any_data
        內）——HRV 維度不可評，但 consecutive_training_days 維度仍可評，
        故 readiness 為 "normal" 而非 None。"""
        series = _load_series([0.0])
        result = assess_readiness(series, [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["readiness"], "normal")
        self.assertIn("consecutive_training_days", result[0]["dimensions_evaluated"])
        self.assertTrue(
            any(na["dimension"] == "hrv" for na in result[0]["not_assessed"])
        )


def _trend_wellness(baseline_days: int, recent: list[float]) -> list[dict]:
    """前段為穩定基線（60/64 交替，SD 不為 0），後段接 recent。

    hrv_weekly_avg_ms 刻意跟著當日值走，模擬「7 日均值隨使用者一起下沉」
    ——15% 規則因此不會觸發，正是 SWC 要補的情境。
    """
    values = [60.0 if i % 2 else 64.0 for i in range(baseline_days)] + recent
    return _wellness(
        {i: {"hrv_ms": v, "hrv_weekly_avg_ms": v} for i, v in enumerate(values)}
    )


class TestAssessReadinessHrvTrend(unittest.TestCase):
    """HRV 趨勢（SWC）與 15% 規則並存：只提醒，不影響 readiness。"""

    def test_sustained_decline_flags_below_without_changing_readiness(self):
        wellness = _trend_wellness(28, [50.0] * 7)
        series = _load_series([0.0] * len(wellness))
        last = assess_readiness(series, wellness)[-1]
        self.assertEqual(last["hrv_trend"]["status"], "below")
        self.assertEqual(last["readiness"], "normal")
        self.assertFalse(any(t["dimension"] == "hrv" for t in last["triggers"]))
        self.assertEqual(last["hrv_trend"]["confidence"]["level"], "low")

    def test_stable_hrv_is_within(self):
        wellness = _trend_wellness(35, [])
        series = _load_series([0.0] * len(wellness))
        self.assertEqual(assess_readiness(series, wellness)[-1]["hrv_trend"]["status"], "within")

    def test_short_baseline_is_not_assessed(self):
        """基線不足 20 筆時不評——None 不等於 within。"""
        wellness = _trend_wellness(10, [50.0] * 3)
        series = _load_series([0.0] * len(wellness))
        trend = assess_readiness(series, wellness)[-1]["hrv_trend"]
        self.assertIsNone(trend["status"])
        self.assertIn("基線", trend["reason"])

    def test_no_hrv_today_is_not_assessed(self):
        wellness = _trend_wellness(35, [])
        series = _load_series([0.0] * (len(wellness) + 1))
        self.assertIsNone(assess_readiness(series, wellness)[-1]["hrv_trend"]["status"])


class TestAssessReadinessGeneral(unittest.TestCase):
    def test_empty_series_returns_empty_result(self):
        self.assertEqual(assess_readiness([], []), [])

    def test_covers_full_date_range(self):
        series = _load_series([50.0] * 10)
        result = assess_readiness(series, [])
        self.assertEqual([r["date"] for r in result], [d["date"] for d in series])

    def test_multiple_triggers_all_reported(self):
        series = _tsb_series_with_low_day(load=100.0)  # 非零負荷才會累積連續訓練天數
        last = len(series) - 1
        wellness = _wellness(
            {
                last - 2: {"hrv_ms": 60.0, "hrv_weekly_avg_ms": 60.0},
                last - 1: {"hrv_ms": 60.0, "hrv_weekly_avg_ms": 60.0},
                last: {"hrv_ms": 40.0, "hrv_weekly_avg_ms": 60.0},
            }
        )
        result = assess_readiness(series, wellness)
        last = result[-1]
        triggered_dimensions = {t["dimension"] for t in last["triggers"]}
        self.assertEqual(
            triggered_dimensions, {"consecutive_training_days", "tsb", "hrv"}
        )


class TestAssessReadinessMeasurements(unittest.TestCase):
    """measurements：各維度「數值 / 觸發門檻」，與判斷實際用到的量一致。"""

    def test_all_dimensions_report_value_and_threshold(self):
        series = _tsb_series_with_low_day(load=100.0)
        last = len(series) - 1
        wellness = _wellness(
            {
                last - 2: {"hrv_ms": 60.0, "hrv_weekly_avg_ms": 60.0},
                last - 1: {"hrv_ms": 60.0, "hrv_weekly_avg_ms": 60.0},
                last: {"hrv_ms": 40.0, "hrv_weekly_avg_ms": 60.0},
            }
        )
        m = assess_readiness(series, wellness)[-1]["measurements"]
        self.assertEqual(m["consecutive_training_days"], {"value": len(series), "threshold": 6})
        self.assertEqual(m["tsb"]["value"], -100.0)
        self.assertEqual(m["tsb"]["threshold"], 0.0)  # 其餘天全為 0，P10 為 0
        self.assertEqual(m["hrv_change_pct"], {"value": -33.3, "threshold": -15.0})

    def test_unassessed_dimensions_have_none_value(self):
        """TSB 樣本不足、HRV 無資料時，數值為 None，不可填 0 假裝評過。"""
        m = assess_readiness(_load_series([0.0]), [])[0]["measurements"]
        self.assertIsNone(m["tsb"]["value"])
        self.assertIsNone(m["tsb"]["threshold"])
        self.assertIsNone(m["hrv_change_pct"]["value"])
        self.assertEqual(m["consecutive_training_days"]["value"], 0)

    def test_date_without_any_data_has_no_values(self):
        series = _load_series([0.0, 0.0])
        result = assess_readiness(series, [], dates_without_any_data={series[-1]["date"]})
        m = result[-1]["measurements"]
        self.assertIsNone(m["consecutive_training_days"]["value"])
        self.assertIsNone(m["tsb"]["value"])
        self.assertIsNone(m["hrv_change_pct"]["value"])


class TestAssessReadinessDatesWithoutAnyData(unittest.TestCase):
    """N-13：完全無資料的日期應回報 readiness=None，不是 normal。"""

    def test_date_without_any_data_returns_none_not_normal(self):
        series = _load_series([0.0, 0.0, 0.0])
        no_data_date = series[-1]["date"]
        result = assess_readiness(series, [], dates_without_any_data={no_data_date})
        self.assertIsNone(result[-1]["readiness"])
        self.assertEqual(result[-1]["dimensions_evaluated"], [])
        self.assertEqual(result[-1]["triggers"], [])
        not_assessed_dims = {na["dimension"] for na in result[-1]["not_assessed"]}
        self.assertEqual(
            not_assessed_dims, {"consecutive_training_days", "tsb", "hrv"}
        )
        for na in result[-1]["not_assessed"]:
            self.assertIn("reason", na)

    def test_only_the_flagged_date_is_affected(self):
        series = _load_series([0.0, 0.0, 0.0])
        no_data_date = series[1]["date"]
        result = assess_readiness(series, [], dates_without_any_data={no_data_date})
        self.assertIsNone(result[1]["readiness"])
        self.assertEqual(result[0]["readiness"], "normal")
        self.assertEqual(result[2]["readiness"], "normal")

    def test_omitting_the_parameter_preserves_existing_behavior(self):
        """未傳入時視為空集合，既有呼叫端（未跟進此參數）行為零變動。"""
        series = _load_series([0.0])
        result = assess_readiness(series, [])
        self.assertEqual(result[0]["readiness"], "normal")

    def test_dimensions_evaluated_present_on_normal_day(self):
        series = _load_series([0.0])
        result = assess_readiness(series, [])
        self.assertEqual(
            result[0]["dimensions_evaluated"], ["consecutive_training_days"]
        )

    def test_tsb_not_assessed_reason_present_when_samples_insufficient(self):
        series = _load_series([0.0] * 5, tsb_overrides={0: -9999.0})
        result = assess_readiness(series, [])
        tsb_not_assessed = [
            na for na in result[0]["not_assessed"] if na["dimension"] == "tsb"
        ]
        self.assertEqual(len(tsb_not_assessed), 1)
        self.assertIn("reason", tsb_not_assessed[0])


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
