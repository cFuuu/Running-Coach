"""vdot_engine 的單元測試——全部使用合成活動資料，不含任何真實個人資料。"""

import datetime
import unittest

from src.main.python.services.vdot_engine import select_candidate

REFERENCE_DATE = datetime.date(2026, 8, 19)


def _activity(
    days_ago: int,
    distance_category: str,
    is_max_effort: bool = True,
    label: str | None = None,
) -> dict:
    return {
        "date": REFERENCE_DATE - datetime.timedelta(days=days_ago),
        "distance_category": distance_category,
        "is_max_effort": is_max_effort,
        "label": label or f"{distance_category}-{days_ago}d",
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

    def test_non_max_effort_activity_is_not_a_candidate(self):
        """本輪（Ticket 1）範圍：非全力程度的活動直接排除，不做心率強度換算。"""
        activities = [
            _activity(days_ago=10, distance_category="10k", is_max_effort=False),
        ]
        result = select_candidate(activities, reference_date=REFERENCE_DATE)
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


if __name__ == "__main__":
    unittest.main()
