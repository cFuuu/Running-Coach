"""context_notes_store 的單元測試——全部使用合成假資料與臨時 SQLite，不含真實個人資料。"""

import datetime
import unittest

from src.main.python.models.db import get_connection
from src.main.python.services.context_notes_store import (
    TrackingActionError,
    TrackingItemNotFound,
    add_note,
    add_tracking_item,
    apply_tracking_action,
    delete_note,
    get_notes_in_range,
    get_tracking_item,
    list_tracking_items,
)

START = datetime.date(2026, 9, 1)


def _build_athlete(conn, name="測試學員") -> int:
    cursor = conn.execute(
        "INSERT INTO athlete_profile (name, updated_at) VALUES (?, '2026-01-01T00:00:00')",
        (name,),
    )
    return cursor.lastrowid


class TestAddNote(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)
        self.conn.commit()

    def test_stores_note_verbatim(self):
        """原話須原樣保留——摘要會抹掉「腳有點緊」與「腳痛」的差別。"""
        add_note(self.conn, self.athlete_id, "左腳有點緊，但跑起來不痛", START)
        rows = get_notes_in_range(self.conn, self.athlete_id, START, START)
        self.assertEqual(rows[0]["note"], "左腳有點緊，但跑起來不痛")

    def test_accepts_iso_string_dates(self):
        add_note(self.conn, self.athlete_id, "出差", "2026-09-01", "2026-09-05")
        rows = get_notes_in_range(self.conn, self.athlete_id, START, START)
        self.assertEqual(rows[0]["end_date"], "2026-09-05")

    def test_end_date_optional_means_single_day(self):
        add_note(self.conn, self.athlete_id, "睡不好", START)
        rows = get_notes_in_range(self.conn, self.athlete_id, START, START)
        self.assertIsNone(rows[0]["end_date"])

    def test_rejects_empty_note(self):
        with self.assertRaises(ValueError):
            add_note(self.conn, self.athlete_id, "   ", START)

    def test_rejects_end_before_start(self):
        with self.assertRaises(ValueError):
            add_note(self.conn, self.athlete_id, "出差", START, START - datetime.timedelta(days=1))

    def test_multiple_notes_on_same_day_are_kept(self):
        """同一天可以有多筆——「睡不好」與「腳有點緊」是兩件事，不做去重。"""
        add_note(self.conn, self.athlete_id, "睡不好", START)
        add_note(self.conn, self.athlete_id, "腳有點緊", START)
        self.assertEqual(len(get_notes_in_range(self.conn, self.athlete_id, START, START)), 2)


class TestGetNotesInRange(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)
        self.conn.commit()

    def test_overlapping_range_is_returned(self):
        """重疊即回傳：09-01~09-10 的出差，查 09-05~09-06 時必須出現
        ——那幾天正在出差當中。"""
        add_note(self.conn, self.athlete_id, "出差", START, START + datetime.timedelta(days=9))
        rows = get_notes_in_range(
            self.conn,
            self.athlete_id,
            START + datetime.timedelta(days=4),
            START + datetime.timedelta(days=5),
        )
        self.assertEqual(len(rows), 1)

    def test_non_overlapping_range_is_excluded(self):
        add_note(self.conn, self.athlete_id, "出差", START, START + datetime.timedelta(days=2))
        rows = get_notes_in_range(
            self.conn,
            self.athlete_id,
            START + datetime.timedelta(days=10),
            START + datetime.timedelta(days=12),
        )
        self.assertEqual(rows, [])

    def test_other_athletes_notes_are_not_returned(self):
        other_id = _build_athlete(self.conn, name="另一位學員")
        self.conn.commit()
        add_note(self.conn, other_id, "別人的紀錄", START)
        self.assertEqual(get_notes_in_range(self.conn, self.athlete_id, START, START), [])


