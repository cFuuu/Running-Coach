"""plan_reconciliation 的單元測試——全部使用合成資料，不含任何真實個人資料。"""

import unittest

from src.main.python.services.plan_reconciliation import (
    is_compatible,
    match_activity_to_plan,
)


def _plan(plan_id: int, workout_type: str, linked: int | None = None) -> dict:
    return {
        "id": plan_id,
        "workout_type": workout_type,
        "planned_distance_km": 10.0,
        "planned_pace_sec_per_km": 360,
        "notes": None,
        "linked_activity_id": linked,
    }


def _act(activity_id: int, activity_type: str) -> dict:
    return {"id": activity_id, "activity_type": activity_type}


class TestIsCompatible(unittest.TestCase):
    def test_running_workout_types_accept_running_activities(self):
        for workout_type in ("easy", "tempo", "interval", "lsd", "race"):
            for activity_type in (
                "running",
                "treadmill_running",
                "track_running",
                "trail_running",
            ):
                self.assertTrue(is_compatible(workout_type, activity_type))

    def test_running_workout_rejects_walking(self):
        # F2 的核心情境：走路不該被算成執行了跑步課表
        self.assertFalse(is_compatible("lsd", "walking"))
        self.assertFalse(is_compatible("tempo", "hiking"))

    def test_strength_workout_matches_only_strength(self):
        self.assertTrue(is_compatible("strength", "strength_training"))
        self.assertFalse(is_compatible("strength", "running"))

    def test_cross_training_excludes_running_and_strength(self):
        self.assertTrue(is_compatible("cross_training", "cycling"))
        self.assertTrue(is_compatible("cross_training", "lap_swimming"))
        self.assertFalse(is_compatible("cross_training", "running"))
        self.assertFalse(is_compatible("cross_training", "strength_training"))

    def test_rest_matches_nothing(self):
        # 休息日沒有「對應的活動」——當天有跑不代表執行了休息課表
        self.assertFalse(is_compatible("rest", "running"))
        self.assertFalse(is_compatible("rest", "walking"))

    def test_missing_values_are_not_compatible(self):
        self.assertFalse(is_compatible(None, "running"))
        self.assertFalse(is_compatible("easy", None))

    def test_unknown_workout_type_is_not_guessed(self):
        self.assertFalse(is_compatible("future_new_type", "running"))


class TestMatchActivityToPlan(unittest.TestCase):
    def test_linked_activity_id_wins_over_heuristic(self):
        activity = _act(7, "running")
        rows = [_plan(1, "easy"), _plan(2, "lsd", linked=7)]
        result = match_activity_to_plan(activity, rows, [activity])
        self.assertEqual(result["state"], "exact")
        self.assertEqual(result["method"], "linked_activity_id")
        self.assertEqual(result["plan"]["id"], 2)

    def test_single_compatible_plan_is_inferred(self):
        activity = _act(7, "running")
        result = match_activity_to_plan(activity, [_plan(1, "lsd")], [activity])
        self.assertEqual(result["state"], "inferred")
        self.assertEqual(result["method"], "date_and_sport")
        self.assertEqual(result["plan"]["id"], 1)

    def test_walking_does_not_match_running_plan(self):
        # F2 實例：同日 walking + running，走路那場不得配到跑步課表
        walk, run = _act(7, "walking"), _act(8, "running")
        result = match_activity_to_plan(walk, [_plan(1, "lsd")], [walk, run])
        self.assertEqual(result["state"], "none")
        self.assertEqual(result["reason"], "no_compatible_plan")
        self.assertIsNone(result["plan"])

    def test_walking_present_does_not_block_the_run(self):
        # 反面：走路存在不該讓跑步那場變成歧義
        walk, run = _act(7, "walking"), _act(8, "running")
        result = match_activity_to_plan(run, [_plan(1, "lsd")], [walk, run])
        self.assertEqual(result["state"], "inferred")
        self.assertEqual(result["plan"]["id"], 1)

    def test_two_runs_one_plan_is_ambiguous(self):
        first, second = _act(7, "running"), _act(8, "running")
        result = match_activity_to_plan(first, [_plan(1, "lsd")], [first, second])
        self.assertEqual(result["state"], "ambiguous")
        self.assertEqual(result["reason"], "multiple_activities_for_one_plan")
        self.assertIsNone(result["plan"])

    def test_two_compatible_plans_is_ambiguous(self):
        activity = _act(7, "running")
        rows = [_plan(1, "easy"), _plan(2, "tempo")]
        result = match_activity_to_plan(activity, rows, [activity])
        self.assertEqual(result["state"], "ambiguous")
        self.assertEqual(result["reason"], "multiple_plan_candidates")
        self.assertIsNone(result["plan"])

    def test_plan_linked_to_another_activity_is_not_stolen(self):
        activity = _act(7, "running")
        result = match_activity_to_plan(
            activity, [_plan(1, "lsd", linked=99)], [activity]
        )
        self.assertEqual(result["state"], "none")
        self.assertEqual(result["reason"], "no_compatible_plan")

    def test_no_plan_rows_at_all(self):
        activity = _act(7, "running")
        result = match_activity_to_plan(activity, [], [activity])
        self.assertEqual(result["state"], "none")
        self.assertIsNone(result["plan"])

    def test_rest_day_plan_never_matches(self):
        activity = _act(7, "running")
        result = match_activity_to_plan(activity, [_plan(1, "rest")], [activity])
        self.assertEqual(result["state"], "none")

    def test_strength_and_run_same_day_each_match_own_plan(self):
        run, lift = _act(7, "running"), _act(8, "strength_training")
        rows = [_plan(1, "easy"), _plan(2, "strength")]
        run_result = match_activity_to_plan(run, rows, [run, lift])
        lift_result = match_activity_to_plan(lift, rows, [run, lift])
        self.assertEqual(run_result["state"], "inferred")
        self.assertEqual(run_result["plan"]["id"], 1)
        self.assertEqual(lift_result["state"], "inferred")
        self.assertEqual(lift_result["plan"]["id"], 2)

    def test_result_never_carries_plan_when_not_matched(self):
        # 不變式：state 非 exact/inferred 時一律不得附帶課表內容
        activity = _act(7, "running")
        for rows, same_day in (
            ([], [activity]),
            ([_plan(1, "easy"), _plan(2, "tempo")], [activity]),
            ([_plan(1, "easy")], [activity, _act(8, "running")]),
        ):
            result = match_activity_to_plan(activity, rows, same_day)
            self.assertNotIn(result["state"], ("exact", "inferred"))
            self.assertIsNone(result["plan"])
            self.assertIsNotNone(result["reason"])


if __name__ == "__main__":
    unittest.main()
