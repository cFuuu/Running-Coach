"""fit_import_runner 的單元測試——全部使用合成假資料，不含任何真實個人資料。

match_activity() 直接測（純 DB 邏輯，不需要真的 FIT 檔）。import_fit_files() 的
sport 過濾/分類 gating 邏輯則用 unittest.mock 隔離 fit_parser.parse_fit（建構合法
的 FIT 二進位檔過於複雜，且不是這裡要測的重點——重點是 import_fit_files 本身
「該不該匯入、該不該分類」的判斷邏輯）。
"""

import datetime
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from src.main.python.models.db import get_connection
from src.main.python.services import fit_import_runner as runner


def _insert_activity(conn, athlete_id, started_at, distance_km, activity_type="running"):
    conn.execute(
        """
        INSERT INTO activities (athlete_id, activity_type, started_at, distance_km, source, fetched_at)
        VALUES (?, ?, ?, ?, 'garmin_export', '2026-01-01T00:00:00')
        """,
        (athlete_id, activity_type, started_at, distance_km),
    )
    conn.commit()
    return conn.execute("SELECT id FROM activities WHERE started_at = ?", (started_at,)).fetchone()["id"]


class TestMatchActivity(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.conn.execute(
            "INSERT INTO athlete_profile (name, updated_at) VALUES ('Test', '2026-01-01T00:00:00')"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_matches_via_utc_to_local_offset_and_distance(self):
        """實測驗證過的情境：FIT 存 UTC，DB 存當地時間（此例 +8 小時偏移）。"""
        aid = _insert_activity(self.conn, 1, "2021-12-19T07:03:36", 21.1639)
        matched = runner.match_activity(
            self.conn, datetime.datetime(2021, 12, 18, 23, 3, 36), 21163.94
        )
        self.assertEqual(matched, aid)

    def test_no_match_when_distance_differs_too_much(self):
        _insert_activity(self.conn, 1, "2021-12-19T07:03:36", 21.1639)
        matched = runner.match_activity(
            self.conn, datetime.datetime(2021, 12, 18, 23, 3, 36), 5000.0
        )
        self.assertIsNone(matched)

    def test_no_match_when_time_offset_exceeds_max_tz_hours(self):
        _insert_activity(self.conn, 1, "2021-12-19T07:03:36", 21.1639)
        # 時間差 20 小時，超過 MAX_TZ_OFFSET_HOURS=14，即使距離完全吻合也不該配對
        matched = runner.match_activity(
            self.conn, datetime.datetime(2021, 12, 17, 11, 3, 36), 21163.94
        )
        self.assertIsNone(matched)

    def test_picks_closest_distance_when_multiple_candidates_in_time_window(self):
        _insert_activity(self.conn, 1, "2021-12-19T05:00:00", 10.00)
        near = _insert_activity(self.conn, 1, "2021-12-19T09:00:00", 10.05)
        matched = runner.match_activity(
            self.conn, datetime.datetime(2021, 12, 18, 21, 0, 0), 10050.0
        )
        self.assertEqual(matched, near)

    def test_returns_none_without_start_time_or_distance(self):
        self.assertIsNone(runner.match_activity(self.conn, None, 5000.0))
        self.assertIsNone(
            runner.match_activity(self.conn, datetime.datetime(2021, 1, 1), None)
        )


def _fake_parsed(sport: str, start_local_iso: str, distance_m: float, n_laps: int = 4) -> dict:
    """建構 fit_parser.parse_fit() 應回傳的資料結構，繞過真實 FIT 二進位解析。"""
    start_utc = datetime.datetime.fromisoformat(start_local_iso) - datetime.timedelta(hours=8)
    laps = [
        {
            "lap_index": i + 1,
            "distance_km": 1.0,
            "duration_sec": 360,
            "pace_sec_per_km": 360,
            "avg_hr_bpm": 150,
            "max_hr_bpm": 160,
        }
        for i in range(n_laps)
    ]
    records = [
        {"elapsed_sec": t, "distance_km": t / 360, "hr_bpm": 150, "pace_sec_per_km": 360, "cadence_spm": 170, "altitude_m": 10.0}
        for t in range(0, 60, 5)
    ]
    return {
        "session": {
            "start_time_utc": start_utc,
            "total_distance_m": distance_m,
            "total_elapsed_sec": n_laps * 360,
            "sport": sport,
            "sub_sport": "generic",
        },
        "laps": laps,
        "records": records,
    }


def _build_zip_with_fit_placeholders(tmp: Path, names: list[str]) -> None:
    """建立一個含指定檔名（內容為填充位元組）的 zip，供 import_fit_files 掃描。

    內容本身不重要，因為測試會 mock 掉真正的二進位解析。
    """
    zip_dir = tmp / "export" / "DI_CONNECT" / "DI-Connect-Uploaded-Files"
    zip_dir.mkdir(parents=True)
    with zipfile.ZipFile(zip_dir / "part1.zip", "w") as z:
        for name in names:
            z.writestr(name, b"0" * 5000)  # 超過 min_file_bytes 預設門檻


class TestImportFitFilesSportFiltering(unittest.TestCase):
    """驗證運動類型不是寫死的：import_sports / classifiable_sports 兩個參數各自生效。"""

    def setUp(self):
        self.conn = get_connection(":memory:")
        self.conn.execute(
            "INSERT INTO athlete_profile (name, updated_at) VALUES ('Test', '2026-01-01T00:00:00')"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_default_only_imports_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_id = _insert_activity(self.conn, 1, "2026-01-05T07:00:00", 10.0)
            bike_id = _insert_activity(self.conn, 1, "2026-01-06T07:00:00", 30.0, activity_type="cycling")
            _build_zip_with_fit_placeholders(tmp_path, ["a_1.fit", "a_2.fit"])

            parsed_by_name = {
                "a_1.fit": _fake_parsed("running", "2026-01-05T07:00:00", 10000.0),
                "a_2.fit": _fake_parsed("cycling", "2026-01-06T07:00:00", 30000.0),
            }
            stats = self._import_with_name_tracking(tmp_path, parsed_by_name)

            self.assertEqual(stats["imported"], 1)
            self.assertEqual(stats["sport_excluded"], 1)
            laps = self.conn.execute("SELECT COUNT(*) FROM activity_laps WHERE activity_id=?", (run_id,)).fetchone()[0]
            self.assertEqual(laps, 4)
            bike_laps = self.conn.execute("SELECT COUNT(*) FROM activity_laps WHERE activity_id=?", (bike_id,)).fetchone()[0]
            self.assertEqual(bike_laps, 0)

    def test_expanding_import_sports_allows_other_sports(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bike_id = _insert_activity(self.conn, 1, "2026-01-06T07:00:00", 30.0, activity_type="cycling")
            _build_zip_with_fit_placeholders(tmp_path, ["a_2.fit"])
            parsed_by_name = {"a_2.fit": _fake_parsed("cycling", "2026-01-06T07:00:00", 30000.0)}

            stats = self._import_with_name_tracking(
                tmp_path, parsed_by_name, import_sports=frozenset({"cycling"}), classifiable_sports=frozenset()
            )

            self.assertEqual(stats["imported"], 1)
            laps = self.conn.execute("SELECT COUNT(*) FROM activity_laps WHERE activity_id=?", (bike_id,)).fetchone()[0]
            self.assertEqual(laps, 4)
            # classifiable_sports 是空集合 -> 不應寫入 workout_type
            row = self.conn.execute("SELECT workout_type FROM activities WHERE id=?", (bike_id,)).fetchone()
            self.assertIsNone(row["workout_type"])

    def test_imported_but_not_classifiable_skips_classification(self):
        """匯入分圈/逐秒資料，但該運動不在 classifiable_sports -> 不寫 workout_type。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            swim_id = _insert_activity(self.conn, 1, "2026-01-07T07:00:00", 1.5, activity_type="lap_swimming")
            _build_zip_with_fit_placeholders(tmp_path, ["a_3.fit"])
            parsed_by_name = {"a_3.fit": _fake_parsed("swimming", "2026-01-07T07:00:00", 1500.0)}

            self._import_with_name_tracking(
                tmp_path,
                parsed_by_name,
                import_sports=frozenset({"swimming"}),
                classifiable_sports=frozenset(),  # 明確不分類
            )
            row = self.conn.execute("SELECT workout_type FROM activities WHERE id=?", (swim_id,)).fetchone()
            self.assertIsNone(row["workout_type"])
            laps = self.conn.execute("SELECT COUNT(*) FROM activity_laps WHERE activity_id=?", (swim_id,)).fetchone()[0]
            self.assertEqual(laps, 4)  # 分圈資料仍照存

    def _import_with_name_tracking(self, tmp_path, parsed_by_name, **kwargs):
        """patch zipfile 讀取以追蹤目前檔名，讓 mock 的 parse_fit 能依檔名回傳對應資料。"""
        import src.main.python.services.fit_import_runner as runner_module

        original_read = zipfile.ZipFile.read

        def tracking_read(self_zip, name, *a, **kw):
            _current_name[0] = name
            return original_read(self_zip, name, *a, **kw)

        with patch.object(zipfile.ZipFile, "read", tracking_read), patch.object(
            runner_module.fit_parser, "parse_fit", side_effect=lambda data: parsed_by_name[_current_name[0]]
        ):
            return runner_module.import_fit_files(self.conn, tmp_path, **kwargs)


_current_name = [None]  # 供 mock 的 parse_fit 得知目前正在處理哪個檔名


if __name__ == "__main__":
    unittest.main()
