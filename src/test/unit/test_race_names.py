"""race_names 的單元測試——用合成的學員檔，不含任何真實個人資料。"""

import datetime
import tempfile
import unittest
from pathlib import Path

from src.main.python.services.race_names import (
    is_personal_test,
    load_race_names,
    load_race_schedule,
    race_countdown,
)

_PROFILE = """# 學員檔

## 目標

| 項目 | 內容 |
|---|---|
| 日期 | 名稱 |

### 比賽成績

說明文字。

| 賽事 | 日期 | 名稱 | 時間 |
|---|---|---|---|
| 半馬 | 2025-12-21 | 城市馬拉松 | 1:57:27 |
| **10K** | **2025-11-23** | **城市 10K（PB）** | 52:30 |
| 3K | 2026-10-08 | 跑班 3K 速度測試 | 13:59² |
| 5K | 不是日期 | 應略過 | 25:00 |

² 註腳

| 日期 | 名稱 |
|---|---|
| 2020-01-01 | 下一張表不該讀到 |

### 首馬注意事項

| 日期 | 名稱 |
|---|---|
| 2021-01-01 | 別的小節 |
"""


class LoadRaceNamesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "athlete_profile.md"
        self.path.write_text(_PROFILE, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_only_the_race_table(self):
        self.assertEqual(
            load_race_names(self.path),
            {
                "2025-12-21": "城市馬拉松",
                "2025-11-23": "城市 10K（PB）",
                "2026-10-08": "跑班 3K 速度測試",
            },
        )

    def test_missing_file_returns_empty(self):
        self.assertEqual(load_race_names(Path(self.tmp.name) / "nope.md"), {})
        self.assertEqual(load_race_names(None), {})

    def test_file_without_race_section_returns_empty(self):
        self.path.write_text("# 學員檔\n\n| 日期 | 名稱 |\n|---|---|\n| 2025-01-01 | x |\n", encoding="utf-8")
        self.assertEqual(load_race_names(self.path), {})


_SCHEDULE_PROFILE = """# 學員檔

## 目標賽事

| 項目 | 值 |
|---|---|
| 賽事 | 範例馬拉松（全程）|
| 日期 | 2026-12-20（日）|
| 主要目標 | sub-4:30 |

### 賽程（未來賽事）

| 日期 | 名稱 |
|---|---|
| 2026-11-15 | 範例 10K |
| 不是日期 | 應略過 |
| 2026-13-45 | 無效日期應略過 |

### 比賽成績

| 賽事 | 日期 | 名稱 |
|---|---|---|
| 半馬 | 2025-12-21 | 已完成的賽事 |
"""


class LoadRaceScheduleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "athlete_profile.md"
        self.path.write_text(_SCHEDULE_PROFILE, encoding="utf-8")

    def test_reads_target_and_upcoming(self):
        self.assertEqual(
            load_race_schedule(self.path),
            {
                "target": {
                    "name": "範例馬拉松（全程）", "distance": None, "distance_km": None,
                    "date": "2026-12-20",
                },
                "upcoming": [
                    {"name": "範例", "distance": "10K", "distance_km": 10.0, "date": "2026-11-15"}
                ],
            },
        )

    def test_trailing_distance_is_split_from_name(self):
        self.path.write_text(
            "## 目標賽事\n\n| 項目 | 值 |\n|---|---|\n| 賽事 | 2026 範例馬拉松 42K |\n| 日期 | 2026-12-20 |\n\n"
            "### 賽程\n\n| 日期 | 名稱 |\n|---|---|\n"
            "| 2026-11-08 | 範例盃 21.0975 k |\n| 2026-11-09 | 10K 路跑 |\n",
            encoding="utf-8",
        )
        schedule = load_race_schedule(self.path)
        self.assertEqual(schedule["target"]["name"], "2026 範例馬拉松")
        self.assertEqual(schedule["target"]["distance"], "42K")
        self.assertEqual(schedule["target"]["distance_km"], 42.0)
        self.assertEqual(schedule["upcoming"][0]["distance"], "21.0975K")
        self.assertEqual(schedule["upcoming"][0]["distance_km"], 21.0975)
        # 距離不在結尾（或名稱只剩距離）時不拆，保留原名稱
        self.assertEqual(
            schedule["upcoming"][1],
            {"name": "10K 路跑", "distance": None, "distance_km": None, "date": "2026-11-09"},
        )

    def test_race_results_table_does_not_leak_into_upcoming(self):
        names = [r["name"] for r in load_race_schedule(self.path)["upcoming"]]
        self.assertNotIn("已完成的賽事", names)

    def test_missing_file_or_sections_returns_empty_schedule(self):
        empty = {"target": None, "upcoming": []}
        self.assertEqual(load_race_schedule(Path(self.tmp.name) / "nope.md"), empty)
        self.assertEqual(load_race_schedule(None), empty)
        self.path.write_text("# 學員檔\n", encoding="utf-8")
        self.assertEqual(load_race_schedule(self.path), empty)


class RaceCountdownTest(unittest.TestCase):
    TARGET = {"name": "目標馬", "date": "2026-12-20"}
    TODAY = datetime.date(2026, 10, 21)

    def test_next_is_nearest_upcoming_and_target_is_separate(self):
        schedule = {"target": self.TARGET, "upcoming": [{"name": "暖身賽", "date": "2026-11-15"}]}
        result = race_countdown(schedule, self.TODAY)
        self.assertEqual(result["next"]["name"], "暖身賽")
        self.assertEqual(result["next"]["days_left"], 25)
        self.assertFalse(result["next"]["is_target"])
        self.assertEqual(result["target"]["days_left"], 60)
        self.assertTrue(result["target"]["is_target"])

    def test_next_is_target_when_no_other_race(self):
        result = race_countdown({"target": self.TARGET, "upcoming": []}, self.TODAY)
        self.assertEqual(result["next"], result["target"])
        self.assertTrue(result["next"]["is_target"])

    def test_race_day_is_zero_and_past_races_are_dropped(self):
        schedule = {"target": self.TARGET, "upcoming": [{"name": "已過", "date": "2026-10-01"}]}
        self.assertEqual(race_countdown(schedule, datetime.date(2026, 12, 20))["target"]["days_left"], 0)
        after = race_countdown(schedule, datetime.date(2026, 12, 21))
        self.assertIsNone(after["next"])
        self.assertIsNone(after["target"])

    def test_empty_schedule(self):
        self.assertEqual(
            race_countdown({"target": None, "upcoming": []}, self.TODAY),
            {"next": None, "target": None},
        )


class IsPersonalTestTest(unittest.TestCase):
    def test_test_keywords(self):
        self.assertTrue(is_personal_test("跑班 3K 速度測試"))
        self.assertTrue(is_personal_test("5K 計時測驗"))

    def test_regular_race(self):
        self.assertFalse(is_personal_test("台北馬半程"))
        self.assertFalse(is_personal_test(None))


if __name__ == "__main__":
    unittest.main()
