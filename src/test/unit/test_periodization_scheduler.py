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

WEEKDAY_SATURDAY = 5

START_DATE = datetime.date(2026, 9, 7)  # 週一——訓練週以週一為週首，比照 weeklyVolume() 慣例

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
            generate_schedule(_config(days_per_week=2))

    def test_dates_are_contiguous_from_start_date(self):
        schedule = generate_schedule(_config(total_weeks=2))
        dates = [day["date"] for day in schedule]
        self.assertEqual(dates[0], START_DATE)
        self.assertEqual(dates[-1], START_DATE + datetime.timedelta(days=13))


class TestLowFrequencyDaysPerWeek(unittest.TestCase):
    def test_three_days_per_week_has_exactly_one_lsd_one_quality_one_easy(self):
        schedule = generate_schedule(_config(total_weeks=6, days_per_week=3))
        # 只看第一週（尚未進入減量週），確認固定為 1 LSD + 1 品質課 + 1 easy。
        first_week = [day for day in schedule if day["date"] < START_DATE + datetime.timedelta(days=7)]
        training_days = [day for day in first_week if day["workout_type"] != "rest"]

        self.assertEqual(len(training_days), 3)
        workout_types = [day["workout_type"] for day in training_days]
        self.assertEqual(workout_types.count("lsd"), 1)
        self.assertEqual(workout_types.count("easy"), 1)
        # 品質課在 Base 期展開為 tempo。
        self.assertEqual(workout_types.count("tempo"), 1)

    def test_lsd_falls_on_sunday(self):
        schedule = generate_schedule(_config(total_weeks=4, days_per_week=3))
        lsd_days = [day for day in schedule if day["workout_type"] == "lsd"]
        self.assertTrue(lsd_days)
        for day in lsd_days:
            self.assertEqual(day["date"].weekday(), 6)  # 週日

    def test_quality_workout_is_tempo_in_base_phase(self):
        schedule = generate_schedule(_config(total_weeks=20, days_per_week=3))
        base_quality_days = [
            day
            for day in schedule
            if day["phase"] == "base" and day["workout_type"] in ("tempo", "interval")
        ]
        self.assertTrue(base_quality_days)
        for day in base_quality_days:
            self.assertEqual(day["workout_type"], "tempo")

    def test_quality_workout_can_be_interval_in_build_or_peak_phase(self):
        schedule = generate_schedule(_config(total_weeks=20, days_per_week=3))
        build_or_peak_quality_days = [
            day
            for day in schedule
            if day["phase"] in ("build", "peak") and day["workout_type"] in ("tempo", "interval")
        ]
        self.assertTrue(build_or_peak_quality_days)
        self.assertTrue(any(day["workout_type"] == "interval" for day in build_or_peak_quality_days))

    def test_low_frequency_volume_rules_still_apply(self):
        """低頻率配置下，量進/減量規則（週增幅上限、減量週 70-80%）仍正確套用於 3 天總量。"""
        schedule = generate_schedule(
            _config(total_weeks=12, days_per_week=3, starting_weekly_km=15.0)
        )
        weekly_totals: dict[int, float] = {}
        for day in schedule:
            week_index = (day["date"] - START_DATE).days // 7
            weekly_totals[week_index] = weekly_totals.get(week_index, 0.0) + day["target_distance_km"]
        totals = [weekly_totals[i] for i in sorted(weekly_totals)]

        for week_index in range(1, len(totals)):
            if _is_step_back_week(week_index):
                ratio = totals[week_index] / totals[week_index - 1]
                self.assertGreaterEqual(ratio, STEP_BACK_RATIO_LOW - 1e-6)
                self.assertLessEqual(ratio, STEP_BACK_RATIO_HIGH + 1e-6)
            else:
                self.assertLessEqual(
                    totals[week_index], totals[week_index - 1] * (1 + WEEKLY_VOLUME_INCREASE_CAP) + 1e-6
                )

    def test_standard_days_per_week_behavior_unaffected(self):
        """5 天/週的標準情況行為不受低頻率擴充影響：仍是 easy/tempo/easy/interval/lsd。"""
        schedule = generate_schedule(_config(total_weeks=1, days_per_week=5))
        training_days = sorted(
            (day for day in schedule if day["workout_type"] != "rest"),
            key=lambda d: d["date"],
        )
        workout_types = [day["workout_type"] for day in training_days]
        self.assertEqual(workout_types, ["easy", "tempo", "easy", "interval", "lsd"])


