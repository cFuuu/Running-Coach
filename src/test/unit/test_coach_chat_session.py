"""coach_chat_session（聊天的 Claude Code session 後端）與其路由的單元測試。

全部不啟動真的 claude：runner 換成逐行吐合成 stream-json 的假函式（形狀依
2026-10-14／10-27 對真實 `claude -p --output-format stream-json --verbose` 的觀察，
內容為虛構，不含真實個人資料）。
"""

import datetime
import json
import logging
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.main.python.api import routes_analysis, routes_chat
from src.main.python.api.app import create_app
from src.main.python.models.db import get_connection
from src.main.python.services import claude_session, coach_chat, coach_chat_session, coach_chat_store
from src.test.unit.fixtures import build_fixture_db
from src.test.unit.test_coach_analysis import claude_settings, refusing_runner, runner_of
from src.test.unit.test_routes_chat import parse_sse

for _noisy in ("httpx", "httpx2"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

LOCAL_CLIENT = ("127.0.0.1", 1)
TODAY = datetime.date(2026, 3, 10)


def _line(obj) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


def chat_stream(answer="今天狀況不錯，照課表練。"):
    """一回合：thinking 與 tool_use 各自一行 assistant 事件（Claude Code 實際就是這樣拆）、
    一次被拒的寫入工具、最後文字回覆與 result。"""
    return [
        _line({"type": "system", "subtype": "init", "tools": [], "permissionMode": "dontAsk"}),
        _line({"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "thinking", "thinking": "先查今天", "signature": "sig"}]}}),
        _line({"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "tool_use", "id": "tu_1", "name": "mcp__running-coach__get_today", "input": {}}]}}),
        _line({"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "tool_use", "id": "tu_2", "name": "mcp__running-coach__add_context_note",
             "input": {"note": "x"}}]}}),
        _line({"type": "system", "subtype": "permission_denied",
               "tool_name": "mcp__running-coach__add_context_note"}),
        _line({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tu_1",
             "content": [{"type": "text", "text": "{\"date\": \"2026-03-10\"}"}]}]}}),
        _line({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tu_2", "is_error": True, "content": "denied"}]}}),
        "not json\n",
        _line({"type": "assistant", "message": {"id": "m2", "content": [{"type": "text", "text": answer}]}}),
        _line({"duration_ms": 5000, "type": "result", "subtype": "success", "is_error": False,
               "stop_reason": "end_turn", "result": answer, "num_turns": 3}),
    ]


class SessionTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.db_path = tmp / "test.db"
        build_fixture_db(self.db_path).close()
        self.conn = get_connection(self.db_path, check_same_thread=False)
        self.addCleanup(self.conn.close)
        self.config = coach_chat.ChatConfig(claude=claude_settings(tmp))
        self.athlete_id = self.conn.execute(
            "SELECT id FROM athlete_profile ORDER BY id LIMIT 1"
        ).fetchone()["id"]
        self.cid = coach_chat_store.create_conversation(self.conn, self.athlete_id)["id"]

    def run_turn(self, runner, text="今天狀況如何？", config=None):
        return list(coach_chat_session.run_turn(
            conn=self.conn, config=config or self.config, athlete_id=self.athlete_id,
            conversation_id=self.cid, text=text, today=TODAY, runner=runner,
        ))


class TestRunTurn(SessionTestCase):
    def test_events_have_the_api_path_shape(self):
        events = self.run_turn(runner_of(chat_stream()))
        self.assertEqual(
            [e.event for e in events],
            ["tool_use", "tool_use", "denied", "tool_result", "tool_result", "text", "done"],
        )
        self.assertEqual(events[0].data, {"id": "tu_1", "name": "get_today", "input": {}})
        self.assertEqual(events[2].data, {"tool": "add_context_note"})
        self.assertEqual(events[3].data, {"tool_use_id": "tu_1", "name": "get_today",
                                          "is_error": False, "content": '{"date": "2026-03-10"}'})
        self.assertTrue(events[4].data["is_error"])
        self.assertEqual(events[5].data, {"delta": "今天狀況不錯，照課表練。"})
        self.assertEqual(events[-1].data["stop_reason"], "end_turn")

    def test_persisted_as_anthropic_content_blocks(self):
        events = self.run_turn(runner_of(chat_stream()))
        stored = coach_chat_store.get_messages(self.conn, self.cid)
        self.assertEqual([m["role"] for m in stored], ["user", "assistant", "user", "assistant"])
        self.assertEqual(stored[0]["content"], [
            {"type": "text", "text": "今天狀況如何？"},
            {"type": "text", "text": "〔日期〕2026-03-10（週二）"},
        ])
        # 拆成多行的 assistant 合併成一則、thinking 不存、工具名去前綴
        self.assertEqual(stored[1]["content"], [
            {"type": "tool_use", "id": "tu_1", "name": "get_today", "input": {}},
            {"type": "tool_use", "id": "tu_2", "name": "add_context_note", "input": {"note": "x"}},
        ])
        self.assertEqual([b["tool_use_id"] for b in stored[2]["content"]], ["tu_1", "tu_2"])
        self.assertEqual(stored[2]["content"][0]["content"], '{"date": "2026-03-10"}')
        self.assertEqual(stored[3]["content"], [{"type": "text", "text": "今天狀況不錯，照課表練。"}])
        last_id = coach_chat_store.get_conversation(self.conn, self.athlete_id, self.cid)["messages"][-1]["id"]
        self.assertEqual(events[-1].data["message_id"], last_id)

    def test_prompt_goes_to_stdin_with_date_and_history(self):
        self.run_turn(runner_of(chat_stream("第一次回答")), text="第一問")
        runner = runner_of(chat_stream())
        self.run_turn(runner, text="第二問")
        prompt = runner.prompt
        self.assertNotIn(prompt, runner.cmd)
        self.assertIn(coach_chat_session.HISTORY_HEADING, prompt)
        self.assertIn("學員：第一問", prompt)
        self.assertIn("教練（查了：get_today、add_context_note）：第一次回答", prompt)
        self.assertNotIn("2026-03-10\\\"", prompt)  # 不帶工具回傳
        self.assertTrue(prompt.endswith("〔日期〕2026-03-10（週二）\n第二問"))

    def test_command_uses_chat_model_and_panel_prompt(self):
        runner = runner_of(chat_stream())
        config = coach_chat.ChatConfig(claude=self.config.claude, model="opus")
        self.run_turn(runner, config=config)
        cmd = runner.cmd
        self.assertEqual(cmd[cmd.index("--model") + 1], "opus")
        self.assertEqual(cmd[cmd.index("--append-system-prompt") + 1], coach_chat.build_system_prompt())
        self.assertIn("--panel", json.loads(cmd[cmd.index("--mcp-config") + 1])
                      ["mcpServers"]["running-coach"]["args"])

    def test_failures_are_not_persisted(self):
        cases = {
            "no result": chat_stream()[:-1],
            "is_error": chat_stream()[:-1] + [_line({"type": "result", "subtype": "error_max_turns",
                                                      "is_error": True, "result": "secret raw"})],
            "ends on tool_result": chat_stream()[:7] + [chat_stream()[-1]],
        }
        for name, lines in cases.items():
            with self.subTest(name):
                events = self.run_turn(runner_of(lines))
                self.assertEqual(events[-1].event, "error")
                self.assertNotIn("secret raw", events[-1].data["message"])
                self.assertEqual(coach_chat_store.get_messages(self.conn, self.cid), [])

    def test_timeout_before_result_is_error(self):
        events = self.run_turn(runner_of(chat_stream()[:3], raise_after=TimeoutError("逾時")))
        self.assertEqual((events[-1].event, events[-1].data["message"]), ("error", "逾時"))

    def test_exit_code_after_complete_result_is_ignored(self):
        events = self.run_turn(runner_of(chat_stream(), raise_after=RuntimeError("結束碼 1")))
        self.assertEqual(events[-1].event, "done")


class TestHistoryText(unittest.TestCase):
    def rows(self, n):
        rows = []
        for i in range(n):
            rows.append({"role": "user", "kind": "chat", "created_at": f"2026-03-0{i + 1}T04:00:00+00:00",
                         "content": [{"type": "text", "text": f"問{i}" + "填" * 50},
                                     {"type": "text", "text": "〔日期〕2026-03-0x"}]})
            rows.append({"role": "assistant", "kind": "chat", "created_at": f"2026-03-0{i + 1}T04:00:00+00:00",
                         "content": [{"type": "text", "text": f"答{i}"}]})
        return rows

    def test_each_message_dated_and_date_block_not_repeated(self):
        text = coach_chat_session.build_history_text(self.rows(1), 10_000)
        self.assertIn("〔2026-03-01（週日）〕學員：問0", text)
        self.assertIn("〔2026-03-01（週日）〕教練：答0", text)
        self.assertNotIn("〔日期〕", text)

    def test_oldest_whole_rounds_dropped_when_too_long(self):
        text = coach_chat_session.build_history_text(self.rows(4), 200)
        self.assertNotIn("問0", text)
        self.assertIn("問3", text)
        self.assertIn("已省略", text)
        self.assertNotIn("答0", text)

    def test_empty_history(self):
        self.assertEqual(coach_chat_session.build_history_text([], 100), "")


class TestSessionRoutes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.db_path = tmp / "test.db"
        build_fixture_db(self.db_path).close()
        assets = tmp / "assets"
        assets.mkdir()
        self.app = create_app(str(self.db_path), assets_dir=assets,
                              chat_config=coach_chat.ChatConfig(claude=claude_settings(tmp)))
        self.runner = runner_of(chat_stream())
        self.app.dependency_overrides[routes_chat.get_session_runner] = lambda: self.runner
        self.app.dependency_overrides[routes_analysis.get_runner] = lambda: refusing_runner
        self.app.dependency_overrides[routes_chat.get_today] = lambda: TODAY

    def client(self):
        return TestClient(self.app, client=LOCAL_CLIENT)

    def test_status_reports_session_backend(self):
        status = self.client().get("/api/chat/status").json()
        self.assertEqual((status["enabled"], status["backend"], status["reason"]), (True, "session", None))

    def test_message_streams_and_title_is_first_sentence(self):
        cid = self.client().post("/api/chat/conversations").json()["id"]
        resp = self.client().post(f"/api/chat/conversations/{cid}/messages",
                                  json={"text": "膝蓋有點痛，今天還能練嗎？我昨天跑了十公里\n〔檢視脈絡〕view=home"})
        events = parse_sse(resp.text)
        self.assertEqual(events[-1][0], "done")
        self.assertIn("denied", [name for name, _ in events])
        conversation = self.client().get(f"/api/chat/conversations/{cid}").json()
        self.assertEqual(conversation["title"], "膝蓋有點痛，今天還能練嗎？")
        self.assertEqual({m["kind"] for m in conversation["messages"]}, {"chat"})
        self.assertFalse(claude_session.SESSION_LOCK.locked())

    def test_title_created_even_if_the_turn_fails(self):
        self.runner = runner_of(chat_stream()[:2], raise_after=TimeoutError("逾時"))
        cid = self.client().post("/api/chat/conversations").json()["id"]
        events = parse_sse(self.client().post(f"/api/chat/conversations/{cid}/messages",
                                              json={"text": "今天要練嗎？"}).text)
        self.assertEqual(events[-1][0], "error")
        conversation = self.client().get(f"/api/chat/conversations/{cid}").json()
        self.assertEqual((conversation["title"], conversation["messages"]), ("今天要練嗎？", []))
        self.assertFalse(claude_session.SESSION_LOCK.locked())

    def test_busy_returns_409_shared_with_analysis(self):
        cid = self.client().post("/api/chat/conversations").json()["id"]
        self.assertTrue(claude_session.SESSION_LOCK.acquire(blocking=False))
        try:
            resp = self.client().post(f"/api/chat/conversations/{cid}/messages", json={"text": "嗨"})
            self.assertEqual(resp.status_code, 409)
            self.assertIn("請等它完成", resp.json()["detail"])
            self.assertTrue(self.client().get("/api/analysis/status").json()["running"])
        finally:
            claude_session.SESSION_LOCK.release()
        self.assertEqual(self.runner.calls, 0)

    def test_disabled_session_returns_503_without_api_key_mention(self):
        self.app.dependency_overrides[routes_chat.get_chat_config] = lambda: coach_chat.ChatConfig(
            claude=claude_session.ClaudeCodeSettings(claude_bin="definitely-not-a-real-binary-xyz"))
        status = self.client().get("/api/chat/status").json()
        self.assertFalse(status["enabled"])
        self.assertNotIn("ANTHROPIC_API_KEY", status["reason"])
        self.assertEqual(self.client().post("/api/chat/conversations").status_code, 503)

    def test_recent_advice_endpoint(self):
        conn = get_connection(self.db_path)
        self.addCleanup(conn.close)
        aid = conn.execute("SELECT id FROM athlete_profile ORDER BY id LIMIT 1").fetchone()["id"]
        cid = coach_chat_store.create_conversation(conn, aid)["id"]
        coach_chat_store.append_turn(conn, cid, [
            {"role": "user", "content": [{"type": "text", "text": "〔分析近況〕\n問"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "本週減量。\n**依據**：TSB −30"}]},
        ], kind="analysis", created_at="2026-03-08T04:00:00+00:00")
        body = self.client().get("/api/chat/recent-advice").json()
        self.assertEqual(body, {"advice": [{"date": "2026-03-08", "conversation_id": cid, "text": "本週減量。"}]})
        # 來源限制同其他聊天端點
        other = TestClient(self.app, client=("192.168.1.5", 1))
        self.assertEqual(other.get("/api/chat/recent-advice").status_code, 403)


if __name__ == "__main__":
    unittest.main()
