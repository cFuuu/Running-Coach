"""scripts/build_dashboard_offline.py 的測試：fixture DB → 單一離線 HTML。

全部使用 fixtures.build_fixture_db() 的合成假資料；植入的 token／email 也都是假的。
"""

import base64
import datetime
import gzip
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.test.unit.fixtures import FAKE_USER_ID, build_fixture_db

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "build_dashboard_offline.py"
_spec = importlib.util.spec_from_file_location("build_dashboard_offline", _SCRIPT)
offline = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = offline  # dataclass 需要模組已註冊
_spec.loader.exec_module(offline)

FIXTURE_TODAY = datetime.date(2026, 3, 10)
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlLXVzZXIifQ.ZmFrZS1zaWduYXR1cmU"
FAKE_EMAIL = "planted.runner@example.org"


def _read_snapshot(html: str) -> dict:
    gz = re.search(r'window\.__SNAPSHOT_GZ__ = "([^"]+)";', html)
    if gz:
        return json.loads(gzip.decompress(base64.b64decode(gz.group(1))).decode("utf-8"))
    plain = re.search(r"window\.__SNAPSHOT__ = (.*?);</script>", html, re.S)
    return json.loads(plain.group(1).replace("<\\/", "</"))


class BuildDashboardOfflineTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "fixture.db"
        build_fixture_db(self.db_path).close()
        self.out = self.tmp / "out" / "dashboard_offline.html"

    def build(self, **kwargs) -> str:
        kwargs.setdefault("today", FIXTURE_TODAY)
        offline.build(self.db_path, self.out, **kwargs)
        return self.out.read_text(encoding="utf-8")

    def plant_title(self, title: str) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE activities SET title = ? WHERE id = 1", (title,))
        conn.commit()
        conn.close()


class TestOutput(BuildDashboardOfflineTestCase):
    def test_gzip_snapshot_has_expected_keys(self):
        html = self.build()
        self.assertNotIn("window.__SNAPSHOT__ =", html)
        self.assertIn("window.__SNAPSHOT_AT__ = ", html)
        snap = _read_snapshot(html)
        for key in ("generated_at", "exact", "calendar", "feels", "detail_since"):
            self.assertIn(key, snap)
        exact = snap["exact"]
        for key in ("/api/meta", "/api/today", "/api/overview?range=30d",
                    "/api/training-load?range=30d", "/api/garmin-cards?range=30d"):
            self.assertIn(key, exact)
        for range_key in ("7d", "30d", "90d", "1y", "all"):
            for endpoint in offline.RANGE_ENDPOINTS:
                self.assertIn(f"{endpoint}?range={range_key}", exact)
        # 全部歷史：每場活動都有明細（含非跑步），逐秒資料改成欄式
        detail_keys = [k for k in exact if re.fullmatch(r"/api/sessions/\d+", k)]
        self.assertEqual(len(detail_keys), 5)
        points = exact["/api/sessions/1"]["records"]["points"]
        self.assertEqual(set(points), {"cols", "rows"})
        self.assertIn("hr_bpm", points["cols"])
        # 行事曆涵蓋最早活動（2026-01-15）所在月曆格起點到今天所在月曆格
        days = snap["calendar"]["days"]
        self.assertLessEqual(days[0]["date"], "2026-01-01")
        self.assertGreaterEqual(days[-1]["date"], "2026-03-31")

    def test_no_gzip_writes_plain_snapshot(self):
        html = self.build(use_gzip=False)
        self.assertNotIn("__SNAPSHOT_GZ__ =", html)
        self.assertIn("/api/meta", _read_snapshot(html)["exact"])

    def test_page_is_self_contained(self):
        html = self.build()
        self.assertNotIn('<link rel="stylesheet"', html)
        self.assertNotIn('src="js/', html)
        self.assertIn('<script type="importmap">', html)
        imports = json.loads(re.search(r'<script type="importmap">(.*?)</script>', html, re.S).group(1))["imports"]
        self.assertIn("@rc/js/main.js", imports)
        self.assertIn("@rc/js/core/api.js", imports)
        main_src = base64.b64decode(imports["@rc/js/main.js"].split(",", 1)[1]).decode("utf-8")
        self.assertNotIn("from './", main_src)
        self.assertIn("from '@rc/js/core/api.js'", main_src)

    def test_detail_months_drops_older_details(self):
        html = self.build(detail_months=1)  # cutoff 2026-02-10
        snap = _read_snapshot(html)
        self.assertEqual(snap["detail_since"], "2026-02-10")
        sessions = snap["exact"]["/api/sessions?range=all"]["sessions"]
        old = [s["id"] for s in sessions if s["date"] < "2026-02-10"]
        recent = [s["id"] for s in sessions if s["date"] >= "2026-02-10"]
        self.assertTrue(old and recent)
        for sid in old:
            self.assertNotIn(f"/api/sessions/{sid}", snap["exact"])
        for sid in recent:
            self.assertIn(f"/api/sessions/{sid}", snap["exact"])
        # 清單摘要仍保留完整歷史
        self.assertIn(old[0], [s["id"] for s in sessions])

    def test_db_is_not_modified(self):
        def fingerprint():
            return hashlib.sha256(self.db_path.read_bytes()).hexdigest(), self.db_path.stat().st_mtime_ns

        before = fingerprint()
        self.build()
        self.assertEqual(fingerprint(), before)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["fixture.db", "out"])


