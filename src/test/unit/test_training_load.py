"""training_load 的單元測試——全部使用合成活動資料，不含任何真實個人資料。"""

import datetime
import unittest

from src.main.python.services.training_load import (
    compute_activity_load,
    compute_daily_loads,
    compute_hrr_intensity_pct,
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


class TestComputeActivityLoad(unittest.TestCase):
    def test_running_with_hr_uses_hrr_intensity(self):
        activity = _running_activity(datetime.date(2026, 8, 1), duration_sec=1800, avg_hr_bpm=140)
        result = compute_activity_load(activity, max_hr_bpm=MAX_HR, resting_hr_bpm=RESTING_HR)
        self.assertIsNotNone(result)
        self.assertFalse(result["uncertain"])
        expected_intensity = 90 / 140
        self.assertAlmostEqual(result["intensity_pct"], expected_intensity, places=6)
        self.assertAlmostEqual(result["load"], 1800 * expected_intensity, places=4)

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
        self.assertAlmostEqual(result["load"], 3600 * 0.5, places=4)

    def test_zero_duration_is_excluded(self):
        activity = _running_activity(datetime.date(2026, 8, 1), duration_sec=0, avg_hr_bpm=140)
        result = compute_activity_load(activity, max_hr_bpm=MAX_HR, resting_hr_bpm=RESTING_HR)
        self.assertIsNone(result)

    def test_unknown_activity_type_is_excluded(self):
        activity = {
            "date": datetime.date(2026, 8, 1),
            "activity_type": "cycling",
            "duration_sec": 1800,
            "avg_hr_bpm": 140,
        }
        result = compute_activity_load(activity, max_hr_bpm=MAX_HR, resting_hr_bpm=RESTING_HR)
        self.assertIsNone(result)


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


if __name__ == "__main__":
    unittest.main()
