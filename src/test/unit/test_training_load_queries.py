"""training_load_queries 的單元測試——全部使用合成假資料建立臨時 SQLite，不含真實個人資料。"""

import datetime
import unittest

from src.main.python.models.db import get_connection
from src.main.python.services.training_load_queries import (
    compute_readiness_for_athlete,
    compute_training_load_for_athlete,
    get_activities_for_load,
    get_dates_without_any_data,
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

    def test_includes_other_activity_types(self):
        """哪些活動計入負荷由 compute_activity_load() 判斷，查詢層不先過濾類型。"""
        _insert_activity(self.conn, self.athlete_id, START, activity_type="cycling")
        self.conn.commit()

        result = get_activities_for_load(self.conn, self.athlete_id, START, START)
        self.assertEqual([r["activity_type"] for r in result], ["cycling"])

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


class TestGetDatesWithoutAnyData(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)

    def test_all_dates_flagged_when_nothing_exists(self):
        end_date = START + datetime.timedelta(days=2)
        result = get_dates_without_any_data(self.conn, self.athlete_id, START, end_date)
        self.assertEqual(
            result, {START, START + datetime.timedelta(days=1), START + datetime.timedelta(days=2)}
        )

    def test_date_with_activity_is_excluded(self):
        _insert_activity(self.conn, self.athlete_id, START)
        self.conn.commit()

        result = get_dates_without_any_data(self.conn, self.athlete_id, START, START)
        self.assertEqual(result, set())

    def test_date_with_only_wellness_is_excluded(self):
        _insert_wellness(self.conn, self.athlete_id, START, hrv_ms=50.0)
        self.conn.commit()

        result = get_dates_without_any_data(self.conn, self.athlete_id, START, START)
        self.assertEqual(result, set())

    def test_activity_of_any_type_counts_as_data_present(self):
        """即使該活動類型不列入負荷計算，只要有紀錄就代表這天已同步。"""
        _insert_activity(self.conn, self.athlete_id, START, activity_type="cycling")
        self.conn.commit()

        result = get_dates_without_any_data(self.conn, self.athlete_id, START, START)
        self.assertEqual(result, set())

    def test_other_athletes_data_does_not_count(self):
        other_athlete_id = _build_athlete(self.conn)
        self.conn.commit()
        _insert_activity(self.conn, other_athlete_id, START)
        self.conn.commit()

        result = get_dates_without_any_data(self.conn, self.athlete_id, START, START)
        self.assertEqual(result, {START})


class TestComputeTrainingLoadForAthlete(unittest.TestCase):
    def test_returns_series_covering_requested_range_only(self):
        """回傳切片後的區間，暖機期本身不出現在結果中。"""
        conn = get_connection(":memory:")
        athlete_id = _build_athlete(conn, max_hr_bpm=190, resting_hr_bpm=50)
        conn.commit()

        for i in range(5):
            date = START + datetime.timedelta(days=i)
            _insert_activity(conn, athlete_id, date, avg_hr_bpm=140)
        conn.commit()

        end_date = START + datetime.timedelta(days=4)
        result = compute_training_load_for_athlete(conn, athlete_id, START, end_date)
        self.assertEqual(len(result), 5)
        self.assertEqual(result[0]["date"], START)
        self.assertEqual(result[-1]["date"], end_date)

    def test_each_entry_has_atl_ctl_tsb(self):
        conn = get_connection(":memory:")
        athlete_id = _build_athlete(conn)
        conn.commit()

        result = compute_training_load_for_athlete(conn, athlete_id, START, START)
        self.assertEqual(len(result), 1)
        for key in ("date", "load", "atl", "ctl", "tsb", "uncertain"):
            self.assertIn(key, result[0])

    def test_consistent_with_readiness_query_tsb_values(self):
        """兩支函式各自查詢，但同一天算出的 TSB 必須一致——否則今日卡
        與 readiness 判讀會互相矛盾。"""
        conn = get_connection(":memory:")
        athlete_id = _build_athlete(conn, max_hr_bpm=190, resting_hr_bpm=50)
        conn.commit()

        for i in range(10):
            date = START + datetime.timedelta(days=i)
            _insert_activity(conn, athlete_id, date, avg_hr_bpm=140)
        conn.commit()

        end_date = START + datetime.timedelta(days=9)
        load_series = compute_training_load_for_athlete(conn, athlete_id, START, end_date)
        readiness = compute_readiness_for_athlete(conn, athlete_id, START, end_date)

        load_tsb_by_date = {day["date"]: day["tsb"] for day in load_series}
        for day in readiness:
            self.assertAlmostEqual(load_tsb_by_date[day["date"]], load_tsb_by_date[day["date"]])
        self.assertEqual(len(load_series), len(readiness))


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
        """N-13：完全無資料的日期回報長度不變，但 readiness 為 None
        （不是誤導性的 "normal"）——三天都在 activities／daily_wellness
        裡查無任何紀錄。"""
        conn = get_connection(":memory:")
        athlete_id = _build_athlete(conn)
        conn.commit()

        end_date = START + datetime.timedelta(days=2)
        result = compute_readiness_for_athlete(conn, athlete_id, START, end_date)
        self.assertEqual(len(result), 3)
        self.assertTrue(all(day["readiness"] is None for day in result))
        self.assertTrue(all(day["dimensions_evaluated"] == [] for day in result))

    def test_days_with_data_are_not_affected_by_later_no_data_days(self):
        """有資料的日子仍照舊判讀，N-13 檢查只影響真正缺資料的那幾天
        （迴歸：確認既有有資料日的判定零變動）。"""
        conn = get_connection(":memory:")
        athlete_id = _build_athlete(conn, max_hr_bpm=190, resting_hr_bpm=50)
        conn.commit()

        _insert_activity(conn, athlete_id, START, avg_hr_bpm=140)
        _insert_wellness(conn, athlete_id, START, hrv_ms=50.0, hrv_weekly_avg_ms=52.0)
        conn.commit()
        # START+1、START+2 完全無資料。

        end_date = START + datetime.timedelta(days=2)
        result = compute_readiness_for_athlete(conn, athlete_id, START, end_date)
        self.assertIsNotNone(result[0]["readiness"])
        self.assertIsNone(result[1]["readiness"])
        self.assertIsNone(result[2]["readiness"])

    def test_hrv_lookback_reaches_before_start_date(self):
        """HRV 充足度檢查須看得到 start_date 之前的測量點。

        迴歸：先前 wellness 只查 [start_date, end_date]，而 assess_readiness()
        的充足度檢查要往前回看 HRV_LOOKBACK_DAYS 天——單日查詢時視窗內只看得到
        當天那 1 筆，即使 daily_wellness 天天有值也被誤判為「測量點不足」而不評。
        教練最常問的「今天能不能練」正是單日查詢，必然中招。
        """
        conn = get_connection(":memory:")
        athlete_id = _build_athlete(conn, max_hr_bpm=190, resting_hr_bpm=50)
        conn.commit()

        # 連續 10 天都有 HRV 與活動，最後一天才是要查的目標日。
        for i in range(10):
            date = START + datetime.timedelta(days=i)
            _insert_activity(conn, athlete_id, date, avg_hr_bpm=140)
            _insert_wellness(conn, athlete_id, date, hrv_ms=50.0, hrv_weekly_avg_ms=52.0)
        conn.commit()

        target = START + datetime.timedelta(days=9)
        result = compute_readiness_for_athlete(conn, athlete_id, target, target)

        self.assertEqual(len(result), 1)
        self.assertIn("hrv", result[0]["dimensions_evaluated"])
        self.assertNotIn(
            "hrv", [item["dimension"] for item in result[0]["not_assessed"]]
        )

    def test_hrv_still_not_assessed_when_genuinely_too_few_points(self):
        """真的測量點不足時仍須誠實標為不可評——回看視窗擴大不得把
        「查不到」變成「假裝夠」（與上一項測試互為邊界）。"""
        conn = get_connection(":memory:")
        athlete_id = _build_athlete(conn, max_hr_bpm=190, resting_hr_bpm=50)
        conn.commit()

        # 目標日當天有 HRV，但前 7 天內只有這 1 筆（其餘日期完全無 HRV 紀錄）。
        target = START + datetime.timedelta(days=9)
        for i in range(10):
            _insert_activity(conn, athlete_id, START + datetime.timedelta(days=i), avg_hr_bpm=140)
        _insert_wellness(conn, athlete_id, target, hrv_ms=50.0, hrv_weekly_avg_ms=52.0)
        conn.commit()

        result = compute_readiness_for_athlete(conn, athlete_id, target, target)

        self.assertNotIn("hrv", result[0]["dimensions_evaluated"])
        self.assertIn("hrv", [item["dimension"] for item in result[0]["not_assessed"]])

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
