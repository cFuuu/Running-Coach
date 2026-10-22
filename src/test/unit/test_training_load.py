"""training_load 的單元測試——全部使用合成活動資料，不含任何真實個人資料。"""

import datetime
import math
import unittest

from src.main.python.services.training_load import (
    NON_RUNNING_MIN_LOAD_DURATION_SEC,
    compute_activity_load,
    compute_daily_loads,
    compute_hrr_intensity_pct,
    compute_training_load_series,
    compute_trimp,
)

MAX_HR = 190.0
RESTING_HR = 50.0
EASY_PACE_FAST_SEC_PER_KM = 330.0  # 5:30/km


def _running_activity(
    date: datetime.date,
    duration_sec: float = 1800.0,
    avg_hr_bpm: float | None = None,
    avg_pace_sec_per_km: float | None = None,
) -> dict:
    return {
        "date": date,
        "activity_type": "running",
        "duration_sec": duration_sec,
        "avg_hr_bpm": avg_hr_bpm,
        "avg_pace_sec_per_km": avg_pace_sec_per_km,
    }


def _strength_activity(
    date: datetime.date,
    duration_sec: float = 3600.0,
    avg_hr_bpm: float | None = None,
) -> dict:
    return {
        "date": date,
        "activity_type": "strength_training",
        "duration_sec": duration_sec,
        "avg_hr_bpm": avg_hr_bpm,
    }


def _other_activity(
    date: datetime.date,
    duration_sec: float = 3600.0,
    avg_hr_bpm: float | None = None,
) -> dict:
    return {
        "date": date,
        "activity_type": "walking",
        "duration_sec": duration_sec,
        "avg_hr_bpm": avg_hr_bpm,
    }


class TestComputeHrrIntensityPct(unittest.TestCase):
    def test_mid_intensity(self):
        # (140 - 50) / (190 - 50) = 90/140 ≈ 0.643
        pct = compute_hrr_intensity_pct(avg_hr_bpm=140, max_hr_bpm=MAX_HR, resting_hr_bpm=RESTING_HR)
        self.assertAlmostEqual(pct, 90 / 140, places=6)

    def test_clamped_to_zero_when_below_resting_hr(self):
        pct = compute_hrr_intensity_pct(avg_hr_bpm=40, max_hr_bpm=MAX_HR, resting_hr_bpm=RESTING_HR)
        self.assertEqual(pct, 0.0)

    def test_clamped_to_one_when_at_or_above_max_hr(self):
        pct = compute_hrr_intensity_pct(avg_hr_bpm=200, max_hr_bpm=MAX_HR, resting_hr_bpm=RESTING_HR)
        self.assertEqual(pct, 1.0)

    def test_raises_when_hrr_not_positive(self):
        with self.assertRaises(ValueError):
            compute_hrr_intensity_pct(avg_hr_bpm=140, max_hr_bpm=50, resting_hr_bpm=50)


class TestComputeTrimp(unittest.TestCase):
    def test_matches_banister_male_formula(self):
        # 60 分鐘 × 0.7 × 0.64·e^(1.92×0.7)
        expected = 60 * 0.7 * 0.64 * math.exp(1.92 * 0.7)
        self.assertAlmostEqual(compute_trimp(3600, 0.7), expected, places=6)

    def test_one_hour_easy_and_threshold_land_on_known_scale(self):
        """一小時 easy（65% HRR）約 85、threshold（85% HRR）約 165。"""
        self.assertAlmostEqual(compute_trimp(3600, 0.65), 87.0, delta=2)
        self.assertAlmostEqual(compute_trimp(3600, 0.85), 167.0, delta=2)

    def test_weighting_is_superlinear_in_intensity(self):
        """強度加倍，負荷增幅大於兩倍（線性公式低估高強度課的修正點）。"""
        self.assertGreater(compute_trimp(3600, 0.8), 2 * compute_trimp(3600, 0.4))

    def test_zero_intensity_is_zero_load(self):
        self.assertEqual(compute_trimp(3600, 0.0), 0.0)


