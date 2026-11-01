"""client_access 的 Host／Origin 守門（LocalRequestGuard）測試。

對應 2026-11-01 資安稽核 NV-1（跨站請求觸發同步／分析）與 NV-2（DNS rebinding）：
來源位址都是 127.0.0.1，peer-IP 判斷分不出惡意網頁，必須靠 Host 與 Origin。

**絕不呼叫真實的 Garmin API**：sync runner 一律換成假的。
"""

import logging
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.main.python.api import client_access, routes_sync
from src.main.python.api.app import create_app
from src.main.python.services import coach_chat
from src.test.unit.fixtures import build_fixture_db

LOCAL_CLIENT = ("127.0.0.1", 1)

for _noisy in ("httpx", "httpx2"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


class TestHostAllowed(unittest.TestCase):
    def test_ip_literals_and_localhost_allowed(self):
        for host in ("127.0.0.1:8000", "192.168.1.5:8000", "100.101.102.103", "[::1]:8000",
                     "localhost:8000", "LOCALHOST"):
            with self.subTest(host=host):
                self.assertTrue(client_access.is_host_allowed(host))

    def test_domain_names_rejected(self):
        """DNS rebinding 一定經過網域名稱。"""
        for host in ("attacker.example:8000", "127.0.0.1.nip.io", "localhost.attacker.example"):
            with self.subTest(host=host):
                self.assertFalse(client_access.is_host_allowed(host))

    def test_missing_host_rejected(self):
        self.assertFalse(client_access.is_host_allowed(None))
        self.assertFalse(client_access.is_host_allowed(""))

    def test_extra_names_allowed(self):
        self.assertTrue(client_access.is_host_allowed("mypc.tailnet.ts.net:8000",
                                                      {"mypc.tailnet.ts.net"}))


class TestCrossSiteWrite(unittest.TestCase):
    def test_same_origin_write_allowed(self):
        self.assertFalse(client_access.is_cross_site_write(
            "POST", "127.0.0.1:8000", "http://127.0.0.1:8000", "same-origin"))

    def test_foreign_origin_write_rejected(self):
        """localhost:9000 → localhost:8000 是 same-site，但不同源，仍要擋。"""
        self.assertTrue(client_access.is_cross_site_write(
            "POST", "localhost:8000", "http://localhost:9000", "same-site"))

    def test_null_origin_rejected(self):
        self.assertTrue(client_access.is_cross_site_write("POST", "127.0.0.1:8000", "null", None))

    def test_cross_site_fetch_metadata_rejected_without_origin(self):
        self.assertTrue(client_access.is_cross_site_write("PUT", "127.0.0.1:8000", None, "cross-site"))

    def test_non_browser_client_without_origin_allowed(self):
        self.assertFalse(client_access.is_cross_site_write("POST", "127.0.0.1:8000", None, None))

    def test_safe_methods_never_blocked(self):
        self.assertFalse(client_access.is_cross_site_write(
            "GET", "127.0.0.1:8000", "http://evil.example", "cross-site"))


class GuardedAppTestCase(unittest.TestCase):
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
            chat_config=coach_chat.ChatConfig(api_key_present=False),
            allowed_host_names=(),
        )
        self.sync_calls = []
        self.app.dependency_overrides[routes_sync.get_sync_runner] = lambda: (
            lambda conn, athlete_id, days=None, dry_run=False: self.sync_calls.append(dry_run) or {
                "available": True, "dry_run": dry_run})

    def client(self, base_url="http://127.0.0.1:8000"):
        return TestClient(self.app, base_url=base_url, client=LOCAL_CLIENT)


class TestGuardOnApp(GuardedAppTestCase):
    def test_rebinding_host_cannot_read(self):
        r = self.client("http://attacker.example:8000").get("/api/chat/conversations")
        self.assertEqual(r.status_code, 400)
        self.assertIn("RC_ALLOWED_HOSTS", r.json()["detail"])

    def test_rebinding_host_cannot_sync(self):
        r = self.client("http://attacker.example:8000").post("/api/sync", json={"dry_run": True})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.sync_calls, [])

    def test_cross_site_empty_form_cannot_sync(self):
        """NV-1：跨站空表單原本會觸發真的同步（dry_run=False）。"""
        r = self.client().post(
            "/api/sync",
            headers={"Origin": "http://evil.example",
                     "Content-Type": "application/x-www-form-urlencoded"},
            content=b"",
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.sync_calls, [])

    def test_cross_site_cannot_start_analysis(self):
        r = self.client().post("/api/analysis", headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_same_origin_dashboard_sync_still_works(self):
        r = self.client().post("/api/sync", json={},
                               headers={"Origin": "http://127.0.0.1:8000"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.sync_calls, [False])

    def test_lan_ip_host_still_reads(self):
        """手機用區網 IP 連進來看 dashboard 不受影響。"""
        r = self.client("http://192.168.1.5:8000").get("/api/meta")
        self.assertEqual(r.status_code, 200)

    def test_configured_host_name_allowed(self):
        app = create_app(
            str(Path(self._tmp.name) / "test.db"),
            assets_dir=Path(self._tmp.name) / "assets",
            chat_config=coach_chat.ChatConfig(api_key_present=False),
            allowed_host_names=("mypc.tailnet.ts.net",),
        )
        r = TestClient(app, base_url="http://mypc.tailnet.ts.net:8000",
                       client=LOCAL_CLIENT).get("/api/meta")
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
