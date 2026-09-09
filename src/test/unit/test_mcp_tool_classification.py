"""mcp/server.py 的工具分類（READ_ONLY_TOOL_NAMES／WRITE_TOOL_NAMES）測試。

這支測試存在的唯一理由是防「新增一個 tool 卻忘記分類」：AI 教練面板只把
READ_ONLY 的工具餵給模型，漏分類的新 tool 會靜悄悄地從聊天消失（唯讀的）
或靜悄悄地被開放（寫入的）。斷言 list_tools() 的名稱集合恰好等於兩者的
聯集、且兩者不相交，任何遺漏都會在這裡變紅。
"""

import asyncio
import unittest

from src.main.python.mcp.server import READ_ONLY_TOOL_NAMES, WRITE_TOOL_NAMES, mcp


class TestToolClassification(unittest.TestCase):
    def setUp(self):
        self.registered = {tool.name for tool in asyncio.run(mcp.list_tools())}

    def test_every_registered_tool_is_classified(self):
        self.assertEqual(
            self.registered,
            READ_ONLY_TOOL_NAMES | WRITE_TOOL_NAMES,
            "有 tool 沒被分類到 READ_ONLY_TOOL_NAMES／WRITE_TOOL_NAMES，"
            "或清單裡列了不存在的 tool 名稱",
        )

    def test_classifications_do_not_overlap(self):
        self.assertEqual(
            READ_ONLY_TOOL_NAMES & WRITE_TOOL_NAMES,
            frozenset(),
            "同一個 tool 不可能既唯讀又寫入",
        )

    def test_known_write_tools_are_not_read_only(self):
        """四個寫入型 tool 是硬約束的具體內容，逐一釘住（見 D.1）。"""
        for name in ("sync_garmin_data", "generate_training_plan", "add_context_note",
                     "save_external_workouts"):
            with self.subTest(tool=name):
                self.assertIn(name, WRITE_TOOL_NAMES)
                self.assertNotIn(name, READ_ONLY_TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()