class TestComputeActivityLoad(unittest.TestCase):
    def test_running_with_hr_uses_hrr_intensity(self):
        activity = _running_activity(datetime.date(2026, 8, 1), duration_sec=1800, avg_hr_bpm=140)
        result = compute_activity_load(activity, max_hr_bpm=MAX_HR, resting_hr_bpm=RESTING_HR)
        self.assertIsNotNone(result)
        self.assertFalse(result["uncertain"])
        expected_intensity = 90 / 140
        self.assertAlmostEqual(result["intensity_pct"], expected_intensity, places=6)
        self.assertAlmostEqual(result["load"], compute_trimp(1800, expected_intensity), places=4)

    def test_running_without_hr_falls_back_to_pace_and_is_uncertain(self):
        activity = _running_activity(
            datetime.date(2026, 8, 1), duration_sec=1800, avg_pace_sec_per_km=300
        )
        result = compute_activity_load(
            activity, easy_pace_fast_sec_per_km=EASY_PACE_FAST_SEC_PER_KM
        )
        self.assertIsNotNone(result)
        self.assertTrue(result["uncertain"])

    def test_running_without_hr_or_pace_is_excluded(self):
        activity = _running_activity(datetime.date(2026, 8, 1))
        result = compute_activity_load(activity, max_hr_bpm=MAX_HR, resting_hr_bpm=RESTING_HR)
        self.assertIsNone(result)

    def test_running_without_hr_and_no_easy_pace_reference_is_excluded(self):
        """配速有值但呼叫端沒提供 VDOT easy 配速基準 → 無法換算，明確排除。"""
        activity = _running_activity(
            datetime.date(2026, 8, 1), avg_pace_sec_per_km=300
        )
        result = compute_activity_load(activity)
        self.assertIsNone(result)

    def test_strength_with_hr_uses_hrr_intensity(self):
        activity = _strength_activity(datetime.date(2026, 8, 1), duration_sec=3600, avg_hr_bpm=120)
        result = compute_activity_load(activity, max_hr_bpm=MAX_HR, resting_hr_bpm=RESTING_HR)
        self.assertIsNotNone(result)
        self.assertFalse(result["uncertain"])

    def test_strength_without_hr_uses_conservative_fallback_and_is_uncertain(self):
        activity = _strength_activity(datetime.date(2026, 8, 1), duration_sec=3600)
        result = compute_activity_load(activity)
        self.assertIsNotNone(result)
        self.assertTrue(result["uncertain"])
        self.assertEqual(result["intensity_pct"], 0.5)
        self.assertAlmostEqual(result["load"], compute_trimp(3600, 0.5), places=4)

    def test_zero_duration_is_excluded(self):
        activity = _running_activity(datetime.date(2026, 8, 1), duration_sec=0, avg_hr_bpm=140)
        result = compute_activity_load(activity, max_hr_bpm=MAX_HR, resting_hr_bpm=RESTING_HR)
        self.assertIsNone(result)

    def test_other_activity_over_threshold_with_hr_uses_hrr_intensity(self):
        activity = _other_activity(datetime.date(2026, 8, 1), duration_sec=3600, avg_hr_bpm=120)
        result = compute_activity_load(activity, max_hr_bpm=MAX_HR, resting_hr_bpm=RESTING_HR)
        self.assertIsNotNone(result)
        self.assertFalse(result["uncertain"])
        self.assertAlmostEqual(result["load"], compute_trimp(3600, 70 / 140), places=4)

    def test_other_activity_without_hr_is_excluded(self):
        activity = _other_activity(datetime.date(2026, 8, 1), duration_sec=3600)
        result = compute_activity_load(activity, max_hr_bpm=MAX_HR, resting_hr_bpm=RESTING_HR)
        self.assertIsNone(result)

    def test_non_running_at_threshold_is_excluded(self):
        """門檻是「超過」30 分鐘：剛好 30 分鐘的健走與重訓都不計入。"""
        at = NON_RUNNING_MIN_LOAD_DURATION_SEC
        for activity in (
            _other_activity(datetime.date(2026, 8, 1), duration_sec=at, avg_hr_bpm=120),
            _strength_activity(datetime.date(2026, 8, 1), duration_sec=at, avg_hr_bpm=120),
        ):
            result = compute_activity_load(activity, max_hr_bpm=MAX_HR, resting_hr_bpm=RESTING_HR)
            self.assertIsNone(result, activity["activity_type"])

    def test_non_running_just_over_threshold_counts(self):
        activity = _other_activity(
            datetime.date(2026, 8, 1), duration_sec=NON_RUNNING_MIN_LOAD_DURATION_SEC + 1, avg_hr_bpm=120
        )
        result = compute_activity_load(activity, max_hr_bpm=MAX_HR, resting_hr_bpm=RESTING_HR)
        self.assertIsNotNone(result)

    def test_short_running_is_not_subject_to_threshold(self):
        activity = _running_activity(datetime.date(2026, 8, 1), duration_sec=600, avg_hr_bpm=140)
        result = compute_activity_load(activity, max_hr_bpm=MAX_HR, resting_hr_bpm=RESTING_HR)
        self.assertIsNotNone(result)


