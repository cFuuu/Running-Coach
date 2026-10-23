"""`PUT /api/plan/{plan_id}/link` 的路由測試——來源限制、錯誤轉換、回傳形狀，
以及寫入後 `/api/calendar` 的配對是否真的變成 exact、歧義是否解除。

欄位驗證的細節（日期、運動種類、暖身、他人資料……）在
test_training_plan_store.py 的 TestSetPlanLink；這裡只確認路由把它們轉成
正確的 HTTP 狀態，不重複每一種組合。

⚠️ TestClient 預設 host 是 `"testclient"`（非合法 IP），來源限制 fail closed，
要通過的請求必須明確傳 `client=("127.0.0.1", 1)`。
"""

import logging
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.main.python.api import routes_dashboard
from src.main.python.api.app import create_app
from src.main.python.services import coach_chat
from src.test.unit.fixtures import build_fixture_db

LOCAL_CLIENT = ("127.0.0.1", 1)
LAN_CLIENT = ("192.168.1.50", 1)

PLAN_DATE = "2026-03-09"  # fixture 唯一一筆課表（lsd）與 activity 100 同一天

for _noisy in ("httpx", "httpx2"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


class PlanLinkRouteTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "test.db"
        conn = build_fixture_db(self.db_path)
        self.athlete_id = conn.execute("SELECT id FROM athlete_profile").fetchone()["id"]
        self.plan_id = conn.execute(
            "SELECT id FROM training_plan WHERE planned_date = ?", (PLAN_DATE,)
        ).fetchone()["id"]
        self.main_run = conn.execute(
            "SELECT id FROM activities WHERE external_id = '10000001'"
        ).fetchone()["id"]
        # 同一天第二場跑步：讓配對變成 ambiguous（兩場都相容於同一份課表）
        self.second_run = self._insert_activity(conn, "2026-03-09T18:00:00", "running", 4.0)
        self.walk = self._insert_activity(conn, "2026-03-09T12:00:00", "walking", 2.0)
        conn.commit()
        conn.close()

        assets_dir = Path(self._tmp.name) / "assets"
        assets_dir.mkdir()
        self.app = create_app(
            str(self.db_path),
            assets_dir=assets_dir,
            chat_config=coach_chat.ChatConfig(api_key_present=False),
        )
        self.allow_private = False
        self.app.dependency_overrides[routes_dashboard.allow_plan_link_private_network] = (
            lambda: self.allow_private
        )

    def _insert_activity(self, conn, started_at, activity_type, distance_km):
        return conn.execute(
            "INSERT INTO activities (athlete_id, source, fetched_at, activity_type, started_at, "
            "distance_km, duration_sec) VALUES (?, 'garmin_mcp', '2026-03-10T00:00:00', ?, ?, ?, 1500)",
            (self.athlete_id, activity_type, started_at, distance_km),
        ).lastrowid

    def client(self, client=LOCAL_CLIENT):
        return TestClient(self.app, client=client)

    def put_link(self, activity_id, plan_id=None, client=LOCAL_CLIENT, json=None):
        body = {"activity_id": activity_id} if json is None else json
        return self.client(client).put(f"/api/plan/{plan_id or self.plan_id}/link", json=body)

    def linked_in_db(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(
                "SELECT linked_activity_id FROM training_plan WHERE id = ?", (self.plan_id,)
            ).fetchone()[0]
        finally:
            conn.close()

    def calendar_matches(self):
        r = self.client().get(
            "/api/calendar", params={"start": PLAN_DATE, "end": PLAN_DATE, "today": "2026-03-10"}
        )
        self.assertEqual(r.status_code, 200)
        [day] = r.json()["days"]
        return {a["id"]: a["plan_match"] for a in day["activities"]}


class TestLinkAndCalendar(PlanLinkRouteTestCase):
    def test_calendar_is_ambiguous_before_linking(self):
        """前提：沒有確認前，兩場跑步搶同一份課表，行事曆不猜。"""
        matches = self.calendar_matches()
        self.assertEqual(matches[self.main_run]["state"], "ambiguous")
        self.assertEqual(matches[self.second_run]["state"], "ambiguous")

    def test_link_returns_exact_and_resolves_ambiguity(self):
        r = self.put_link(self.main_run)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            r.json(),
            {
                "plan_id": self.plan_id,
                "linked_activity_id": self.main_run,
                "plan_match": {"state": "exact", "method": "linked_activity_id", "reason": None},
            },
        )
        self.assertEqual(self.linked_in_db(), self.main_run)

        matches = self.calendar_matches()
        self.assertEqual(matches[self.main_run]["state"], "exact")
        self.assertEqual(matches[self.main_run]["method"], "linked_activity_id")
        # 競爭的那一場不再 ambiguous：課表已被確認給別場
        self.assertEqual(matches[self.second_run]["state"], "none")

    def test_relink_moves_exact_to_other_activity(self):
        self.put_link(self.main_run)
        r = self.put_link(self.second_run)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["plan_match"]["state"], "exact")
        matches = self.calendar_matches()
        self.assertEqual(matches[self.second_run]["state"], "exact")
        self.assertEqual(matches[self.main_run]["state"], "none")

    def test_null_clears_link_and_ambiguity_returns(self):
        self.put_link(self.main_run)
        r = self.put_link(None)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            r.json(), {"plan_id": self.plan_id, "linked_activity_id": None, "plan_match": None}
        )
        self.assertIsNone(self.linked_in_db())
        self.assertEqual(self.calendar_matches()[self.main_run]["state"], "ambiguous")


class TestValidationErrors(PlanLinkRouteTestCase):
    def test_incompatible_sport_is_422(self):
        r = self.put_link(self.walk)
        self.assertEqual(r.status_code, 422)
        self.assertIn("運動種類不相容", r.json()["detail"])
        self.assertIsNone(self.linked_in_db())

    def test_missing_plan_is_404(self):
        r = self.put_link(self.main_run, plan_id=99999)
        self.assertEqual(r.status_code, 404)

    def test_missing_activity_is_404(self):
        r = self.put_link(99999)
        self.assertEqual(r.status_code, 404)

    def test_missing_activity_id_key_is_422_not_clear(self):
        """漏帶欄位不可被當成取消，否則前端一個 bug 就清掉確認過的連結。"""
        self.put_link(self.main_run)
        r = self.put_link(None, json={})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.linked_in_db(), self.main_run)

    def test_non_integer_activity_id_is_422(self):
        for bad in ("12", 1.5, True):
            with self.subTest(bad=bad):
                r = self.put_link(None, json={"activity_id": bad})
                self.assertEqual(r.status_code, 422)


class TestOriginRestriction(PlanLinkRouteTestCase):
    def test_lan_client_rejected_by_default(self):
        r = self.put_link(self.main_run, client=LAN_CLIENT)
        self.assertEqual(r.status_code, 403)
        self.assertIn("RC_PLAN_LINK_ALLOW_PRIVATE_NETWORK", r.json()["detail"])
        self.assertIsNone(self.linked_in_db(), "被擋下時不可寫入")

    def test_unparseable_host_rejected(self):
        r = TestClient(self.app).put(
            f"/api/plan/{self.plan_id}/link", json={"activity_id": self.main_run}
        )
        self.assertEqual(r.status_code, 403)

    def test_lan_client_allowed_when_flag_set(self):
        self.allow_private = True
        r = self.put_link(self.main_run, client=LAN_CLIENT)
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
