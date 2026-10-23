"""training_plan_store 的單元測試——全部使用合成課表與臨時 SQLite，不含真實個人資料。"""

import datetime
import unittest

from src.main.python.models.db import get_connection
from src.main.python.services.training_plan_store import (
    PlanLinkNotFoundError,
    PlanLinkRejectedError,
    get_active_schedule,
    get_plan_history_for_date,
    save_external_workouts,
    save_schedule,
    set_plan_link,
)

START = datetime.date(2026, 8, 1)


_DEFAULT_PACE_ZONE = {"fast_sec_per_km": 300, "slow_sec_per_km": 330}


def _day(
    date: datetime.date,
    workout_type: str = "easy",
    target_distance_km: float = 8.0,
    pace_zone: dict | None | object = "__default__",
) -> dict:
    resolved_pace_zone = _DEFAULT_PACE_ZONE if pace_zone == "__default__" else pace_zone
    return {
        "date": date,
        "phase": "base",
        "workout_type": workout_type,
        "target_distance_km": target_distance_km,
        "pace_zone": resolved_pace_zone,
        "fueling_rehearsal": False,
        "constraint_level": None,
    }


def _build_athlete(conn) -> int:
    cursor = conn.execute(
        "INSERT INTO athlete_profile (name, updated_at) VALUES ('測試學員', '2026-01-01T00:00:00')"
    )
    return cursor.lastrowid


class TestSaveScheduleFirstWrite(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)

    def test_writes_correct_fields(self):
        schedule = [_day(START, workout_type="tempo", target_distance_km=10.0)]
        save_schedule(self.conn, self.athlete_id, schedule, created_at="2026-08-01T00:00:00")

        row = self.conn.execute("SELECT * FROM training_plan").fetchone()
        self.assertEqual(row["planned_date"], "2026-08-01")
        self.assertEqual(row["workout_type"], "tempo")
        self.assertEqual(row["planned_distance_km"], 10.0)
        self.assertEqual(row["planned_pace_sec_per_km"], 315)  # midpoint of 300/330
        self.assertEqual(row["plan_source"], "generated")

    def test_first_write_is_active_with_no_superseded_by(self):
        schedule = [_day(START)]
        save_schedule(self.conn, self.athlete_id, schedule)

        row = self.conn.execute("SELECT * FROM training_plan").fetchone()
        self.assertEqual(row["is_active"], 1)
        self.assertIsNone(row["superseded_by"])

    def test_rest_day_has_null_pace(self):
        schedule = [_day(START, workout_type="rest", target_distance_km=0.0, pace_zone=None)]
        save_schedule(self.conn, self.athlete_id, schedule)

        row = self.conn.execute("SELECT * FROM training_plan").fetchone()
        self.assertIsNone(row["planned_pace_sec_per_km"])

    def test_returns_new_row_ids_in_order(self):
        schedule = [_day(START), _day(START + datetime.timedelta(days=1))]
        ids = save_schedule(self.conn, self.athlete_id, schedule)
        self.assertEqual(len(ids), 2)
        rows = self.conn.execute("SELECT id, planned_date FROM training_plan ORDER BY id").fetchall()
        self.assertEqual([r["id"] for r in rows], ids)


