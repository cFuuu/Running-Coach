"""routes_sync 的單元測試——來源限制、參數驗證、憑證不外洩。

**絕不呼叫真實的 Garmin API**：runner 一律用 dependency_overrides 換成假的，
斷言的是「路由層有沒有把對的參數傳下去、有沒有把結果如實透出」，
而不是同步邏輯本身（那在 test_garmin_sync_runner.py）。

⚠️ Starlette 的 TestClient 預設 client host 是字串 `"testclient"`，
不是合法 IP。來源限制是 fail closed 的，所以每個要通過的測試都必須明確傳
`client=("127.0.0.1", 1)`——順帶讓「解析不出來的 host 一律 403」有得測。
"""

import logging
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.main.python.api import routes_sync
from src.main.python.api.app import create_app
from src.main.python.services import coach_chat
from src.test.unit.fixtures import build_fixture_db

LOCAL_CLIENT = ("127.0.0.1", 1)
LAN_CLIENT = ("192.168.1.50", 1)
TAILSCALE_CLIENT = ("100.101.102.103", 1)
# 刻意不用 203.0.113.x（TEST-NET-3）：Python 的 ip_address.is_private 對保留給
# 文件用途的網段回 True，拿它當「公網位址」測不到真正想測的東西。
PUBLIC_CLIENT = ("8.8.8.8", 1)

for _noisy in ("httpx", "httpx2"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


class FakeRunner:
    """記錄呼叫參數的假 runner。回傳值模仿 garmin_sync_runner.sync() 的形狀。"""

    def __init__(self, result=None):
        self.calls = []
        self.result = result or {
            "available": True,
            "dry_run": False,
            "days": 7,
            "days_auto_resolved": True,
            "gap_truncated": False,
            "estimate": {"summary": "7 天／35 次 API 請求／約 35 秒"},
            "start_date": "2026-09-03",
            "end_date": "2026-09-09",
            "activities": 3,
            "wellness": 7,
        }

    def __call__(self, conn, athlete_id, days=None, dry_run=False):
        self.calls.append({"athlete_id": athlete_id, "days": days, "dry_run": dry_run})
        return self.result


class SyncRouteTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "test.db"
        build_fixture_db(self.db_path).close()
        assets_dir = Path(self._tmp.name) / "assets"
        assets_dir.mkdir()

        self.app = create_app(
            str(self.db_path),
            assets_dir=assets_dir,
            chat_config=coach_chat.ChatConfig(api_key_present=False),
        )
        self.runner = FakeRunner()
        self.app.dependency_overrides[routes_sync.get_sync_runner] = lambda: self.runner
        # 預設不放行私有網段（與正式環境的預設一致）
        self.allow_private = False
        self.app.dependency_overrides[routes_sync.allow_private_network] = (
            lambda: self.allow_private
        )

    def client(self, client=LOCAL_CLIENT):
        return TestClient(self.app, client=client)


class TestOriginRestriction(SyncRouteTestCase):
    """同步會打 Garmin 的 API，觸發過頻會把帳號推向被鎖，故預設只接受本機。"""

    def test_localhost_is_allowed(self):
        r = self.client().post("/api/sync", json={"dry_run": True})
        self.assertEqual(r.status_code, 200)

    def test_lan_client_is_rejected_by_default(self):
        r = self.client(LAN_CLIENT).post("/api/sync", json={"dry_run": True})
        self.assertEqual(r.status_code, 403)
        self.assertIn("RC_SYNC_ALLOW_PRIVATE_NETWORK", r.json()["detail"])
        self.assertEqual(self.runner.calls, [], "被擋下時不該真的執行同步")

    def test_lan_client_allowed_when_flag_set(self):
        self.allow_private = True
        r = self.client(LAN_CLIENT).post("/api/sync", json={"dry_run": True})
        self.assertEqual(r.status_code, 200)

    def test_tailscale_cgnat_allowed_when_flag_set(self):
        """100.64.0.0/10 的 is_private 為 False，必須靠明列網段才放行。"""
        self.allow_private = True
        r = self.client(TAILSCALE_CLIENT).post("/api/sync", json={"dry_run": True})
        self.assertEqual(r.status_code, 200)

    def test_public_ip_rejected_even_when_flag_set(self):
        """放行的是「私有網段」，不是「所有人」。"""
        self.allow_private = True
        r = self.client(PUBLIC_CLIENT).post("/api/sync", json={"dry_run": True})
        self.assertEqual(r.status_code, 403)

    def test_unparseable_host_is_rejected(self):
        """fail closed：TestClient 預設的 "testclient" 不是合法 IP。"""
        r = TestClient(self.app).post("/api/sync", json={"dry_run": True})
        self.assertEqual(r.status_code, 403)

    def test_status_returns_200_even_when_not_allowed(self):
        """否則前端連「為什麼不能同步」都說不出口。"""
        r = self.client(LAN_CLIENT).get("/api/sync/status")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["client_allowed"])
        self.assertIn("RC_SYNC_ALLOW_PRIVATE_NETWORK", r.json()["reason"])

    def test_status_when_allowed_has_no_reason(self):
        r = self.client().get("/api/sync/status")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["client_allowed"])
        self.assertIsNone(r.json()["reason"])