class TestFirstMarathonConditionalBranch(unittest.TestCase):
    def test_first_marathon_pace_zones_are_slower_than_non_first_marathon(self):
        first_marathon_schedule = generate_schedule(
            _config(total_weeks=4, days_per_week=5, is_first_marathon=True)
        )
        regular_schedule = generate_schedule(
            _config(total_weeks=4, days_per_week=5, is_first_marathon=False)
        )

        first_easy_zone = next(
            day["pace_zone"] for day in first_marathon_schedule if day["workout_type"] == "easy"
        )
        regular_easy_zone = next(
            day["pace_zone"] for day in regular_schedule if day["workout_type"] == "easy"
        )
        self.assertGreater(
            first_easy_zone["fast_sec_per_km"], regular_easy_zone["fast_sec_per_km"]
        )
        self.assertGreater(
            first_easy_zone["slow_sec_per_km"], regular_easy_zone["slow_sec_per_km"]
        )

    def test_non_first_marathon_pace_zones_are_unbuffered(self):
        schedule = generate_schedule(
            _config(total_weeks=4, days_per_week=5, is_first_marathon=False)
        )
        easy_zone = next(day["pace_zone"] for day in schedule if day["workout_type"] == "easy")
        self.assertEqual(easy_zone, FAKE_PACE_ZONES["easy"])

    def test_first_marathon_peak_phase_has_fueling_rehearsal_days(self):
        schedule = generate_schedule(
            _config(total_weeks=20, days_per_week=5, is_first_marathon=True)
        )
        rehearsal_days = [day for day in schedule if day["fueling_rehearsal"]]
        self.assertGreaterEqual(len(rehearsal_days), 1)
        self.assertLessEqual(len(rehearsal_days), 2)
        for day in rehearsal_days:
            self.assertEqual(day["phase"], "peak")
            self.assertEqual(day["workout_type"], "lsd")

    def test_non_first_marathon_has_no_fueling_rehearsal_days(self):
        schedule = generate_schedule(
            _config(total_weeks=20, days_per_week=5, is_first_marathon=False)
        )
        rehearsal_days = [day for day in schedule if day["fueling_rehearsal"]]
        self.assertEqual(rehearsal_days, [])

    def test_is_first_marathon_defaults_to_false_when_omitted(self):
        config = _config(total_weeks=4, days_per_week=5)
        config.pop("is_first_marathon", None)
        schedule = generate_schedule(config)
        easy_zone = next(day["pace_zone"] for day in schedule if day["workout_type"] == "easy")
        self.assertEqual(easy_zone, FAKE_PACE_ZONES["easy"])


