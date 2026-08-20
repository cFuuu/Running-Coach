"""vdot_engine 的單元測試——全部使用合成活動資料，不含任何真實個人資料。"""

import datetime
import unittest

from src.main.python.services.vdot_engine import (
    compute_pace_zones,
    estimate_max_effort_pace,
    estimate_vdot,
    estimate_vdot_and_paces,
    project_to_marathon_time_sec,
    select_candidate,
)

REFERENCE_DATE = datetime.date(2026, 8, 19)


def _activity(
    days_ago: int,
    distance_category: str,
    is_max_effort: bool = True,
    label: str | None = None,
    avg_hr_bpm: float | None = None,
    pace_sec_per_km: float | None = None,
) -> dict:
    return {
        "date": REFERENCE_DATE - datetime.timedelta(days=days_ago),
        "distance_category": distance_category,
        "is_max_effort": is_max_effort,
        "label": label or f"{distance_category}-{days_ago}d",
        "avg_hr_bpm": avg_hr_bpm,
        "pace_sec_per_km": pace_sec_per_km,
    }


class TestSelectCandidate(unittest.TestCase):
    def test_short_distance_beyond_90_days_is_excluded(self):
        activities = [_activity(days_ago=91, distance_category="10k")]
        result = select_candidate(activities, reference_date=REFERENCE_DATE)
        self.assertFalse(result["available"])

    def test_short_distance_within_90_days_is_selected(self):
        activities = [_activity(days_ago=89, distance_category="10k")]
        result = select_candidate(activities, reference_date=REFERENCE_DATE)
        self.assertTrue(result["available"])
        self.assertEqual(result["distance_category"], "10k")
        self.assertEqual(result["confidence"], "high")

    def test_long_distance_beyond_90_but_within_long_gate_is_selected_with_low_confidence(self):
        activities = [_activity(days_ago=150, distance_category="half_marathon")]
        result = select_candidate(activities, reference_date=REFERENCE_DATE)
        self.assertTrue(result["available"])
        self.assertEqual(result["distance_category"], "half_marathon")
        self.assertEqual(result["confidence"], "low")

    def test_long_distance_beyond_long_gate_is_excluded(self):
        activities = [_activity(days_ago=200, distance_category="marathon")]
        result = select_candidate(activities, reference_date=REFERENCE_DATE)
        self.assertFalse(result["available"])

    def test_recent_short_distance_loses_to_older_but_valid_long_distance(self):
        """較新的短距離候選 vs 較舊但通過門檻的長距離候選——代表性排序優先於新鮮度。"""
        activities = [
            _activity(days_ago=10, distance_category="10k", label="recent-10k"),
            _activity(days_ago=150, distance_category="half_marathon", label="older-half"),
        ]
        result = select_candidate(activities, reference_date=REFERENCE_DATE)
        self.assertTrue(result["available"])
        self.assertEqual(result["distance_category"], "half_marathon")
        self.assertEqual(result["activity"]["label"], "older-half")

    def test_same_rank_candidates_take_the_newest(self):
        activities = [
            _activity(days_ago=80, distance_category="half_marathon", label="half-80d"),
            _activity(days_ago=30, distance_category="half_marathon", label="half-30d"),
        ]
        result = select_candidate(activities, reference_date=REFERENCE_DATE)
        self.assertTrue(result["available"])
        self.assertEqual(result["activity"]["label"], "half-30d")

    def test_empty_candidate_list_returns_unavailable_with_reason(self):
        result = select_candidate([], reference_date=REFERENCE_DATE)
        self.assertFalse(result["available"])
        self.assertIn("reason", result)
        self.assertTrue(result["reason"])

    def test_all_candidates_fail_freshness_gate_returns_unavailable(self):
        activities = [
            _activity(days_ago=200, distance_category="5k"),
            _activity(days_ago=400, distance_category="marathon"),
        ]
        result = select_candidate(activities, reference_date=REFERENCE_DATE)
        self.assertFalse(result["available"])

    def test_non_max_effort_activity_without_hr_data_is_not_a_candidate(self):
        """沒有心率/配速資料的非全力活動無法換算，維持排除。"""
        activities = [
            _activity(days_ago=10, distance_category="10k", is_max_effort=False),
        ]
        result = select_candidate(activities, reference_date=REFERENCE_DATE, max_hr_bpm=190)
        self.assertFalse(result["available"])

    def test_unknown_distance_category_is_ignored(self):
        activities = [
            {
                "date": REFERENCE_DATE - datetime.timedelta(days=10),
                "distance_category": "some_unrecognized_category",
                "is_max_effort": True,
            }
        ]
        result = select_candidate(activities, reference_date=REFERENCE_DATE)
        self.assertFalse(result["available"])

    def test_default_reference_date_is_today_when_not_supplied(self):
        activities = [_activity(days_ago=1, distance_category="5k")]
        # reference_date 省略時應退回 datetime.date.today()，這筆活動昨天發生，
        # 無論今天實際日期為何都應通過新鮮度門檻。
        result = select_candidate(activities)
        self.assertTrue(result["available"])