class TestComputeDailyLoads(unittest.TestCase):
    def test_covers_full_date_range_with_zero_for_rest_days(self):
        start = datetime.date(2026, 8, 1)
        end = datetime.date(2026, 8, 5)
        result = compute_daily_loads([], start, end)
        self.assertEqual(len(result), 5)
        self.assertEqual([r["date"] for r in result], [start + datetime.timedelta(days=i) for i in range(5)])
        self.assertTrue(all(r["load"] == 0.0 for r in result))
        self.assertTrue(all(r["uncertain"] is False for r in result))

    def test_multiple_activities_same_day_sum_load(self):
        day = datetime.date(2026, 8, 1)
        activities = [
            _running_activity(day, duration_sec=1800, avg_hr_bpm=140),
            _strength_activity(day, duration_sec=3600, avg_hr_bpm=120),
        ]
        result = compute_daily_loads(
            activities, day, day, max_hr_bpm=MAX_HR, resting_hr_bpm=RESTING_HR
        )
        self.assertEqual(len(result), 1)
        running_only = compute_activity_load(
            activities[0], max_hr_bpm=MAX_HR, resting_hr_bpm=RESTING_HR
        )
        strength_only = compute_activity_load(
            activities[1], max_hr_bpm=MAX_HR, resting_hr_bpm=RESTING_HR
        )
        self.assertAlmostEqual(
            result[0]["load"], running_only["load"] + strength_only["load"], places=4
        )

    def test_excluded_activity_does_not_contribute_but_day_stays_present(self):
        day = datetime.date(2026, 8, 1)
        activities = [_running_activity(day)]  # 無心率也無配速 → 排除
        result = compute_daily_loads(activities, day, day)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["load"], 0.0)
        self.assertFalse(result[0]["uncertain"])

    def test_uncertain_flag_propagates_to_day(self):
        day = datetime.date(2026, 8, 1)
        activities = [_strength_activity(day, duration_sec=3600)]  # 無心率 → 保守估計
        result = compute_daily_loads(activities, day, day)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["uncertain"])

    def test_activity_outside_date_range_is_ignored(self):
        start = datetime.date(2026, 8, 1)
        end = datetime.date(2026, 8, 3)
        outside_activity = _running_activity(
            datetime.date(2026, 8, 10), avg_hr_bpm=140
        )
        result = compute_daily_loads(
            [outside_activity], start, end, max_hr_bpm=MAX_HR, resting_hr_bpm=RESTING_HR
        )
        self.assertEqual(len(result), 3)
        self.assertTrue(all(r["load"] == 0.0 for r in result))

    def test_start_after_end_raises(self):
        with self.assertRaises(ValueError):
            compute_daily_loads([], datetime.date(2026, 8, 5), datetime.date(2026, 8, 1))