class TestSecurityScan(BuildDashboardOfflineTestCase):
    def assert_aborts(self, secret: str) -> None:
        with self.assertRaises(offline.SecurityScanError) as ctx:
            self.build()
        self.assertNotIn(secret, str(ctx.exception))
        self.assertFalse(self.out.exists())

    def test_planted_token_aborts(self):
        self.plant_title(f"晨跑 {FAKE_JWT}")
        self.assert_aborts(FAKE_JWT)

    def test_planted_email_aborts(self):
        self.plant_title(f"晨跑 {FAKE_EMAIL}")
        self.assert_aborts(FAKE_EMAIL)

    def test_identity_ref_from_db_aborts(self):
        self.plant_title(f"晨跑 {FAKE_USER_ID}")
        self.assert_aborts(FAKE_USER_ID)

    def test_cli_exits_non_zero_without_echoing(self):
        self.plant_title(f"晨跑 {FAKE_JWT}")
        with mock.patch("sys.stderr") as err:
            code = offline.main(["--db-path", str(self.db_path), "--out", str(self.out)])
        self.assertEqual(code, 2)
        written = "".join(str(c.args[0]) for c in err.write.call_args_list)
        self.assertIn("資安掃描未通過", written)
        self.assertNotIn(FAKE_JWT, written)

    def test_rules(self):
        cases = {
            "userProfileId 欄位": ('{"userProfileId":1}', ""),
            "displayName": ('{"x":"displayName"}', ""),
            "token 欄位": ('{"oauth_token":"x"}', ""),
            "cookie 欄位": ('{"cookies":[]}', ""),
            "Bearer": ("", "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"),
            "cookie 字串": ("", "JWT_FGP=abc"),
            "長 token": ('{"v":"a1b2c3d4e5f6a7b8c9d0a1b2c3d4e5f6a7b8c9d0ab"}', ""),
            "email 在程式碼": ("{}", "// contact someone@example.com"),
        }
        for name, (data, page) in cases.items():
            with self.subTest(name), self.assertRaises(offline.SecurityScanError):
                offline.security_scan(data, page)
        # 一般資料不誤判
        offline.security_scan('{"title":"測試晨跑","token_count":null,"pace":"5:00"}', "@media (x) {}")


