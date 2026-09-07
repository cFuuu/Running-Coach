"""context_notes_store 的單元測試——全部使用合成假資料與臨時 SQLite，不含真實個人資料。"""

import datetime
import unittest

from src.main.python.models.db import get_connection
from src.main.python.services.context_notes_store import (
    add_note,
    delete_note,
    get_notes_in_range,
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
