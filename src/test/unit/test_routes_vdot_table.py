"""`GET /api/vdot-table` 的路由測試（N-41）——回傳形狀、精確值列、錯誤轉 400。

表格內容（範圍、單調性、與 compute_pace_zones 一致）在 test_vdot_engine.py 的
TestVdotEquivalenceTable；這裡只確認路由把引擎輸出原樣回傳、參數錯誤轉成 400。
"""

import logging
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.main.python.api.app import create_app
from src.main.python.services import coach_chat
from src.main.python.services.vdot_engine import compute_pace_zones, vdot_equivalence_table
from src.test.unit.fixtures import build_fixture_db

for _noisy in ("httpx", "httpx2"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


class VdotTableRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db_path = Path(cls._tmp.name) / "test.db"
        build_fixture_db(db_path).close()
        assets_dir = Path(cls._tmp.name) / "assets"
        assets_dir.mkdir()
        app = create_app(
            str(db_path),
            assets_dir=assets_dir,
            chat_config=coach_chat.ChatConfig(api_key_present=False),
        )
        cls.client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def get(self, **params):
        return self.client.get("/api/vdot-table", params=params)

    def test_without_vdot_returns_full_table_and_no_current(self):
        r = self.get()
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body, vdot_equivalence_table())
        self.assertEqual(len(body["rows"]), 66)
        self.assertEqual((body["rows"][0]["vdot"], body["rows"][-1]["vdot"]), (20, 85))
        self.assertIsNone(body["current"])

    def test_exact_vdot_row(self):
        r = self.get(vdot="42.9")
        self.assertEqual(r.status_code, 200)
        current = r.json()["current"]
        self.assertEqual(current["vdot"], 42.9)
        self.assertEqual(current["paces"]["easy"], compute_pace_zones(42.9)["easy"])
        self.assertEqual(set(current["race_times_sec"]), {"3k", "5k", "10k", "half_marathon", "marathon"})

    def test_bounds_inclusive(self):
        for v in ("20", "85"):
            self.assertEqual(self.get(vdot=v).status_code, 200)

    def test_bad_vdot_is_400(self):
        for bad in ("abc", "19.9", "85.1", "nan", "inf", "-40"):
            r = self.get(vdot=bad)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("vdot", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