class TestEstimateMaxEffortPace(unittest.TestCase):
    def test_at_max_hr_pace_is_unchanged(self):
        pace = estimate_max_effort_pace(pace_sec_per_km=300, avg_hr_bpm=190, max_hr_bpm=190)
        self.assertAlmostEqual(pace, 300, places=3)

    def test_below_max_hr_pace_speeds_up(self):
        # 65% HRmax，等效全力配速應明顯快於原配速。
        pace = estimate_max_effort_pace(pace_sec_per_km=400, avg_hr_bpm=123.5, max_hr_bpm=190)
        self.assertLess(pace, 400)

    def test_hr_above_max_hr_is_clamped(self):
        """avg_hr 因感測器雜訊略高於 max_hr 時，不應產生變慢的荒謬結果。"""
        pace = estimate_max_effort_pace(pace_sec_per_km=300, avg_hr_bpm=195, max_hr_bpm=190)
        self.assertAlmostEqual(pace, 300, places=3)


class TestSelectCandidateWithHrConversion(unittest.TestCase):
    def test_low_intensity_activity_is_converted_and_usable(self):
        """低強度活動（65% HRmax）換算後仍可用，且信賴度標記較低。"""
        activities = [
            _activity(
                days_ago=5,
                distance_category="10k",
                is_max_effort=False,
                avg_hr_bpm=123.5,
                pace_sec_per_km=400,
            )
        ]
        result = select_candidate(activities, reference_date=REFERENCE_DATE, max_hr_bpm=190)
        self.assertTrue(result["available"])
        self.assertEqual(result["confidence"], "low")
        self.assertTrue(result["hr_converted"])
        self.assertLess(result["effective_pace_sec_per_km"], 400)

    def test_activity_without_hr_data_remains_excluded(self):
        activities = [
            _activity(days_ago=5, distance_category="10k", is_max_effort=False)
        ]
        result = select_candidate(activities, reference_date=REFERENCE_DATE, max_hr_bpm=190)
        self.assertFalse(result["available"])

    def test_missing_max_hr_bpm_excludes_non_max_effort_candidates(self):
        """呼叫端未提供 max_hr_bpm 時，非全力候選無法換算，視為不合格。"""
        activities = [
            _activity(
                days_ago=5,
                distance_category="10k",
                is_max_effort=False,
                avg_hr_bpm=123.5,
                pace_sec_per_km=400,
            )
        ]
        result = select_candidate(activities, reference_date=REFERENCE_DATE, max_hr_bpm=None)
        self.assertFalse(result["available"])

    def test_high_intensity_non_max_effort_activity_is_not_downgraded(self):
        """心率已接近全力（>=85% HRmax）的候選，即使 is_max_effort=False 也不需要換算降級。"""
        activities = [
            _activity(
                days_ago=5,
                distance_category="10k",
                is_max_effort=False,
                avg_hr_bpm=170,  # 170/190 ≈ 89.5%，高於門檻
                pace_sec_per_km=330,
            )
        ]
        result = select_candidate(activities, reference_date=REFERENCE_DATE, max_hr_bpm=190)
        self.assertTrue(result["available"])
        self.assertEqual(result["confidence"], "high")
        self.assertFalse(result["hr_converted"])
        self.assertEqual(result["effective_pace_sec_per_km"], 330)

    def test_converted_low_intensity_candidate_still_wins_on_distance_representativeness(self):
        """換算後的長距離候選，即使信賴度較低，代表性排序仍優先於短距離全力候選。"""
        activities = [
            _activity(days_ago=5, distance_category="10k", is_max_effort=True, pace_sec_per_km=300),
            _activity(
                days_ago=10,
                distance_category="half_marathon",
                is_max_effort=False,
                avg_hr_bpm=123.5,
                pace_sec_per_km=400,
            ),
        ]
        result = select_candidate(activities, reference_date=REFERENCE_DATE, max_hr_bpm=190)
        self.assertTrue(result["available"])
        self.assertEqual(result["distance_category"], "half_marathon")
        self.assertTrue(result["hr_converted"])

    def test_max_effort_candidate_pace_is_untouched(self):
        """既有全力程度候選（is_max_effort=True）行為不變：不做任何換算。"""
        activities = [
            _activity(days_ago=5, distance_category="10k", is_max_effort=True, pace_sec_per_km=300)
        ]
        result = select_candidate(activities, reference_date=REFERENCE_DATE, max_hr_bpm=190)
        self.assertTrue(result["available"])
        self.assertFalse(result["hr_converted"])
        self.assertEqual(result["effective_pace_sec_per_km"], 300)
        self.assertEqual(result["confidence"], "high")