class TestSaveScheduleSupersedes(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)

    def test_regenerating_same_date_supersedes_old_row(self):
        first = save_schedule(
            self.conn, self.athlete_id, [_day(START, workout_type="easy")],
            created_at="2026-08-01T00:00:00",
        )
        second = save_schedule(
            self.conn, self.athlete_id, [_day(START, workout_type="tempo")],
            created_at="2026-08-02T00:00:00",
        )

        old_row = self.conn.execute(
            "SELECT * FROM training_plan WHERE id = ?", (first[0],)
        ).fetchone()
        new_row = self.conn.execute(
            "SELECT * FROM training_plan WHERE id = ?", (second[0],)
        ).fetchone()

        self.assertEqual(old_row["is_active"], 0)
        self.assertEqual(old_row["superseded_by"], second[0])
        self.assertEqual(old_row["workout_type"], "easy")  # 舊列內容不被覆寫
        self.assertEqual(new_row["is_active"], 1)
        self.assertIsNone(new_row["superseded_by"])
        self.assertEqual(new_row["workout_type"], "tempo")

    def test_old_row_is_not_deleted(self):
        first = save_schedule(self.conn, self.athlete_id, [_day(START)], created_at="2026-08-01T00:00:00")
        save_schedule(self.conn, self.athlete_id, [_day(START)], created_at="2026-08-02T00:00:00")

        count = self.conn.execute("SELECT COUNT(*) AS c FROM training_plan").fetchone()["c"]
        self.assertEqual(count, 2)
        still_there = self.conn.execute(
            "SELECT id FROM training_plan WHERE id = ?", (first[0],)
        ).fetchone()
        self.assertIsNotNone(still_there)

    def test_different_dates_do_not_supersede_each_other(self):
        save_schedule(self.conn, self.athlete_id, [_day(START)], created_at="2026-08-01T00:00:00")
        save_schedule(
            self.conn, self.athlete_id, [_day(START + datetime.timedelta(days=1))],
            created_at="2026-08-02T00:00:00",
        )
        rows = self.conn.execute("SELECT is_active FROM training_plan").fetchall()
        self.assertTrue(all(r["is_active"] == 1 for r in rows))

    def test_external_plan_source_row_is_never_superseded(self):
        self.conn.execute(
            """
            INSERT INTO training_plan
                (athlete_id, planned_date, workout_type, plan_source, is_active, created_at)
            VALUES (?, ?, 'easy', 'external', 1, '2026-07-01T00:00:00')
            """,
            (self.athlete_id, START.isoformat()),
        )
        self.conn.commit()
        external_id = self.conn.execute(
            "SELECT id FROM training_plan WHERE plan_source = 'external'"
        ).fetchone()["id"]

        save_schedule(self.conn, self.athlete_id, [_day(START)], created_at="2026-08-01T00:00:00")

        external_row = self.conn.execute(
            "SELECT is_active, superseded_by FROM training_plan WHERE id = ?", (external_id,)
        ).fetchone()
        self.assertEqual(external_row["is_active"], 1)
        self.assertIsNone(external_row["superseded_by"])

    def test_different_athlete_same_date_does_not_supersede(self):
        conn = self.conn
        other_athlete_id = _build_athlete(conn)
        # SQLite 遊標插入後才 commit，_build_athlete 內未 commit，這裡先手動 commit 確保可查。
        conn.commit()

        save_schedule(conn, self.athlete_id, [_day(START)], created_at="2026-08-01T00:00:00")
        save_schedule(conn, other_athlete_id, [_day(START)], created_at="2026-08-01T00:00:00")

        rows = conn.execute("SELECT athlete_id, is_active FROM training_plan").fetchall()
        self.assertTrue(all(r["is_active"] == 1 for r in rows))


class TestGetActiveSchedule(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)

    def test_only_active_rows_returned(self):
        save_schedule(self.conn, self.athlete_id, [_day(START, workout_type="easy")], created_at="2026-08-01T00:00:00")
        save_schedule(self.conn, self.athlete_id, [_day(START, workout_type="tempo")], created_at="2026-08-02T00:00:00")

        active = get_active_schedule(self.conn, self.athlete_id)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["workout_type"], "tempo")

    def test_ordered_by_date(self):
        schedule = [
            _day(START + datetime.timedelta(days=2)),
            _day(START),
            _day(START + datetime.timedelta(days=1)),
        ]
        save_schedule(self.conn, self.athlete_id, schedule)
        active = get_active_schedule(self.conn, self.athlete_id)
        self.assertEqual(
            [row["planned_date"] for row in active],
            [START.isoformat(), (START + datetime.timedelta(days=1)).isoformat(), (START + datetime.timedelta(days=2)).isoformat()],
        )

    def test_includes_external_rows(self):
        self.conn.execute(
            """
            INSERT INTO training_plan
                (athlete_id, planned_date, workout_type, plan_source, is_active, created_at)
            VALUES (?, ?, 'easy', 'external', 1, '2026-07-01T00:00:00')
            """,
            (self.athlete_id, START.isoformat()),
        )
        self.conn.commit()

        active = get_active_schedule(self.conn, self.athlete_id)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["plan_source"], "external")

    def test_empty_when_no_plan(self):
        self.assertEqual(get_active_schedule(self.conn, self.athlete_id), [])


