"""mcp/server.py 的工具分類（READ_ONLY_TOOL_NAMES／WRITE_TOOL_NAMES）測試。

這支測試存在的唯一理由是防「新增一個 tool 卻忘記分類」：AI 教練面板只把
READ_ONLY 的工具餵給模型，漏分類的新 tool 會靜悄悄地從聊天消失（唯讀的）
或靜悄悄地被開放（寫入的）。斷言 list_tools() 的名稱集合恰好等於兩者的
聯集、且兩者不相交，任何遺漏都會在這裡變紅。
"""

import asyncio
import unittest

from mcp.server import MCPServer

from src.main.python.mcp.server import (
    PANEL_TOOL_NAMES,
    PANEL_WRITE_TOOL_NAMES,
    READ_ONLY_TOOL_NAMES,
    WRITE_TOOL_NAMES,
    mcp,
    restrict_tools,
)


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

    def test_panel_whitelist_is_subset_of_write_tools(self):
        self.assertLessEqual(PANEL_WRITE_TOOL_NAMES, WRITE_TOOL_NAMES)
        self.assertEqual(PANEL_TOOL_NAMES, READ_ONLY_TOOL_NAMES | PANEL_WRITE_TOOL_NAMES)

    def test_tracking_tools_are_classified(self):
        """N-35：讀取為唯讀；提議與更新是寫入，且在 §10.2 白名單內。"""
        self.assertIn("get_tracking_items", READ_ONLY_TOOL_NAMES)
        for name in ("propose_tracking_item", "update_tracking_item"):
            with self.subTest(tool=name):
                self.assertIn(name, WRITE_TOOL_NAMES)
                self.assertIn(name, PANEL_WRITE_TOOL_NAMES)

    def test_protected_writes_never_reach_the_panel(self):
        """PLAN.md §10.3：這些寫入在 Dashboard 對話框一律禁止，只能走 Claude Code。"""
        for name in ("sync_garmin_data", "generate_training_plan", "add_context_note",
                     "save_external_workouts"):
            with self.subTest(tool=name):
                self.assertNotIn(name, PANEL_TOOL_NAMES)


class TestRestrictTools(unittest.TestCase):
    """--panel／--read-only 由 server 自己移除工具，不只靠 Claude CLI 的權限判斷
    （2026-11-01 資安稽核 NV-3）。用一個新的 server 測，不動共用的 `mcp`。"""

    def _server_with_all_tools(self) -> MCPServer:
        server = MCPServer("restrict-test")
        for name in READ_ONLY_TOOL_NAMES | WRITE_TOOL_NAMES:
            server.add_tool(lambda: None, name=name)
        return server

    def _names(self, server):
        return {tool.name for tool in asyncio.run(server.list_tools())}

    def test_panel_keeps_only_whitelist(self):
        server = self._server_with_all_tools()
        restrict_tools(server, PANEL_TOOL_NAMES)
        self.assertEqual(self._names(server), PANEL_TOOL_NAMES)
        self.assertNotIn("sync_garmin_data", self._names(server))

    def test_read_only_keeps_no_write_tools(self):
        server = self._server_with_all_tools()
        restrict_tools(server, READ_ONLY_TOOL_NAMES)
        self.assertEqual(self._names(server), READ_ONLY_TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()
