"""race_names 的單元測試——用合成的學員檔，不含任何真實個人資料。"""

import tempfile
import unittest
from pathlib import Path

from src.main.python.services.race_names import is_personal_test, load_race_names

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


class IsPersonalTestTest(unittest.TestCase):
    def test_test_keywords(self):
        self.assertTrue(is_personal_test("跑班 3K 速度測試"))
        self.assertTrue(is_personal_test("5K 計時測驗"))

    def test_regular_race(self):
        self.assertFalse(is_personal_test("台北馬半程"))
        self.assertFalse(is_personal_test(None))


if __name__ == "__main__":
    unittest.main()