class TestDeleteNote(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)
        self.conn.commit()

    def test_deletes_and_reports_true(self):
        note_id = add_note(self.conn, self.athlete_id, "記錯了", START)
        self.assertTrue(delete_note(self.conn, self.athlete_id, note_id))
        self.assertEqual(get_notes_in_range(self.conn, self.athlete_id, START, START), [])

    def test_cannot_delete_other_athletes_note(self):
        """跨學員誤刪防護：只憑 id 不得動手。"""
        other_id = _build_athlete(self.conn, name="另一位學員")
        self.conn.commit()
        note_id = add_note(self.conn, other_id, "別人的紀錄", START)
        self.assertFalse(delete_note(self.conn, self.athlete_id, note_id))
        self.assertEqual(len(get_notes_in_range(self.conn, other_id, START, START)), 1)


class TestTrackingCoverageInRange(unittest.TestCase):
    """N-35 順帶修的缺陷：end_date 為 NULL 的追蹤事項不可隔天就消失。"""

    TODAY = START + datetime.timedelta(days=20)

    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)
        self.conn.commit()

    def _query(self, day):
        return get_notes_in_range(self.conn, self.athlete_id, day, day, today=self.TODAY)

    def test_plain_note_without_end_date_is_still_single_day(self):
        add_note(self.conn, self.athlete_id, "睡不好", START)
        self.assertEqual(self._query(START + datetime.timedelta(days=1)), [])

    def test_open_tracking_items_last_until_today(self):
        tracking_id = add_tracking_item(self.conn, self.athlete_id, "左膝痛", START)
        apply_tracking_action(self.conn, self.athlete_id, tracking_id, "accept", START)
        add_tracking_item(self.conn, self.athlete_id, "感冒", START)  # 待同意
        self.assertEqual(len(self._query(self.TODAY)), 2)
        self.assertEqual(self._query(self.TODAY + datetime.timedelta(days=1)), [])

    def test_closed_item_covers_until_closed_date(self):
        item_id = add_tracking_item(self.conn, self.athlete_id, "左膝痛", START)
        apply_tracking_action(self.conn, self.athlete_id, item_id, "accept", START)
        closed = START + datetime.timedelta(days=7)
        apply_tracking_action(self.conn, self.athlete_id, item_id, "resolve", closed)
        self.assertEqual(len(self._query(closed)), 1)
        self.assertEqual(self._query(closed + datetime.timedelta(days=1)), [])

    def test_rows_carry_tracking_status(self):
        add_note(self.conn, self.athlete_id, "出差", START)
        self.assertEqual(self._query(START)[0]["tracking_status"], "none")


