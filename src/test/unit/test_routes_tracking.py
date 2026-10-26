"""routes_tracking 的單元測試——/api/chat/tracking 的列表、按鈕動作、錯誤碼與來源限制。

使用合成 fixture DB，不含真實個人資料。TestClient 必須明確傳 client host
（理由見 test_routes_chat.py 開頭說明）。
"""

import datetime
import logging
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.main.python.api import routes_tracking
from src.main.python.api.app import create_app
from src.main.python.models.db import get_connection
from src.main.python.services import coach_chat, context_notes_store
from src.test.unit.fixtures import build_fixture_db

LOCAL_CLIENT = ("127.0.0.1", 1)
TODAY = datetime.date(2026, 10, 27)

for _noisy in ("httpx", "httpx2"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


class TrackingRouteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "test.db"
        build_fixture_db(self.db_path).close()
        assets_dir = Path(self._tmp.name) / "assets"
        assets_dir.mkdir()
        # 聊天**未啟用**：追蹤事項端點不要求聊天啟用，只套來源限制
        self.app = create_app(
            str(self.db_path),
            assets_dir=assets_dir,
            chat_config=coach_chat.ChatConfig(api_key_present=False),
        )
        self.app.dependency_overrides[routes_tracking.get_today] = lambda: TODAY

        conn = get_connection(self.db_path)
        athlete_id = conn.execute("SELECT id FROM athlete_profile ORDER BY id LIMIT 1").fetchone()[0]
        self.proposed_id = context_notes_store.add_tracking_item(conn, athlete_id, "感冒第二天", "2026-10-26")
        self.tracking_id = context_notes_store.add_tracking_item(conn, athlete_id, "左膝外側跑完會痛", "2026-10-20")
        context_notes_store.apply_tracking_action(conn, athlete_id, self.tracking_id, "accept", "2026-10-24")
        self.plain_id = context_notes_store.add_note(conn, athlete_id, "出差", "2026-10-20")
        conn.close()

    def client(self, client=LOCAL_CLIENT):
        return TestClient(self.app, client=client)

    def test_list_returns_open_items_with_action(self):
        response = self.client().get("/api/chat/tracking")
        self.assertEqual(response.status_code, 200)
        items = {item["id"]: item for item in response.json()["items"]}
        self.assertEqual(set(items), {self.proposed_id, self.tracking_id})
        self.assertEqual(items[self.proposed_id]["action"], "await_consent")
        self.assertEqual(items[self.tracking_id]["action"], "follow_up")
        self.assertEqual(items[self.tracking_id]["action_reason"], "上次追問 10/24，已滿 3 天")

    def test_buttons_update_and_return_item(self):
        accepted = self.client().post(f"/api/chat/tracking/{self.proposed_id}", json={"action": "accept"})
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["tracking_status"], "tracking")
        self.assertEqual(accepted.json()["last_followup_date"], "2026-10-27")

        resolved = self.client().post(f"/api/chat/tracking/{self.tracking_id}", json={"action": "resolve"})
        self.assertEqual(resolved.json()["tracking_status"], "resolved")
        self.assertEqual(resolved.json()["closed_date"], "2026-10-27")
        ids = [i["id"] for i in self.client().get("/api/chat/tracking").json()["items"]]
        self.assertEqual(ids, [self.proposed_id])

    def test_record_actions_are_not_buttons(self):
        response = self.client().post(f"/api/chat/tracking/{self.tracking_id}", json={"action": "followed_up"})
        self.assertEqual(response.status_code, 422)

    def test_invalid_transition_is_400(self):
        response = self.client().post(f"/api/chat/tracking/{self.proposed_id}", json={"action": "resolve"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("待學員同意", response.json()["detail"])

    def test_missing_item_and_plain_note_are_404(self):
        for item_id in (99999, self.plain_id):
            with self.subTest(item_id=item_id):
                response = self.client().post(f"/api/chat/tracking/{item_id}", json={"action": "stop"})
                self.assertEqual(response.status_code, 404)

    def test_private_network_is_rejected_by_default(self):
        response = self.client(client=("192.168.1.5", 1)).get("/api/chat/tracking")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