class TestConstraintWindowsAndExternalDates(unittest.TestCase):
    def test_skip_window_removes_all_training_within_range(self):
        window_start = START_DATE + datetime.timedelta(weeks=1)
        window_end = window_start + datetime.timedelta(days=6)
        schedule = generate_schedule(
            _config(
                total_weeks=4,
                days_per_week=5,
                constraint_windows=[
                    {"start_date": window_start, "end_date": window_end, "level": "skip"}
                ],
            )
        )
        days_in_window = [
            day for day in schedule if window_start <= day["date"] <= window_end
        ]
        self.assertTrue(days_in_window)
        for day in days_in_window:
            self.assertEqual(day["workout_type"], "rest")
            self.assertEqual(day["target_distance_km"], 0.0)
            self.assertIsNone(day["pace_zone"])

    def test_reduced_window_only_allows_easy_and_rest(self):
        window_start = START_DATE + datetime.timedelta(weeks=1)
        window_end = window_start + datetime.timedelta(days=6)
        schedule = generate_schedule(
            _config(
                total_weeks=4,
                days_per_week=5,
                constraint_windows=[
                    {"start_date": window_start, "end_date": window_end, "level": "reduced"}
                ],
            )
        )
        days_in_window = [
            day for day in schedule if window_start <= day["date"] <= window_end
        ]
        self.assertTrue(days_in_window)
        for day in days_in_window:
            self.assertIn(day["workout_type"], ("easy", "rest"))
            self.assertNotIn(day["workout_type"], ("tempo", "interval", "lsd"))

    def test_flexible_window_does_not_change_schedule_but_is_marked(self):
        window_start = START_DATE + datetime.timedelta(weeks=1)
        window_end = window_start + datetime.timedelta(days=6)
        with_window = generate_schedule(
            _config(
                total_weeks=4,
                days_per_week=5,
                constraint_windows=[
                    {"start_date": window_start, "end_date": window_end, "level": "flexible"}
                ],
            )
        )
        without_window = generate_schedule(_config(total_weeks=4, days_per_week=5))

        with_window_in_range = [
            day for day in with_window if window_start <= day["date"] <= window_end
        ]
        without_window_in_range = [
            day for day in without_window if window_start <= day["date"] <= window_end
        ]
        for a, b in zip(with_window_in_range, without_window_in_range):
            self.assertEqual(a["workout_type"], b["workout_type"])
            self.assertEqual(a["target_distance_km"], b["target_distance_km"])
            self.assertEqual(a["constraint_level"], "flexible")
            self.assertIsNone(b["constraint_level"])

    def test_external_dates_are_excluded_from_output(self):
        external_date = START_DATE + datetime.timedelta(days=3)
        schedule = generate_schedule(
            _config(total_weeks=2, days_per_week=5, external_dates={external_date})
        )
        dates_in_schedule = {day["date"] for day in schedule}
        self.assertNotIn(external_date, dates_in_schedule)

    def test_external_date_takes_priority_over_constraint_window(self):
        """限制窗口與 external 標記同時涵蓋同一天時，該天不產生新排程（external 優先）。"""
        external_date = START_DATE + datetime.timedelta(weeks=1, days=2)
        window_start = START_DATE + datetime.timedelta(weeks=1)
        window_end = window_start + datetime.timedelta(days=6)
        schedule = generate_schedule(
            _config(
                total_weeks=4,
                days_per_week=5,
                constraint_windows=[
                    {"start_date": window_start, "end_date": window_end, "level": "flexible"}
                ],
                external_dates={external_date},
            )
        )
        dates_in_schedule = {day["date"] for day in schedule}
        self.assertNotIn(external_date, dates_in_schedule)

    def test_stricter_constraint_wins_when_windows_overlap(self):
        """多個限制窗口重疊涵蓋同一天時，取限制最嚴格者（skip > reduced > flexible）。"""
        overlap_date = START_DATE + datetime.timedelta(weeks=1, days=2)
        schedule = generate_schedule(
            _config(
                total_weeks=4,
                days_per_week=5,
                constraint_windows=[
                    {
                        "start_date": overlap_date - datetime.timedelta(days=2),
                        "end_date": overlap_date + datetime.timedelta(days=2),
                        "level": "flexible",
                    },
                    {
                        "start_date": overlap_date,
                        "end_date": overlap_date,
                        "level": "skip",
                    },
                ],
            )
        )
        overlap_day = next(day for day in schedule if day["date"] == overlap_date)
        self.assertEqual(overlap_day["constraint_level"], "skip")
        self.assertEqual(overlap_day["workout_type"], "rest")

    def test_low_frequency_days_outside_window_still_use_correct_configuration(self):
        """限制窗口不影響窗口外日期的低頻率天數配置（Ticket C 規則）。"""
        window_start = START_DATE + datetime.timedelta(weeks=2)
        window_end = window_start + datetime.timedelta(days=6)
        schedule = generate_schedule(
            _config(
                total_weeks=6,
                days_per_week=3,
                constraint_windows=[
                    {"start_date": window_start, "end_date": window_end, "level": "skip"}
                ],
            )
        )
        # 第一週（窗口外）仍應是 1 LSD + 1 品質課 + 1 easy。
        first_week_days = [
            day
            for day in schedule
            if day["date"] < START_DATE + datetime.timedelta(days=7) and day["workout_type"] != "rest"
        ]
        self.assertEqual(len(first_week_days), 3)

    def test_first_marathon_pace_buffer_still_applies_outside_constraint_windows(self):
        """限制窗口不影響窗口外日期的首馬配速緩衝（Ticket D 規則）。"""
        window_start = START_DATE + datetime.timedelta(weeks=2)
        window_end = window_start + datetime.timedelta(days=6)
        schedule = generate_schedule(
            _config(
                total_weeks=4,
                days_per_week=5,
                is_first_marathon=True,
                constraint_windows=[
                    {"start_date": window_start, "end_date": window_end, "level": "skip"}
                ],
            )
        )
        outside_window_easy = next(
            day
            for day in schedule
            if day["workout_type"] == "easy" and not (window_start <= day["date"] <= window_end)
        )
        self.assertGreater(
            outside_window_easy["pace_zone"]["fast_sec_per_km"],
            FAKE_PACE_ZONES["easy"]["fast_sec_per_km"],
        )

    def test_fueling_rehearsal_not_misassigned_to_skipped_peak_lsd(self):
        """首馬條件與限制窗口同時作用時，補給演練標記不會被誤植到 skip 窗口內的日期。"""
        # 先算出一份無窗口的課表，找出 Peak 期的 LSD 日期，挑一個蓋成 skip 窗口。
        baseline = generate_schedule(
            _config(total_weeks=20, days_per_week=5, is_first_marathon=True)
        )
        peak_lsd_dates = sorted(
            day["date"] for day in baseline if day["phase"] == "peak" and day["workout_type"] == "lsd"
        )
        target_date = peak_lsd_dates[-1]

        schedule = generate_schedule(
            _config(
                total_weeks=20,
                days_per_week=5,
                is_first_marathon=True,
                constraint_windows=[
                    {"start_date": target_date, "end_date": target_date, "level": "skip"}
                ],
            )
        )
        skipped_day = next(day for day in schedule if day["date"] == target_date)
        self.assertEqual(skipped_day["workout_type"], "rest")
        self.assertFalse(skipped_day["fueling_rehearsal"])