class TestGetPlanHistoryForDate(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)

    def test_includes_superseded_and_active_rows_in_created_order(self):
        first = save_schedule(
            self.conn, self.athlete_id, [_day(START, workout_type="easy")],
            created_at="2026-08-01T00:00:00",
        )
        second = save_schedule(
            self.conn, self.athlete_id, [_day(START, workout_type="tempo")],
            created_at="2026-08-02T00:00:00",
        )

        history = get_plan_history_for_date(self.conn, self.athlete_id, START)
        self.assertEqual([row["id"] for row in history], [first[0], second[0]])
        self.assertEqual(history[0]["is_active"], 0)
        self.assertEqual(history[1]["is_active"], 1)

    def test_empty_for_date_with_no_plan(self):
        history = get_plan_history_for_date(self.conn, self.athlete_id, START)
        self.assertEqual(history, [])


if __name__ == "__main__":
    unittest.main()


class TestSaveExternalWorkouts(unittest.TestCase):
    """N-2 最小版：外部（跑團）課表逐日落地。"""

    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)
        self.conn.commit()

    def test_writes_rows_marked_external(self):
        save_external_workouts(
            self.conn,
            self.athlete_id,
            [
                {"planned_date": START, "workout_type": "tempo", "planned_distance_km": 8.0},
                {"planned_date": START + datetime.timedelta(days=3), "workout_type": "lsd"},
            ],
        )
        rows = get_active_schedule(self.conn, self.athlete_id)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["plan_source"] == "external" for row in rows))

    def test_accepts_iso_string_date(self):
        """日期允許字串——呼叫端可能是 MCP tool，那裡拿到的是 JSON 字串。"""
        save_external_workouts(
            self.conn, self.athlete_id, [{"planned_date": "2026-08-01", "workout_type": "easy"}]
        )
        rows = get_active_schedule(self.conn, self.athlete_id)
        self.assertEqual(rows[0]["planned_date"], "2026-08-01")

    def test_notes_preserve_original_wording(self):
        """課種對不上時原話須留存在 notes——不可為了整齊而丟棄。"""
        save_external_workouts(
            self.conn,
            self.athlete_id,
            [
                {
                    "planned_date": START,
                    "workout_type": "interval",
                    "notes": "教練說：400 公尺 ×8，休 90 秒",
                }
            ],
        )
        rows = get_active_schedule(self.conn, self.athlete_id)
        self.assertIn("400 公尺 ×8", rows[0]["notes"])

    def test_rejects_unknown_workout_type(self):
        """不在 CHECK 集合內的課種要當場擋下，而非讓 sqlite 丟出難懂的錯誤。"""
        with self.assertRaises(ValueError):
            save_external_workouts(
                self.conn, self.athlete_id, [{"planned_date": START, "workout_type": "配速跑"}]
            )

    def test_supersedes_generated_plan_on_same_day(self):
        """外部課表介入後即為唯一基準：同日的系統自產課表須被標記取代，
        避免 get_active_schedule() 同一天回傳兩列讓教練不知該聽誰的。"""
        save_schedule(self.conn, self.athlete_id, [_day(START, workout_type="easy")])
        save_external_workouts(
            self.conn, self.athlete_id, [{"planned_date": START, "workout_type": "tempo"}]
        )

        active = get_active_schedule(self.conn, self.athlete_id)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["plan_source"], "external")

        history = get_plan_history_for_date(self.conn, self.athlete_id, START)
        self.assertEqual(len(history), 2)
        superseded = [row for row in history if row["is_active"] == 0]
        self.assertEqual(len(superseded), 1)
        self.assertEqual(superseded[0]["plan_source"], "generated")
        self.assertIsNotNone(superseded[0]["superseded_by"])

    def test_generated_plan_never_overwrites_external(self):
        """反向不成立：系統自產課表不得覆蓋教練排的那天（既有 save_schedule 行為的迴歸）。"""
        save_external_workouts(
            self.conn, self.athlete_id, [{"planned_date": START, "workout_type": "tempo"}]
        )
        save_schedule(self.conn, self.athlete_id, [_day(START, workout_type="easy")])

        active = get_active_schedule(self.conn, self.athlete_id)
        sources = sorted(row["plan_source"] for row in active)
        self.assertEqual(sources, ["external", "generated"])
        external_row = [r for r in active if r["plan_source"] == "external"][0]
        self.assertEqual(external_row["is_active"], 1)
        self.assertIsNone(external_row["superseded_by"])

    def test_rerun_replaces_previous_external_version(self):
        """教練改課時重跑同一批即可，不會留下兩列生效的外部課表。"""
        save_external_workouts(
            self.conn,
            self.athlete_id,
            [{"planned_date": START, "workout_type": "tempo", "planned_distance_km": 8.0}],
        )
        save_external_workouts(
            self.conn,
            self.athlete_id,
            [{"planned_date": START, "workout_type": "easy", "planned_distance_km": 5.0}],
        )
        active = get_active_schedule(self.conn, self.athlete_id)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["workout_type"], "easy")


