"""periodization_scheduler 的單元測試——全部使用合成設定資料，不含真實個人資料。"""

import datetime
import unittest

from src.main.python.services.periodization_scheduler import (
    PEAK_MAX_LONG_RUN_KM,
    STEP_BACK_RATIO_HIGH,
    STEP_BACK_RATIO_LOW,
    WEEKLY_VOLUME_INCREASE_CAP,
    _is_step_back_week,
    _split_weeks_into_phases,
    generate_schedule,
)

START_DATE = datetime.date(2026, 9, 1)

FAKE_PACE_ZONES = {
    "easy": {"fast_sec_per_km": 420.0, "slow_sec_per_km": 460.0},
    "marathon": {"fast_sec_per_km": 360.0, "slow_sec_per_km": 375.0},
    "tempo": {"fast_sec_per_km": 330.0, "slow_sec_per_km": 345.0},
    "interval": {"fast_sec_per_km": 300.0, "slow_sec_per_km": 315.0},
    "lsd": {"fast_sec_per_km": 420.0, "slow_sec_per_km": 460.0},
}


def _config(**overrides) -> dict:
    base = {
        "start_date": START_DATE,
        "total_weeks": 18,
        "days_per_week": 5,
        "pace_zones": FAKE_PACE_ZONES,
    }
    base.update(overrides)
    return base


class TestSplitWeeksIntoPhases(unittest.TestCase):
    def test_phase_counts_match_ratios_for_evenly_divisible_weeks(self):
        # 20 週：base=8, build=6, peak=4, taper=2（20*0.4/0.3/0.2/0.1）
        phases = _split_weeks_into_phases(20)
        self.assertEqual(phases.count("base"), 8)
        self.assertEqual(phases.count("build"), 6)
        self.assertEqual(phases.count("peak"), 4)
        self.assertEqual(phases.count("taper"), 2)

    def test_total_weeks_preserved_when_not_evenly_divisible(self):
        for total_weeks in range(12, 21):
            phases = _split_weeks_into_phases(total_weeks)
            self.assertEqual(len(phases), total_weeks)

    def test_taper_is_always_last(self):
        phases = _split_weeks_into_phases(18)
        # 找出最後一個非 taper 期別的位置，確認之後全是 taper。
        last_non_taper = max(i for i, p in enumerate(phases) if p != "taper")
        self.assertTrue(all(p == "taper" for p in phases[last_non_taper + 1 :]))

    def test_phases_appear_in_canonical_order(self):
        phases = _split_weeks_into_phases(18)
        seen_order = []
        for phase in phases:
            if not seen_order or seen_order[-1] != phase:
                seen_order.append(phase)
        self.assertEqual(seen_order, ["base", "build", "peak", "taper"])


class TestStepBackWeek(unittest.TestCase):
    def test_first_week_is_never_step_back(self):
        self.assertFalse(_is_step_back_week(0))

    def test_step_back_occurs_every_interval(self):
        # STEP_BACK_INTERVAL_WEEKS=3：0-indexed 第 2, 5, 8 週為減量週。
        self.assertTrue(_is_step_back_week(2))
        self.assertTrue(_is_step_back_week(5))
        self.assertTrue(_is_step_back_week(8))
        self.assertFalse(_is_step_back_week(1))
        self.assertFalse(_is_step_back_week(3))


class TestGenerateScheduleVolume(unittest.TestCase):
    def test_weekly_volume_never_exceeds_increase_cap(self):
        schedule = generate_schedule(_config(total_weeks=12, starting_weekly_km=20.0))
        weekly_totals = self._weekly_totals(schedule)

        for week_index in range(1, len(weekly_totals)):
            if _is_step_back_week(week_index):
                continue
            prev = weekly_totals[week_index - 1]
            curr = weekly_totals[week_index]
            self.assertLessEqual(curr, prev * (1 + WEEKLY_VOLUME_INCREASE_CAP) + 1e-6)

    def test_step_back_week_drops_to_expected_ratio_range(self):
        schedule = generate_schedule(_config(total_weeks=12, starting_weekly_km=20.0))
        weekly_totals = self._weekly_totals(schedule)

        for week_index in range(1, len(weekly_totals)):
            if not _is_step_back_week(week_index):
                continue
            prev = weekly_totals[week_index - 1]
            curr = weekly_totals[week_index]
            ratio = curr / prev
            self.assertGreaterEqual(ratio, STEP_BACK_RATIO_LOW - 1e-6)
            self.assertLessEqual(ratio, STEP_BACK_RATIO_HIGH + 1e-6)

    def test_peak_phase_long_run_never_exceeds_cap(self):
        schedule = generate_schedule(
            _config(total_weeks=18, days_per_week=5, starting_weekly_km=30.0)
        )
        peak_lsd_distances = [
            day["target_distance_km"]
            for day in schedule
            if day["phase"] == "peak" and day["workout_type"] == "lsd"
        ]
        self.assertTrue(peak_lsd_distances)  # 確保真的有 peak 期 LSD 可供檢查
        for distance in peak_lsd_distances:
            self.assertLessEqual(distance, PEAK_MAX_LONG_RUN_KM)

    @staticmethod
    def _weekly_totals(schedule: list[dict]) -> list[float]:
        totals: dict[int, float] = {}
        for day in schedule:
            week_index = (day["date"] - START_DATE).days // 7
            totals[week_index] = totals.get(week_index, 0.0) + day["target_distance_km"]
        return [totals[i] for i in sorted(totals)]


class TestGenerateScheduleOutputShape(unittest.TestCase):
    def test_each_day_has_required_fields(self):
        schedule = generate_schedule(_config(total_weeks=4))
        for day in schedule:
            self.assertIn("date", day)
            self.assertIn("phase", day)
            self.assertIn("workout_type", day)
            self.assertIn("target_distance_km", day)
            self.assertIn("pace_zone", day)

    def test_total_days_equals_weeks_times_seven(self):
        schedule = generate_schedule(_config(total_weeks=4))
        self.assertEqual(len(schedule), 4 * 7)

    def test_rest_days_have_zero_distance_and_no_pace_zone(self):
        schedule = generate_schedule(_config(total_weeks=4, days_per_week=5))
        rest_days = [day for day in schedule if day["workout_type"] == "rest"]
        self.assertTrue(rest_days)
        for day in rest_days:
            self.assertEqual(day["target_distance_km"], 0.0)
            self.assertIsNone(day["pace_zone"])

    def test_training_days_have_pace_zone_populated(self):
        schedule = generate_schedule(_config(total_weeks=4, days_per_week=5))
        training_days = [day for day in schedule if day["workout_type"] != "rest"]
        for day in training_days:
            self.assertIsNotNone(day["pace_zone"])

    def test_unsupported_days_per_week_raises(self):
        with self.assertRaises(ValueError):
            generate_schedule(_config(days_per_week=3))

    def test_dates_are_contiguous_from_start_date(self):
        schedule = generate_schedule(_config(total_weeks=2))
        dates = [day["date"] for day in schedule]
        self.assertEqual(dates[0], START_DATE)
        self.assertEqual(dates[-1], START_DATE + datetime.timedelta(days=13))


if __name__ == "__main__":
    unittest.main()