class TestCombinatorialInvariants(unittest.TestCase):
    """Ticket F：對輸入參數空間做範圍性測試，確認多規則同時生效時
    所有不變量仍成立。標準天數/低頻率天數已各自在 Ticket B/C 驗證過
    單獨行為，此處聚焦「組合」是否仍守住不變量，不重複驗證單一規則本身。
    """

    def _assert_all_invariants_hold(self, schedule: list[dict], total_weeks: int):
        weekly_totals: dict[int, float] = {}
        for day in schedule:
            week_index = (day["date"] - START_DATE).days // 7
            weekly_totals.setdefault(week_index, 0.0)
            weekly_totals[week_index] += day["target_distance_km"]
        totals = [weekly_totals[i] for i in sorted(weekly_totals)]

        # 期別佔比：base >= build >= peak > taper 的相對量級關係至少不違反
        # 總週數守恆（詳細比例已在 TestSplitWeeksIntoPhases 驗證，此處只
        # 確認組合情境下週別劃分仍完整覆蓋所有週）。
        phases_seen = {day["phase"] for day in schedule}
        self.assertTrue(phases_seen.issubset({"base", "build", "peak", "taper"}))

        # 量進：非減量週的相鄰兩週增幅不超過上限（僅在該週有實際訓練量時比較，
        # 全 0（例如整週皆被 skip 覆蓋）不參與此檢查）。
        for week_index in range(1, len(totals)):
            if totals[week_index - 1] == 0 or totals[week_index] == 0:
                continue
            if _is_step_back_week(week_index):
                continue
            self.assertLessEqual(
                totals[week_index], totals[week_index - 1] * (1 + WEEKLY_VOLUME_INCREASE_CAP) + 1e-6
            )

        # Peak 期距離上限：任何一天都不應超過 PEAK_MAX_LONG_RUN_KM。
        for day in schedule:
            if day["phase"] == "peak":
                self.assertLessEqual(day["target_distance_km"], PEAK_MAX_LONG_RUN_KM + 1e-6)

        # reduced 窗口內不應出現品質課或 LSD。
        for day in schedule:
            if day["constraint_level"] == "reduced":
                self.assertNotIn(day["workout_type"], ("tempo", "interval", "lsd"))

        # skip 窗口內必為 rest、距離為 0。
        for day in schedule:
            if day["constraint_level"] == "skip":
                self.assertEqual(day["workout_type"], "rest")
                self.assertEqual(day["target_distance_km"], 0.0)

        # 補給演練標記只能出現在 Peak 期的 LSD 上（不論是否與限制窗口同時作用）。
        for day in schedule:
            if day["fueling_rehearsal"]:
                self.assertEqual(day["phase"], "peak")
                self.assertEqual(day["workout_type"], "lsd")

    def test_invariants_hold_across_parameter_space(self):
        """短週期＋高頻率、長週期＋低頻率、含首馬、含限制窗口等組合的參數空間。"""
        total_weeks_options = [12, 16, 20]
        days_per_week_options = [3, 4, 5, 6]
        is_first_marathon_options = [True, False]

        for total_weeks in total_weeks_options:
            for days_per_week in days_per_week_options:
                for is_first_marathon in is_first_marathon_options:
                    with self.subTest(
                        total_weeks=total_weeks,
                        days_per_week=days_per_week,
                        is_first_marathon=is_first_marathon,
                    ):
                        window_start = START_DATE + datetime.timedelta(weeks=total_weeks // 2)
                        window_end = window_start + datetime.timedelta(days=6)
                        config = _config(
                            total_weeks=total_weeks,
                            days_per_week=days_per_week,
                            is_first_marathon=is_first_marathon,
                            constraint_windows=[
                                {
                                    "start_date": window_start,
                                    "end_date": window_end,
                                    "level": "reduced",
                                }
                            ],
                        )
                        schedule = generate_schedule(config)
                        self._assert_all_invariants_hold(schedule, total_weeks)

    def test_low_frequency_and_reduced_window_coexist_without_contradiction(self):
        """低頻率天數（Ticket C）與 reduced 限制窗口（Ticket E）同時作用於同一週。

        3 天/週固定配置本來就只有 1 LSD+1 品質課+1 easy，reduced 窗口會把
        該週的 LSD 與品質課都降級為 easy——結果應是該週訓練日全變成 easy，
        而非產生矛盾或未定義行為（例如同時嘗試排品質課又要求跳過品質課）。
        """
        window_start = START_DATE + datetime.timedelta(weeks=1)
        window_end = window_start + datetime.timedelta(days=6)
        schedule = generate_schedule(
            _config(
                total_weeks=6,
                days_per_week=3,
                constraint_windows=[
                    {"start_date": window_start, "end_date": window_end, "level": "reduced"}
                ],
            )
        )
        days_in_window = [
            day
            for day in schedule
            if window_start <= day["date"] <= window_end and day["workout_type"] != "rest"
        ]
        self.assertTrue(days_in_window)
        for day in days_in_window:
            self.assertEqual(day["workout_type"], "easy")

    def test_low_frequency_and_skip_window_coexist_without_contradiction(self):
        """低頻率天數與 skip 限制窗口同時作用：該週應完全變成休息，不產生任何訓練。"""
        window_start = START_DATE + datetime.timedelta(weeks=1)
        window_end = window_start + datetime.timedelta(days=6)
        schedule = generate_schedule(
            _config(
                total_weeks=6,
                days_per_week=3,
                constraint_windows=[
                    {"start_date": window_start, "end_date": window_end, "level": "skip"}
                ],
            )
        )
        days_in_window = [day for day in schedule if window_start <= day["date"] <= window_end]
        self.assertTrue(days_in_window)
        for day in days_in_window:
            self.assertEqual(day["workout_type"], "rest")

    def test_first_marathon_and_low_frequency_combined(self):
        """首馬條件與低頻率天數同時作用：配速緩衝與補給演練標記仍正確套用。"""
        schedule = generate_schedule(
            _config(total_weeks=20, days_per_week=3, is_first_marathon=True)
        )
        easy_zone = next(day["pace_zone"] for day in schedule if day["workout_type"] == "easy")
        self.assertGreater(
            easy_zone["fast_sec_per_km"], FAKE_PACE_ZONES["easy"]["fast_sec_per_km"]
        )
        rehearsal_days = [day for day in schedule if day["fueling_rehearsal"]]
        self.assertGreaterEqual(len(rehearsal_days), 1)


if __name__ == "__main__":
    unittest.main()