def _daily_loads(loads: list[float], uncertain_indices: frozenset[int] = frozenset()) -> list[dict]:
    start = datetime.date(2026, 8, 1)
    return [
        {
            "date": start + datetime.timedelta(days=i),
            "load": load,
            "uncertain": i in uncertain_indices,
        }
        for i, load in enumerate(loads)
    ]


class TestComputeTrainingLoadSeries(unittest.TestCase):
    def test_empty_input_returns_empty_series(self):
        self.assertEqual(compute_training_load_series([]), [])

    def test_first_day_atl_and_ctl_equal_daily_load(self):
        daily = _daily_loads([1000.0])
        series = compute_training_load_series(daily)
        self.assertEqual(series[0]["atl"], 1000.0)
        self.assertEqual(series[0]["ctl"], 1000.0)

    def test_tsb_equals_ctl_minus_atl_every_day(self):
        daily = _daily_loads([1000.0, 800.0, 0.0, 1200.0, 500.0])
        series = compute_training_load_series(daily)
        for day in series:
            self.assertAlmostEqual(day["tsb"], day["ctl"] - day["atl"], places=9)

    def test_series_covers_full_input_range_without_gaps(self):
        daily = _daily_loads([500.0] * 10)
        series = compute_training_load_series(daily)
        self.assertEqual(len(series), 10)
        self.assertEqual([d["date"] for d in series], [d["date"] for d in daily])

    def test_sustained_high_load_atl_rises_faster_than_ctl(self):
        """穩定基線負荷後突然拉高強度，ATL 應比 CTL 更快、更多地反映這波增量
        （急性反應快於慢性累積），因此 ATL 應明顯高於 CTL。"""
        baseline = _daily_loads([500.0] * 30)
        spike = [
            {"date": baseline[-1]["date"] + datetime.timedelta(days=i + 1), "load": 1500.0, "uncertain": False}
            for i in range(14)
        ]
        series = compute_training_load_series(baseline + spike)
        last = series[-1]
        self.assertGreater(last["atl"], last["ctl"])

    def test_sustained_rest_atl_decays_faster_than_ctl(self):
        """先累積負荷，再連續多日休息（load=0），ATL 衰減速度快於 CTL。"""
        build_up = _daily_loads([1500.0] * 20)
        rest = [
            {"date": build_up[-1]["date"] + datetime.timedelta(days=i + 1), "load": 0.0, "uncertain": False}
            for i in range(14)
        ]
        series = compute_training_load_series(build_up + rest)

        atl_before_rest = series[len(build_up) - 1]["atl"]
        ctl_before_rest = series[len(build_up) - 1]["ctl"]
        atl_after_rest = series[-1]["atl"]
        ctl_after_rest = series[-1]["ctl"]

        atl_drop_ratio = (atl_before_rest - atl_after_rest) / atl_before_rest
        ctl_drop_ratio = (ctl_before_rest - ctl_after_rest) / ctl_before_rest
        self.assertGreater(atl_drop_ratio, ctl_drop_ratio)

    def test_uncertain_flag_is_passed_through(self):
        daily = _daily_loads([500.0, 500.0, 500.0], uncertain_indices=frozenset({1}))
        series = compute_training_load_series(daily)
        self.assertEqual([d["uncertain"] for d in series], [False, True, False])

    def test_daily_load_value_is_passed_through_unchanged(self):
        daily = _daily_loads([500.0, 700.0])
        series = compute_training_load_series(daily)
        self.assertEqual(series[0]["load"], 500.0)
        self.assertEqual(series[1]["load"], 700.0)

    def test_constant_load_converges_atl_and_ctl_toward_that_load(self):
        """長期固定負荷下，ATL/CTL 應收斂到接近該負荷值（EWMA 穩態特性）。"""
        daily = _daily_loads([1000.0] * 200)
        series = compute_training_load_series(daily)
        last = series[-1]
        self.assertAlmostEqual(last["atl"], 1000.0, delta=1.0)
        self.assertAlmostEqual(last["ctl"], 1000.0, delta=1.0)
        self.assertAlmostEqual(last["tsb"], 0.0, delta=1.0)


if __name__ == "__main__":
    unittest.main()