class TestCli(BuildDashboardOfflineTestCase):
    def test_copy_to_from_env(self):
        dest = self.tmp / "icloud"
        dest.mkdir()
        with mock.patch.dict(os.environ, {offline.COPY_TO_ENV: str(dest)}), mock.patch("builtins.print"):
            code = offline.main(["--db-path", str(self.db_path), "--out", str(self.out)])
        self.assertEqual(code, 0)
        self.assertEqual((dest / self.out.name).read_bytes(), self.out.read_bytes())

    def test_txt_copy_alongside_html(self):
        dest = self.tmp / "icloud"
        dest.mkdir()
        with mock.patch("builtins.print"):
            code = offline.main(["--db-path", str(self.db_path), "--out", str(self.out),
                                 "--copy-to", str(dest), "--txt"])
        self.assertEqual(code, 0)
        html = self.out.read_bytes()
        self.assertEqual(self.out.with_suffix(".txt").read_bytes(), html)
        self.assertEqual((dest / self.out.name).read_bytes(), html)
        self.assertEqual((dest / self.out.with_suffix(".txt").name).read_bytes(), html)

    def test_no_copy_when_env_unset(self):
        env = {k: v for k, v in os.environ.items() if k != offline.COPY_TO_ENV}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("builtins.print"):
            self.assertEqual(offline.main(["--db-path", str(self.db_path), "--out", str(self.out)]), 0)
        self.assertEqual(sorted(p.name for p in self.out.parent.iterdir()), [self.out.name])

    def test_offline_mode_hooks(self):
        """W4b：「今天」＝匯出日期、臨時浮動徽章已移除、線上功能有 data-online-only 標記。"""
        html = self.build()
        self.assertIn(f'window.__SNAPSHOT_TODAY__ = "{FIXTURE_TODAY.isoformat()}";', html)
        self.assertIn("today: window.__SNAPSHOT_TODAY__", html)
        self.assertNotIn("offline-snapshot-badge", html)
        for element_id in ("sync-btn", "analyze-btn", "coach-toggle", "custom-range", "coach-panel",
                           "coach-rail", "coach-scrim"):
            tag = re.search(r'<[^>]*\bid="' + element_id + r'"[^>]*>', html).group(0)
            self.assertIn("data-online-only", tag, element_id)
        self.assertIn('id="offline-stamp"', html)

    def test_previous_period_requests_in_snapshot(self):
        """趨勢頁前期比較：每個有起日的 range 都有前一段等長期間的 custom: 回應。"""
        exact = _read_snapshot(self.build())["exact"]
        expected = 0
        for range_key in ("7d", "30d", "90d", "1y", "all"):
            win = exact[f"/api/wellness-trend?range={range_key}"]
            prev = offline.previous_range(win.get("start_date"), win["end_date"])
            if prev is None:
                continue
            expected += 1
            for endpoint in offline.PREV_ENDPOINTS:
                self.assertIn(f"{endpoint}?range={prev}", exact)
        self.assertGreaterEqual(expected, 4)


class TestHelpers(unittest.TestCase):
    def test_canonical_key_drops_athlete_id_and_decodes(self):
        self.assertEqual(
            offline.canonical_key("/api/overview?range=custom%3A2026-01-01%3A2026-02-01&athlete_id=3"),
            "/api/overview?range=custom:2026-01-01:2026-02-01",
        )
        self.assertEqual(offline.canonical_key("/api/meta?athlete_id=1"), "/api/meta")

    def test_snapshot_requests_dedupes(self):
        ctx = offline.SnapshotContext(FIXTURE_TODAY, ("30d",), (7,), FIXTURE_TODAY, FIXTURE_TODAY)
        urls = offline.snapshot_requests(ctx)
        self.assertEqual(len(urls), len(set(urls)))
        self.assertIn("/api/sessions/7", urls)

    def test_previous_range_and_prev_requests(self):
        # 30 天（9/24–10/23）的前一段等長期間是 8/25–9/23；range=all 沒有起日 → 無前期
        self.assertEqual(offline.previous_range("2026-09-24", "2026-10-23"), "custom:2026-08-25:2026-09-23")
        self.assertIsNone(offline.previous_range(None, "2026-10-23"))
        ctx = offline.SnapshotContext(FIXTURE_TODAY, ("30d",), (), FIXTURE_TODAY, FIXTURE_TODAY,
                                      ("custom:2026-08-25:2026-09-23",))
        urls = offline.snapshot_requests(ctx)
        for endpoint in offline.PREV_ENDPOINTS:
            self.assertIn(f"{endpoint}?range=custom:2026-08-25:2026-09-23", urls)

    def test_month_grid(self):
        # 2026-03：1 號是週日、31 號是週二 → 3/1 ~ 4/4
        self.assertEqual(offline._month_grid(datetime.date(2026, 3, 15)),
                         (datetime.date(2026, 3, 1), datetime.date(2026, 4, 4)))
        self.assertEqual(offline._months_back(datetime.date(2026, 3, 31), 1), datetime.date(2026, 2, 28))


if __name__ == "__main__":
    unittest.main()