class TestTrackingTransitions(unittest.TestCase):
    TODAY = datetime.date(2026, 10, 27)

    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)
        self.conn.commit()
        self.item_id = add_tracking_item(self.conn, self.athlete_id, "  左膝外側跑完會痛 ", "2026-10-20")

    def _apply(self, action, item_id=None):
        return apply_tracking_action(
            self.conn, self.athlete_id, item_id or self.item_id, action, self.TODAY
        )

    def test_new_item_is_proposed_with_verbatim_note(self):
        item = get_tracking_item(self.conn, self.athlete_id, self.item_id)
        self.assertEqual(item["tracking_status"], "proposed")
        self.assertEqual(item["note"], "左膝外側跑完會痛")
        self.assertIsNone(item["end_date"])

    def test_rejects_bad_start_date_and_empty_note(self):
        with self.assertRaises(ValueError):
            add_tracking_item(self.conn, self.athlete_id, "膝蓋痛", "10/20")
        with self.assertRaises(ValueError):
            add_tracking_item(self.conn, self.athlete_id, " ", "2026-10-20")

    def test_accept_starts_tracking_and_counts_as_followup_and_mention(self):
        item = self._apply("accept")
        self.assertEqual(item["tracking_status"], "tracking")
        self.assertEqual(item["last_followup_date"], "2026-10-27")
        self.assertEqual(item["last_mentioned_date"], "2026-10-27")

    def test_decline_closes_as_dismissed(self):
        item = self._apply("decline")
        self.assertEqual(item["tracking_status"], "dismissed")
        self.assertEqual(item["closed_date"], "2026-10-27")

    def test_record_actions_only_touch_their_date(self):
        self._apply("accept")
        self.conn.execute(
            "UPDATE context_notes SET last_followup_date = '2026-10-01', "
            "last_mentioned_date = '2026-10-01' WHERE id = ?",
            (self.item_id,),
        )
        item = self._apply("mentioned")
        self.assertEqual(item["last_mentioned_date"], "2026-10-27")
        self.assertEqual(item["last_followup_date"], "2026-10-01")
        item = self._apply("followed_up")
        self.assertEqual(item["last_followup_date"], "2026-10-27")
        item = self._apply("stale_asked")
        self.assertEqual(item["stale_asked_date"], "2026-10-27")
        self.assertEqual(item["tracking_status"], "tracking")

    def test_resolve_and_stop_close_tracking(self):
        self._apply("accept")
        item = self._apply("resolve")
        self.assertEqual((item["tracking_status"], item["closed_date"]), ("resolved", "2026-10-27"))

        other = add_tracking_item(self.conn, self.athlete_id, "足底筋膜炎", "2026-10-21")
        self._apply("accept", other)
        self.assertEqual(self._apply("stop", other)["tracking_status"], "dismissed")

    def test_invalid_transitions_are_rejected_with_reason(self):
        for action in ("followed_up", "mentioned", "stale_asked", "resolve", "stop"):
            with self.subTest(action=action), self.assertRaises(TrackingActionError) as cm:
                self._apply(action)  # 還在待同意
            self.assertIn("待學員同意", str(cm.exception))
        self._apply("accept")
        with self.assertRaises(TrackingActionError):
            self._apply("accept")
        self._apply("resolve")
        with self.assertRaises(TrackingActionError) as cm:
            self._apply("followed_up")
        self.assertIn("已結案", str(cm.exception))

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(TrackingActionError):
            self._apply("delete")

    def test_plain_context_note_cannot_be_touched(self):
        """一般情境筆記一律拒絕——教練不得藉追蹤事項的動作結案或修改它。"""
        note_id = add_note(self.conn, self.athlete_id, "出差", "2026-10-20")
        with self.assertRaises(TrackingItemNotFound):
            self._apply("resolve", note_id)
        row = self.conn.execute(
            "SELECT tracking_status, closed_date FROM context_notes WHERE id = ?", (note_id,)
        ).fetchone()
        self.assertEqual(tuple(row), ("none", None))

    def test_other_athletes_item_is_not_found(self):
        other_id = _build_athlete(self.conn, name="另一位學員")
        self.conn.commit()
        with self.assertRaises(TrackingItemNotFound):
            apply_tracking_action(self.conn, other_id, self.item_id, "accept", self.TODAY)

    def test_list_defaults_to_open_items(self):
        self._apply("accept")
        closed = add_tracking_item(self.conn, self.athlete_id, "感冒", "2026-10-01")
        self._apply("decline", closed)
        add_note(self.conn, self.athlete_id, "出差", "2026-10-20")
        items = list_tracking_items(self.conn, self.athlete_id)
        self.assertEqual([i["id"] for i in items], [self.item_id])


class TestFactLayerBoundary(unittest.TestCase):
    """§7.5 硬邊界：軟性輸入永不進事實層。"""

    def test_rule_engine_modules_do_not_import_context_notes(self):
        """規則層模組不得 import 本模組——軟性輸入無法驗證，讓它參與規則
        計算等於在事實層開一個沒有信賴度可標的洞。"""
        import pathlib

        services = pathlib.Path("src/main/python/services")
        for name in ("readiness.py", "training_load.py", "session_quality.py"):
            source = (services / name).read_text(encoding="utf-8")
            self.assertNotIn("context_notes", source, f"{name} 不應依賴 context_notes")
            self.assertNotIn("coach_tracking", source, f"{name} 不應依賴追蹤事項")