class TestProjectToMarathonTimeSec(unittest.TestCase):
    def test_marathon_pace_projects_to_itself(self):
        """距離已經是全馬時，Riegel 推算應等於原始完賽時間。"""
        pace = 360  # 6:00/km
        time_sec = project_to_marathon_time_sec("marathon", pace)
        self.assertAlmostEqual(time_sec, pace * 42.195, places=1)

    def test_half_marathon_projects_slower_than_its_own_pace_scaled(self):
        """半馬推算全馬時間，應比「半馬配速直接乘以全馬距離」慢（Riegel 指數 >1 的效果）。"""
        pace = 356  # 5:56/km，Fu 的半馬配速
        time_sec = project_to_marathon_time_sec("half_marathon", pace)
        naive_time_sec = pace * 42.195
        self.assertGreater(time_sec, naive_time_sec)

    def test_short_interval_has_no_standard_distance_and_raises(self):
        with self.assertRaises(ValueError):
            project_to_marathon_time_sec("short_interval", 240)


class TestEstimateVdot(unittest.TestCase):
    def test_known_marathon_time_produces_plausible_vdot(self):
        """4:24 全馬（Fu 的 Riegel 推算值，見 athlete_profile.md）應落在合理 VDOT 範圍。"""
        marathon_time_sec = 4 * 3600 + 24 * 60
        vdot = estimate_vdot(marathon_time_sec)
        self.assertGreater(vdot, 30)
        self.assertLess(vdot, 40)

    def test_faster_marathon_time_yields_higher_vdot(self):
        faster = estimate_vdot(3 * 3600)
        slower = estimate_vdot(4 * 3600)
        self.assertGreater(faster, slower)


