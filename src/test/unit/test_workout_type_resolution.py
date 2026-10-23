"""workout_type_resolution 的單元測試——全部使用合成資料，不含真實個人資料。"""

import unittest

from src.main.python.models.db import get_connection
from src.main.python.services import fit_import_runner, training_plan_store
from src.main.python.services.workout_type_resolution import (
    plan_type_for_activity,
    resolve_for_dates,
    resolve_workout_type,
)

DAY = "2026-10-20"


class TestResolveWorkoutType(unittest.TestCase):
    def test_plan_wins_over_detected(self):
        self.assertEqual(resolve_workout_type("easy", "tempo"), ("tempo", "plan"))

    def test_no_plan_uses_detected(self):
        self.assertEqual(resolve_workout_type("easy", None), ("easy", "laps"))

    def test_race_plan_does_not_decide(self):
        """race 會進最佳成績與 VDOT，不由課表決定。"""
        self.assertEqual(resolve_workout_type("easy", "race"), ("easy", "laps"))

    def test_rest_and_other_plans_do_not_decide(self):
        for plan in ("rest", "strength", "cross_training"):
            self.assertEqual(resolve_workout_type("easy", plan), ("easy", "laps"))

    def test_nothing_known_returns_none(self):
        self.assertEqual(resolve_workout_type(None, None), (None, None))


