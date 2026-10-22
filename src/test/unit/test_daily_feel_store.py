"""daily_feel_store 與 /api/daily-feel 的單元測試——合成假資料與臨時 SQLite，不含真實個人資料。"""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.main.python.api.app import create_app
from src.main.python.models.db import get_connection
from src.main.python.services import coach_chat
from src.main.python.services.daily_feel_store import (
    delete_feel,
    get_feels_in_range,
    set_feel,
)


def _build_athlete(conn) -> int:
    cursor = conn.execute(
        "INSERT INTO athlete_profile (name, updated_at) VALUES ('測試學員', '2026-01-01T00:00:00')"
    )
    return cursor.lastrowid


class TestDailyFeelStore(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)
        self.conn.commit()

    def test_set_then_get(self):
        set_feel(self.conn, self.athlete_id, "2026-10-22", "tired", "dashboard")
        feels = get_feels_in_range(self.conn, self.athlete_id, "2026-10-01", "2026-10-31")
        self.assertEqual([(f["date"], f["feel"], f["source"]) for f in feels],
                         [("2026-10-22", "tired", "dashboard")])

    def test_same_day_overwrites_instead_of_accumulating(self):
        """一天只留一筆：點錯改選時直接覆寫，分析時不必挑哪一筆。"""
        set_feel(self.conn, self.athlete_id, "2026-10-22", "tired", "dashboard")
        set_feel(self.conn, self.athlete_id, "2026-10-22", "good", "coach", note="睡飽了")
        feels = get_feels_in_range(self.conn, self.athlete_id, "2026-10-22", "2026-10-22")
        self.assertEqual(len(feels), 1)
        self.assertEqual((feels[0]["feel"], feels[0]["source"], feels[0]["note"]),
                         ("good", "coach", "睡飽了"))

    def test_blank_note_stored_as_none(self):
        row = set_feel(self.conn, self.athlete_id, "2026-10-22", "normal", "dashboard", note="  ")
        self.assertIsNone(row["note"])

    def test_rejects_unknown_feel_and_source(self):
        with self.assertRaises(ValueError):
            set_feel(self.conn, self.athlete_id, "2026-10-22", "exhausted", "dashboard")
        with self.assertRaises(ValueError):
            set_feel(self.conn, self.athlete_id, "2026-10-22", "tired", "garmin")

    def test_rejects_malformed_date(self):
        """非 ISO 格式的日期會與其他表對不上，寫入前就擋下。"""
        with self.assertRaises(ValueError):
            set_feel(self.conn, self.athlete_id, "2026-10-2", "tired", "dashboard")

    def test_delete(self):
        set_feel(self.conn, self.athlete_id, "2026-10-22", "tired", "dashboard")
        self.assertTrue(delete_feel(self.conn, self.athlete_id, "2026-10-22"))
        self.assertFalse(delete_feel(self.conn, self.athlete_id, "2026-10-22"))
        self.assertEqual(get_feels_in_range(self.conn, self.athlete_id, "2026-10-22", "2026-10-22"), [])


class TestRulesDoNotReadDailyFeel(unittest.TestCase):
    """硬邊界：規則模組不得讀體感，否則體感就不能再拿來驗證規則。"""

    def test_rule_modules_do_not_import_store(self):
        services = Path(__file__).resolve().parents[2] / "main" / "python" / "services"
        for name in ("readiness.py", "training_load.py", "session_quality.py"):
            source = (services / name).read_text(encoding="utf-8")
            self.assertNotIn("daily_feel", source, name)


class TestDailyFeelRoutes(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "test.db"
        conn = get_connection(db_path)
        _build_athlete(conn)
        conn.commit()
        conn.close()
        assets_dir = Path(tmp.name) / "assets"
        assets_dir.mkdir()
        app = create_app(
            str(db_path),
            assets_dir=assets_dir,
            chat_config=coach_chat.ChatConfig(api_key_present=False),
        )
        self.client = TestClient(app)

    def test_put_get_delete_roundtrip(self):
        r = self.client.put("/api/daily-feel/2026-10-22", json={"feel": "very_tired"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["source"], "dashboard")

        r = self.client.get("/api/daily-feel", params={"start": "2026-10-22", "end": "2026-10-22"})
        self.assertEqual([f["feel"] for f in r.json()["feels"]], ["very_tired"])

        r = self.client.delete("/api/daily-feel/2026-10-22")
        self.assertEqual(r.json(), {"deleted": True})
        r = self.client.get("/api/daily-feel", params={"start": "2026-10-22", "end": "2026-10-22"})
        self.assertEqual(r.json()["feels"], [])

    def test_invalid_input_is_400(self):
        self.assertEqual(
            self.client.put("/api/daily-feel/2026-10-22", json={"feel": "累"}).status_code, 400
        )
        self.assertEqual(
            self.client.put("/api/daily-feel/not-a-date", json={"feel": "tired"}).status_code, 400
        )


if __name__ == "__main__":
    unittest.main()