class TestComputePaceZones(unittest.TestCase):
    def test_zones_are_ordered_fastest_to_slowest(self):
        zones = compute_pace_zones(vdot=35.0)
        # interval 最快、easy/lsd 最慢，配速數字（秒/km）方向相反。
        self.assertLess(
            zones["interval"]["fast_sec_per_km"], zones["tempo"]["fast_sec_per_km"]
        )
        self.assertLess(
            zones["tempo"]["fast_sec_per_km"], zones["marathon"]["fast_sec_per_km"]
        )
        self.assertLess(
            zones["marathon"]["fast_sec_per_km"], zones["easy"]["fast_sec_per_km"]
        )

    def test_lsd_matches_easy_zone(self):
        zones = compute_pace_zones(vdot=35.0)
        self.assertEqual(zones["lsd"], zones["easy"])

    def test_higher_vdot_yields_faster_zones(self):
        low_vdot_zones = compute_pace_zones(vdot=30.0)
        high_vdot_zones = compute_pace_zones(vdot=50.0)
        self.assertLess(
            high_vdot_zones["easy"]["fast_sec_per_km"],
            low_vdot_zones["easy"]["fast_sec_per_km"],
        )


class TestEstimateVdotAndPaces(unittest.TestCase):
    def test_full_pipeline_with_known_half_marathon_produces_expected_range(self):
        """端到端驗證：用近似 Fu 真實半馬成績（5:56/km）算出的 VDOT 應落在
        athlete_profile.md 記載的可信區間（約 34~35）附近。"""
        activities = [
            _activity(
                days_ago=30,
                distance_category="half_marathon",
                is_max_effort=True,
                pace_sec_per_km=356,
            )
        ]
        result = estimate_vdot_and_paces(
            activities,
            max_hr_bpm=190,
            max_hr_source="observed_from_data",
            reference_date=REFERENCE_DATE,
        )
        self.assertTrue(result["available"])
        self.assertGreater(result["vdot"], 32)
        self.assertLess(result["vdot"], 37)
        self.assertIn("easy", result["pace_zones"])
        self.assertIn("marathon", result["pace_zones"])
        self.assertIn("tempo", result["pace_zones"])
        self.assertIn("interval", result["pace_zones"])
        self.assertIn("lsd", result["pace_zones"])

    def test_max_hr_source_is_passed_through_unchanged(self):
        activities = [
            _activity(days_ago=5, distance_category="10k", is_max_effort=True, pace_sec_per_km=300)
        ]
        result = estimate_vdot_and_paces(
            activities,
            max_hr_bpm=190,
            max_hr_source="watch_display",
            reference_date=REFERENCE_DATE,
        )
        self.assertEqual(result["max_hr_source"], "watch_display")

    def test_source_candidate_summary_matches_select_candidate_output(self):
        activities = [
            _activity(
                days_ago=5,
                distance_category="10k",
                is_max_effort=False,
                avg_hr_bpm=123.5,
                pace_sec_per_km=400,
            )
        ]
        result = estimate_vdot_and_paces(
            activities, max_hr_bpm=190, reference_date=REFERENCE_DATE
        )
        self.assertTrue(result["available"])
        self.assertTrue(result["source_candidate"]["hr_converted"])
        self.assertEqual(result["source_candidate"]["confidence"], "low")

    def test_no_available_candidate_returns_unavailable_without_raising(self):
        result = estimate_vdot_and_paces([], max_hr_bpm=190, reference_date=REFERENCE_DATE)
        self.assertFalse(result["available"])
        self.assertIn("reason", result)

    def test_short_interval_only_candidate_returns_unavailable_without_raising(self):
        """short_interval 沒有標準距離，即使是唯一候選也不應拋例外，而是回傳無法推算。"""
        activities = [
            _activity(
                days_ago=5,
                distance_category="short_interval",
                is_max_effort=True,
                pace_sec_per_km=240,
            )
        ]
        result = estimate_vdot_and_paces(activities, max_hr_bpm=190, reference_date=REFERENCE_DATE)
        self.assertFalse(result["available"])
        self.assertIn("reason", result)


if __name__ == "__main__":
    unittest.main()