class TestSaveExternalWorkoutsClearsGaps(unittest.TestCase):
    """跑團課表只排有練的日子，中間空檔不可留著系統自產的課
    （§1：系統退居分析層，不在教練沒排的日子補課）。"""

    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)
        self.conn.commit()
        # 系統先產生連續 5 天的課表
        save_schedule(
            self.conn,
            self.athlete_id,
            [_day(START + datetime.timedelta(days=i)) for i in range(5)],
        )

    def test_gaps_are_deactivated_when_requested(self):
        save_external_workouts(
            self.conn,
            self.athlete_id,
            [
                {"planned_date": START, "workout_type": "tempo"},
                {"planned_date": START + datetime.timedelta(days=4), "workout_type": "lsd"},
            ],
            deactivate_generated_between=True,
        )
        active = get_active_schedule(self.conn, self.athlete_id)
        self.assertEqual(len(active), 2)
        self.assertTrue(all(row["plan_source"] == "external" for row in active))

    def test_gaps_are_kept_by_default(self):
        """預設不動——只補登一兩天時不該清掉整段。"""
        save_external_workouts(
            self.conn,
            self.athlete_id,
            [
                {"planned_date": START, "workout_type": "tempo"},
                {"planned_date": START + datetime.timedelta(days=4), "workout_type": "lsd"},
            ],
        )
        active = get_active_schedule(self.conn, self.athlete_id)
        self.assertEqual(len(active), 5)

    def test_dates_outside_batch_range_are_untouched(self):
        """只清本批日期範圍內的空檔，範圍外的自產課表不受影響。"""
        save_external_workouts(
            self.conn,
            self.athlete_id,
            [
                {"planned_date": START, "workout_type": "tempo"},
                {"planned_date": START + datetime.timedelta(days=2), "workout_type": "lsd"},
            ],
            deactivate_generated_between=True,
        )
        active = get_active_schedule(self.conn, self.athlete_id)
        remaining_generated = [r for r in active if r["plan_source"] == "generated"]
        self.assertEqual(
            sorted(r["planned_date"] for r in remaining_generated),
            [
                (START + datetime.timedelta(days=3)).isoformat(),
                (START + datetime.timedelta(days=4)).isoformat(),
            ],
        )

    def test_existing_external_rows_are_not_cleared(self):
        """不動其他 external 列——先前落地的跑團課不該被新一批蓋掉。"""
        save_external_workouts(
            self.conn,
            self.athlete_id,
            [{"planned_date": START + datetime.timedelta(days=2), "workout_type": "interval"}],
        )
        save_external_workouts(
            self.conn,
            self.athlete_id,
            [
                {"planned_date": START, "workout_type": "tempo"},
                {"planned_date": START + datetime.timedelta(days=4), "workout_type": "lsd"},
            ],
            deactivate_generated_between=True,
        )
        active = get_active_schedule(self.conn, self.athlete_id)
        kept = [r for r in active if r["planned_date"] == (START + datetime.timedelta(days=2)).isoformat()]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["workout_type"], "interval")