class TestParameterValidation(SyncRouteTestCase):
    def test_days_omitted_passes_none_for_auto_resolve(self):
        """省略 days 是建議用法：由 runner 回推到上次同步的斷點。"""
        self.client().post("/api/sync", json={"dry_run": True})
        self.assertIsNone(self.runner.calls[0]["days"])

    def test_days_passed_through(self):
        self.client().post("/api/sync", json={"days": 30, "dry_run": True})
        self.assertEqual(self.runner.calls[0]["days"], 30)

    def test_days_zero_rejected(self):
        r = self.client().post("/api/sync", json={"days": 0})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.runner.calls, [])

    def test_days_negative_rejected(self):
        r = self.client().post("/api/sync", json={"days": -5})
        self.assertEqual(r.status_code, 422)

    def test_days_non_integer_rejected(self):
        r = self.client().post("/api/sync", json={"days": "30"})
        self.assertEqual(r.status_code, 422)

    def test_days_bool_rejected(self):
        """True 在 Python 是 int 的子類，不加防護會被當成 days=1。"""
        r = self.client().post("/api/sync", json={"days": True})
        self.assertEqual(r.status_code, 422)

    def test_dry_run_defaults_to_false(self):
        self.client().post("/api/sync", json={})
        self.assertFalse(self.runner.calls[0]["dry_run"])

    def test_dry_run_non_bool_rejected(self):
        r = self.client().post("/api/sync", json={"dry_run": "yes"})
        self.assertEqual(r.status_code, 422)

    def test_empty_body_is_accepted(self):
        """前端只按「同步」時不帶任何參數。"""
        r = self.client().post("/api/sync")
        self.assertEqual(r.status_code, 200)

    def test_athlete_id_resolved_from_db_when_omitted(self):
        self.client().post("/api/sync", json={"dry_run": True})
        self.assertIsInstance(self.runner.calls[0]["athlete_id"], int)


class TestResultPassthrough(SyncRouteTestCase):
    def test_dry_run_estimate_is_returned(self):
        """前端要先拿 estimate 顯示預計耗時，使用者確認後才真的執行。"""
        r = self.client().post("/api/sync", json={"dry_run": True})
        self.assertIn("estimate", r.json())
        self.assertIn("summary", r.json()["estimate"])

    def test_auth_failure_returns_200_with_reason(self):
        """認證失敗不是「請求錯了」而是「這次做不成」，前端要據 reason 給指引。"""
        self.runner.result = {
            "available": False,
            "dry_run": False,
            "reason": "尚未認證或 token 已過期，請重新執行一次性認證指令",
            "days": 7,
        }
        r = self.client().post("/api/sync", json={})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["available"])
        self.assertIn("認證", r.json()["reason"])

    def test_rate_limited_flag_is_passed_through(self):
        """撞到 Garmin 的 IP 層限制時前端必須知道「不可重試」。"""
        self.runner.result = {
            "available": False,
            "rate_limited": True,
            "reason": "已觸發 Garmin 的請求限制，請稍後再試",
            "days": 7,
        }
        r = self.client().post("/api/sync", json={})
        self.assertTrue(r.json()["rate_limited"])

    def test_gap_truncated_flag_is_passed_through(self):
        self.runner.result = {**self.runner.result, "gap_truncated": True}
        r = self.client().post("/api/sync", json={})
        self.assertTrue(r.json()["gap_truncated"])


class TestNoCredentialLeak(SyncRouteTestCase):
    """CLAUDE.md 憑證規範第 3 條：回傳值與錯誤訊息不得含 token／cookie／email。"""

    def test_response_has_no_credential_shaped_keys(self):
        r = self.client().post("/api/sync", json={"dry_run": True})
        body = r.text.lower()
        for forbidden in ("token", "cookie", "password", "oauth", "userprofileid", "@"):
            self.assertNotIn(forbidden, body, f"回傳值不該出現 {forbidden}")

    def test_forbidden_detail_names_only_the_env_var(self):
        """403 的訊息要能指引使用者，但不能透露任何憑證或內部位址。"""
        r = self.client(LAN_CLIENT).post("/api/sync", json={})
        detail = r.json()["detail"].lower()
        for forbidden in ("token", "cookie", "password", "@"):
            self.assertNotIn(forbidden, detail)


class TestChatOriginRuleUnchanged(unittest.TestCase):
    """把來源判斷抽成 client_access 後，聊天端點的行為必須完全不變。

    這支測試的存在是因為重構動到了既有的安全邊界——重構不該改變行為，
    但「不該」需要有東西擋著。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db_path = Path(self._tmp.name) / "test.db"
        build_fixture_db(db_path).close()
        assets_dir = Path(self._tmp.name) / "assets"
        assets_dir.mkdir()
        self.app = create_app(
            str(db_path),
            assets_dir=assets_dir,
            chat_config=coach_chat.ChatConfig(api_key_present=True),
        )

    def test_chat_still_rejects_lan_by_default(self):
        r = TestClient(self.app, client=LAN_CLIENT).get("/api/chat/conversations")
        self.assertEqual(r.status_code, 403)
        self.assertIn("RC_CHAT_ALLOW_PRIVATE_NETWORK", r.json()["detail"])

    def test_chat_still_allows_localhost(self):
        r = TestClient(self.app, client=LOCAL_CLIENT).get("/api/chat/conversations")
        self.assertEqual(r.status_code, 200)

    def test_sync_and_chat_flags_are_independent(self):
        """設了聊天的放行旗標不該連帶把同步也對區網開放。"""
        chat_cfg = coach_chat.ChatConfig(api_key_present=True, allow_private_network=True)
        app = create_app(
            str(Path(self._tmp.name) / "test.db"),
            assets_dir=Path(self._tmp.name) / "assets",
            chat_config=chat_cfg,
        )
        app.dependency_overrides[routes_sync.get_sync_runner] = lambda: FakeRunner()
        client = TestClient(app, client=LAN_CLIENT)
        self.assertEqual(client.get("/api/chat/conversations").status_code, 200)
        self.assertEqual(client.post("/api/sync", json={}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