class _DbCase(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.conn.execute(
            "INSERT INTO athlete_profile (name, updated_at) VALUES ('Test', '2026-01-01T00:00:00')"
        )

    def tearDown(self):
        self.conn.close()

    def _activity(self, started_at, workout_type="easy", source="auto", minutes=45,
                  warmup_for=None, activity_type="running"):
        return self.conn.execute(
            """
            INSERT INTO activities (athlete_id, activity_type, started_at, distance_km,
                duration_sec, avg_pace_sec_per_km, workout_type, workout_type_source,
                warmup_for_activity_id, source, fetched_at)
            VALUES (1, ?, ?, 8.0, ?, 350, ?, ?, ?, 'garmin_export', '2026-01-01T00:00:00')
            """,
            (activity_type, started_at, minutes * 60, workout_type, source, warmup_for),
        ).lastrowid

    def _plan(self, day, workout_type, source="external", active=1):
        return self.conn.execute(
            """
            INSERT INTO training_plan (athlete_id, planned_date, workout_type, plan_source,
                is_active, created_at)
            VALUES (1, ?, ?, ?, ?, '2026-01-01T00:00:00')
            """,
            (day, workout_type, source, active),
        ).lastrowid

    def _row(self, aid):
        return tuple(self.conn.execute(
            "SELECT workout_type, workout_type_basis, detected_workout_type FROM activities "
            "WHERE id = ?", (aid,)).fetchone())


class TestPlanTypeForActivity(_DbCase):
    def test_external_plan_matches_main_activity(self):
        main = self._activity(f"{DAY}T20:00:00")
        self._plan(DAY, "tempo")
        self.assertEqual(plan_type_for_activity(self.conn, main), "tempo")

    def test_generated_plan_is_ignored(self):
        main = self._activity(f"{DAY}T20:00:00")
        self._plan(DAY, "tempo", source="generated")
        self.assertIsNone(plan_type_for_activity(self.conn, main))

    def test_inactive_plan_is_ignored(self):
        main = self._activity(f"{DAY}T20:00:00")
        self._plan(DAY, "tempo", active=0)
        self.assertIsNone(plan_type_for_activity(self.conn, main))

    def test_warmup_does_not_take_the_plan_and_does_not_block_main(self):
        """★ 暖身是另存的獨立活動：不與主課爭同一份課表，主課照常對到。"""
        main = self._activity(f"{DAY}T20:08:00")
        warmup = self._activity(f"{DAY}T19:39:00", minutes=9, warmup_for=main)
        self._plan(DAY, "interval")
        self.assertEqual(plan_type_for_activity(self.conn, main), "interval")
        self.assertIsNone(plan_type_for_activity(self.conn, warmup))

    def test_two_real_runs_on_one_plan_is_ambiguous(self):
        a = self._activity(f"{DAY}T07:00:00")
        self._activity(f"{DAY}T19:00:00")
        self._plan(DAY, "tempo")
        self.assertIsNone(plan_type_for_activity(self.conn, a))


class TestResolveForDates(_DbCase):
    def test_plan_overrides_and_detection_is_kept(self):
        """★ 課表寫 tempo、分圈偵測為 easy：生效課種是 tempo，偵測結果另存。"""
        aid = self._activity(f"{DAY}T20:00:00", workout_type="easy")
        self._plan(DAY, "tempo")
        changes = resolve_for_dates(self.conn, 1, [DAY])
        self.assertEqual([(c["old"], c["new"], c["detected"]) for c in changes],
                         [("easy", "tempo", "easy")])
        self.assertEqual(self._row(aid), ("tempo", "plan", "easy"))

    def test_manual_is_never_overwritten(self):
        aid = self._activity(f"{DAY}T20:00:00", workout_type="easy", source="manual")
        self._plan(DAY, "tempo")
        self.assertEqual(resolve_for_dates(self.conn, 1, [DAY]), [])
        self.assertEqual(self._row(aid), ("easy", None, None))

    def test_removing_plan_falls_back_to_detection(self):
        aid = self._activity(f"{DAY}T20:00:00", workout_type="easy")
        plan = self._plan(DAY, "tempo")
        resolve_for_dates(self.conn, 1, [DAY])
        self.conn.execute("UPDATE training_plan SET is_active = 0 WHERE id = ?", (plan,))
        resolve_for_dates(self.conn, 1, [DAY])
        self.assertEqual(self._row(aid), ("easy", "laps", "easy"))

    def test_dates_not_listed_are_untouched(self):
        other = self._activity("2026-10-21T20:00:00", workout_type="easy")
        self._plan("2026-10-21", "tempo")
        resolve_for_dates(self.conn, 1, [DAY])
        self.assertEqual(self._row(other)[0], "easy")

    def test_dry_run_does_not_write(self):
        aid = self._activity(f"{DAY}T20:00:00", workout_type="easy")
        self._plan(DAY, "tempo")
        self.assertEqual(len(resolve_for_dates(self.conn, 1, [DAY], apply=False)), 1)
        self.assertEqual(self._row(aid), ("easy", None, None))


class TestHooks(_DbCase):
    def test_saving_external_workouts_resolves_existing_activities(self):
        """★ 活動先同步、課表後匯入：匯入時重新決定當天的生效課種。"""
        aid = self._activity(f"{DAY}T20:00:00", workout_type="easy")
        training_plan_store.save_external_workouts(
            self.conn, 1, [{"planned_date": DAY, "workout_type": "tempo"}])
        self.assertEqual(self._row(aid), ("tempo", "plan", "easy"))

    def test_replacing_plan_updates_resolution(self):
        aid = self._activity(f"{DAY}T20:00:00", workout_type="easy")
        training_plan_store.save_external_workouts(
            self.conn, 1, [{"planned_date": DAY, "workout_type": "tempo"}])
        training_plan_store.save_external_workouts(
            self.conn, 1, [{"planned_date": DAY, "workout_type": "rest"}])
        self.assertEqual(self._row(aid), ("easy", "laps", "easy"))

    def test_update_workout_type_applies_plan(self):
        aid = self._activity(f"{DAY}T20:00:00", workout_type=None)
        self._plan(DAY, "tempo")
        laps = [{"lap_index": i, "distance_km": 1.0, "duration_sec": 380, "pace_sec_per_km": 380,
                 "avg_hr_bpm": None, "max_hr_bpm": None} for i in range(1, 7)]
        self.assertEqual(fit_import_runner.update_workout_type(self.conn, aid, laps), "tempo")
        row = self._row(aid)
        self.assertEqual((row[0], row[1]), ("tempo", "plan"))
        self.assertEqual(row[2], "easy")  # 分圈偵測仍是 easy

    def test_update_workout_type_keeps_manual_but_stores_detection(self):
        aid = self._activity(f"{DAY}T20:00:00", workout_type="tempo", source="manual")
        self._plan(DAY, "interval")
        laps = [{"lap_index": i, "distance_km": 1.0, "duration_sec": 380, "pace_sec_per_km": 380,
                 "avg_hr_bpm": None, "max_hr_bpm": None} for i in range(1, 7)]
        self.assertEqual(fit_import_runner.update_workout_type(self.conn, aid, laps), "manual-kept")
        self.assertEqual(self._row(aid), ("tempo", None, "easy"))

    def test_reclassify_applies_plan_even_without_laps(self):
        aid = self._activity(f"{DAY}T20:00:00", workout_type="easy")
        self._plan(DAY, "tempo")
        changes = fit_import_runner.reclassify_stored_activities(self.conn, apply=True)
        self.assertEqual([(c["activity_id"], c["new"], c["basis"]) for c in changes],
                         [(aid, "tempo", "plan")])
        self.assertEqual(self._row(aid), ("tempo", "plan", "easy"))


if __name__ == "__main__":
    unittest.main()