def _insert_activity(conn, athlete_id, started_at, activity_type="running", warmup_for=None):
    cursor = conn.execute(
        "INSERT INTO activities (athlete_id, source, fetched_at, activity_type, started_at, "
        "distance_km, duration_sec, warmup_for_activity_id) "
        "VALUES (?, 'garmin_mcp', '2026-08-01T00:00:00', ?, ?, 5.0, 1800, ?)",
        (athlete_id, activity_type, started_at, warmup_for),
    )
    return cursor.lastrowid


def _linked(conn, plan_id):
    return conn.execute(
        "SELECT linked_activity_id FROM training_plan WHERE id = ?", (plan_id,)
    ).fetchone()["linked_activity_id"]


class TestSetPlanLink(unittest.TestCase):
    """PLAN.md §1 2026-10-24：學員確認的配對只寫 linked_activity_id 這一欄。"""

    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)
        [self.plan_id] = save_external_workouts(
            self.conn,
            self.athlete_id,
            [{"planned_date": "2026-08-03", "workout_type": "easy", "planned_distance_km": 8.0}],
        )
        self.run_a = _insert_activity(self.conn, self.athlete_id, "2026-08-03T06:00:00")
        self.run_b = _insert_activity(self.conn, self.athlete_id, "2026-08-03T18:00:00")
        self.conn.commit()

    def _plan_row(self):
        return dict(
            self.conn.execute("SELECT * FROM training_plan WHERE id = ?", (self.plan_id,)).fetchone()
        )

    def test_link_writes_only_linked_activity_id(self):
        before = self._plan_row()
        result = set_plan_link(self.conn, self.athlete_id, self.plan_id, self.run_a)
        after = self._plan_row()

        self.assertEqual(result["plan_id"], self.plan_id)
        self.assertEqual(result["linked_activity_id"], self.run_a)
        self.assertEqual(after.pop("linked_activity_id"), self.run_a)
        before.pop("linked_activity_id")
        self.assertEqual(after, before, "課表其餘欄位不得被改動")

    def test_link_reclassifies_that_day_from_plan(self):
        """同日兩場跑步時課表無法唯一對應，課種沿用分圈；確認配對後當天立即
        改以課表為準（N-28），不必等課表重寫或下次同步。"""
        self.conn.execute(
            "UPDATE activities SET workout_type = 'tempo', detected_workout_type = 'tempo', "
            "workout_type_basis = 'laps' WHERE id = ?",
            (self.run_a,),
        )
        self.conn.commit()
        result = set_plan_link(self.conn, self.athlete_id, self.plan_id, self.run_a)
        row = self.conn.execute(
            "SELECT workout_type, workout_type_basis FROM activities WHERE id = ?", (self.run_a,)
        ).fetchone()
        self.assertEqual((row["workout_type"], row["workout_type_basis"]), ("easy", "plan"))
        self.assertIn(self.run_a, [c["activity_id"] for c in result["reclassified"]])

    def test_relink_to_another_activity(self):
        set_plan_link(self.conn, self.athlete_id, self.plan_id, self.run_a)
        set_plan_link(self.conn, self.athlete_id, self.plan_id, self.run_b)
        self.assertEqual(_linked(self.conn, self.plan_id), self.run_b)

    def test_none_clears_link(self):
        set_plan_link(self.conn, self.athlete_id, self.plan_id, self.run_a)
        result = set_plan_link(self.conn, self.athlete_id, self.plan_id, None)
        self.assertIsNone(result["linked_activity_id"])
        self.assertIsNone(_linked(self.conn, self.plan_id))

    def test_rejects_activity_on_another_date(self):
        other_day = _insert_activity(self.conn, self.athlete_id, "2026-08-04T06:00:00")
        with self.assertRaises(PlanLinkRejectedError):
            set_plan_link(self.conn, self.athlete_id, self.plan_id, other_day)
        self.assertIsNone(_linked(self.conn, self.plan_id))

    def test_rejects_incompatible_sport(self):
        walk = _insert_activity(self.conn, self.athlete_id, "2026-08-03T12:00:00", "walking")
        with self.assertRaises(PlanLinkRejectedError):
            set_plan_link(self.conn, self.athlete_id, self.plan_id, walk)
        self.assertIsNone(_linked(self.conn, self.plan_id))

    def test_rejects_warmup(self):
        warmup = _insert_activity(
            self.conn, self.athlete_id, "2026-08-03T05:40:00", warmup_for=self.run_a
        )
        with self.assertRaises(PlanLinkRejectedError) as ctx:
            set_plan_link(self.conn, self.athlete_id, self.plan_id, warmup)
        self.assertIn("暖身", str(ctx.exception))

    def test_rejects_missing_plan(self):
        with self.assertRaises(PlanLinkNotFoundError):
            set_plan_link(self.conn, self.athlete_id, 9999, self.run_a)

    def test_rejects_missing_activity(self):
        with self.assertRaises(PlanLinkNotFoundError):
            set_plan_link(self.conn, self.athlete_id, self.plan_id, 9999)

    def test_rejects_inactive_plan(self):
        save_external_workouts(
            self.conn, self.athlete_id, [{"planned_date": "2026-08-03", "workout_type": "tempo"}]
        )  # 同日新版本取代 self.plan_id
        with self.assertRaises(PlanLinkRejectedError):
            set_plan_link(self.conn, self.athlete_id, self.plan_id, self.run_a)
        self.assertIsNone(_linked(self.conn, self.plan_id))

    def test_rejects_other_athletes_plan_and_activity(self):
        other_athlete = _build_athlete(self.conn)
        other_run = _insert_activity(self.conn, other_athlete, "2026-08-03T07:00:00")
        with self.assertRaises(PlanLinkNotFoundError):
            set_plan_link(self.conn, other_athlete, self.plan_id, other_run)
        with self.assertRaises(PlanLinkNotFoundError):
            set_plan_link(self.conn, self.athlete_id, self.plan_id, other_run)
        self.assertIsNone(_linked(self.conn, self.plan_id))

    def test_rejects_activity_already_linked_to_another_plan(self):
        # 同一天另一份生效課表（generated 不會取代 external，兩列並存）
        self.conn.execute(
            "INSERT INTO training_plan (athlete_id, planned_date, workout_type, plan_source, "
            "is_active, linked_activity_id, created_at) "
            "VALUES (?, '2026-08-03', 'easy', 'generated', 1, ?, '2026-08-01T00:00:00')",
            (self.athlete_id, self.run_a),
        )
        self.conn.commit()
        with self.assertRaises(PlanLinkRejectedError):
            set_plan_link(self.conn, self.athlete_id, self.plan_id, self.run_a)
        self.assertIsNone(_linked(self.conn, self.plan_id))
