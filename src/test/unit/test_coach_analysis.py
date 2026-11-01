"""總覽「AI 分析」的單元測試：claude -p 指令的安全旗標、stream-json 解析、
執行流程、報告保存、HTTP 路由。

全部不啟動真的 claude：執行層以假的 runner 逐行餵入與 2026-10-14 實測輸出
同形狀的合成 stream-json（內容為虛構，不含真實個人資料）。
"""

import datetime
import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from src.main.python.api import routes_analysis, routes_chat
from src.main.python.api.app import create_app
from src.main.python.mcp.server import (
    PANEL_TOOL_NAMES,
    PANEL_WRITE_TOOL_NAMES,
    WRITE_TOOL_NAMES,
    coach,
)
from src.main.python.models.db import get_connection
from src.main.python.services import analysis_report_store as store
from src.main.python.services import claude_session, coach_analysis, coach_chat, coach_chat_store
from src.main.python.services.claude_session import ClaudeCodeSettings
from src.main.python.services.coach_analysis import AnalysisConfig
from src.test.unit.fixtures import build_fixture_db

TODAY = datetime.date(2026, 3, 10)

for _noisy in ("httpx", "httpx2"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

LOCAL_CLIENT = ("127.0.0.1", 1)


def _line(obj) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


def fake_stream(report="## 目前狀態\n一切正常") -> list[str]:
    """與真實 `claude -p --output-format stream-json --verbose` 同形狀的事件序列。"""
    return [
        _line({"type": "system", "subtype": "init", "tools": [], "permissionMode": "dontAsk"}),
        _line({"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "..."},
            {"type": "tool_use", "id": "tu_1", "name": "mcp__running-coach__get_today", "input": {}},
        ]}}),
        _line({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tu_1",
             "content": [{"type": "text", "text": "{\"date\": \"2026-03-10\"}"}]},
        ]}}),
        _line({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu_2", "name": "mcp__running-coach__add_context_note",
             "input": {"note": "x"}},
        ]}}),
        _line({"type": "system", "subtype": "permission_denied",
               "tool_name": "mcp__running-coach__add_context_note"}),
        _line({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tu_2", "is_error": True, "content": "denied"},
        ]}}),
        _line({"type": "assistant", "message": {"content": [{"type": "text", "text": report}]}}),
        _line({"type": "result", "subtype": "success", "is_error": False, "num_turns": 3,
               "duration_ms": 5000, "result": report,
               "permission_denials": [{"tool_name": "mcp__running-coach__add_context_note"}]}),
    ]


def runner_of(lines, raise_after=None):
    """假的 runner（簽名同 claude_session.subprocess_runner），記下指令與 stdin 的 prompt。"""
    def runner(cmd, prompt, timeout):
        runner.cmd = cmd
        runner.prompt = prompt
        runner.calls += 1
        yield from lines
        if raise_after:
            raise raise_after
    runner.cmd = None
    runner.prompt = None
    runner.calls = 0
    return runner


def refusing_runner(cmd, prompt, timeout):
    """單元測試絕不能真的跑 claude：沒被替換掉的 runner 一律換成這個。"""
    raise AssertionError("單元測試不應啟動真的 Claude Code")
    yield  # pragma: no cover


FAKE_MCP_ENTRY = {
    "type": "stdio",
    "command": "python",
    "args": ["-m", "src.main.python.mcp.server", "--db-path", "output/x.db"],
    "env": {},
}


def claude_settings(tmp: Path) -> ClaudeCodeSettings:
    """claude_bin 指向一定存在的執行檔（本測試的 python），mcp_config 指向暫存檔，
    讓 enabled 為真；實際不會被執行（runner 已換成假的）。"""
    mcp = tmp / "mcp.json"
    mcp.write_text(
        json.dumps({"mcpServers": {"running-coach": FAKE_MCP_ENTRY, "other": {"command": "x"}}}),
        encoding="utf-8",
    )
    return ClaudeCodeSettings(claude_bin=sys.executable, mcp_config=mcp)


def enabled_config(tmp: Path) -> AnalysisConfig:
    return AnalysisConfig(claude=claude_settings(tmp))


class TestBuildCommand(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cmd = coach_analysis.build_command(enabled_config(Path(self._tmp.name)))

    def _value(self, flag):
        return self.cmd[self.cmd.index(flag) + 1]

    def test_builtin_tools_disabled(self):
        self.assertEqual(self._value("--tools"), "")

    def test_prompt_is_not_on_the_command_line(self):
        """prompt 走 stdin（Windows 命令列約 32K 字元上限），-p 後面直接接旗標。"""
        self.assertEqual(self._value("-p"), "--output-format")

    def test_panel_tools_preapproved(self):
        allowed = set(self._value("--allowedTools").split(","))
        self.assertEqual(allowed, {f"mcp__running-coach__{n}" for n in PANEL_TOOL_NAMES})

    def test_non_whitelisted_write_tools_explicitly_disallowed(self):
        disallowed = set(self._value("--disallowedTools").split(","))
        self.assertEqual(
            disallowed,
            {f"mcp__running-coach__{n}" for n in WRITE_TOOL_NAMES - PANEL_WRITE_TOOL_NAMES},
        )
        self.assertNotIn("mcp__running-coach__add_context_note", self._value("--allowedTools"))

    def test_mcp_config_is_json_string_with_only_running_coach_in_panel_mode(self):
        config = json.loads(self._value("--mcp-config"))
        self.assertEqual(list(config["mcpServers"]), ["running-coach"])
        entry = config["mcpServers"]["running-coach"]
        self.assertEqual(entry["args"], FAKE_MCP_ENTRY["args"] + ["--panel"])
        self.assertEqual(entry["command"], "python")

    def test_panel_flag_not_duplicated(self):
        path = Path(self._tmp.name) / "panel.json"
        entry = dict(FAKE_MCP_ENTRY, args=FAKE_MCP_ENTRY["args"] + ["--panel"])
        path.write_text(json.dumps({"mcpServers": {"running-coach": entry}}), encoding="utf-8")
        config = json.loads(claude_session.panel_mcp_config_json(path))
        self.assertEqual(config["mcpServers"]["running-coach"]["args"].count("--panel"), 1)

    def test_mcp_config_without_running_coach_is_reported(self):
        path = Path(self._tmp.name) / "bad.json"
        path.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            claude_session.panel_mcp_config_json(path)

    def test_unapproved_tools_are_denied_without_prompting(self):
        self.assertEqual(self._value("--permission-mode"), "dontAsk")
        self.assertIn("--strict-mcp-config", self.cmd)
        self.assertIn("--no-session-persistence", self.cmd)

    def test_system_prompt_starts_with_coach_constraints_verbatim(self):
        prompt = self._value("--append-system-prompt")
        self.assertTrue(prompt.startswith(coach()))
        self.assertIn("追問", prompt[len(coach()):])

    def test_model_defaults_to_the_chat_api_model(self):
        """session 路徑模擬日後打 API：預設與聊天同一個模型，不沿用 Claude Code 全域設定。"""
        self.assertEqual(self._value("--model"), coach_chat.DEFAULT_MODEL)

    def test_model_can_be_overridden_by_env(self):
        with mock.patch.dict(os.environ, {"RC_ANALYSIS_MODEL": "claude-opus-5-5"}):
            self.assertEqual(AnalysisConfig.from_env().model, "claude-opus-5-5")
        with mock.patch.dict(os.environ, {"RC_ANALYSIS_MODEL": ""}):
            self.assertEqual(AnalysisConfig.from_env().model, coach_chat.DEFAULT_MODEL)

    def test_disabled_when_claude_missing(self):
        cfg = AnalysisConfig(claude=ClaudeCodeSettings(claude_bin="definitely-not-a-real-binary-xyz"))
        self.assertFalse(cfg.enabled)
        self.assertIn("找不到 Claude Code", cfg.disabled_reason)


class TestTurnRecorder(unittest.TestCase):
    """stream-json → 要落地的 Anthropic content blocks。"""

    def test_blocks_converted_merged_and_thinking_dropped(self):
        recorder = claude_session.TurnRecorder()
        for line in fake_stream("報告"):
            msg = claude_session.parse_line(line)
            if msg:
                recorder.feed(msg)
        self.assertEqual(
            [m["role"] for m in recorder.messages], ["assistant", "user", "assistant", "user", "assistant"]
        )
        first = recorder.messages[0]["content"]
        self.assertEqual(first, [{"type": "tool_use", "id": "tu_1", "name": "get_today", "input": {}}])
        result = recorder.messages[1]["content"][0]
        self.assertEqual(result["content"], '{"date": "2026-03-10"}')
        self.assertFalse(result["is_error"])
        self.assertTrue(recorder.messages[3]["content"][0]["is_error"])

    def test_consecutive_assistant_lines_merge_into_one_message(self):
        """Claude Code 的 stream-json 每個 content block 可能各一行 assistant 事件。"""
        recorder = claude_session.TurnRecorder()
        for block in (
            {"type": "thinking", "thinking": "嗯", "signature": "sig"},
            {"type": "text", "text": "先查"},
            {"type": "tool_use", "id": "a", "name": "mcp__running-coach__get_today", "input": {}},
            {"type": "tool_use", "id": "b", "name": "mcp__running-coach__get_daily_feel", "input": {}},
        ):
            recorder.feed({"type": "assistant", "message": {"content": [block]}})
        for tid in ("a", "b"):
            recorder.feed({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}})
        self.assertEqual(len(recorder.messages), 2)
        self.assertEqual([b["type"] for b in recorder.messages[0]["content"]], ["text", "tool_use", "tool_use"])
        self.assertEqual([b["tool_use_id"] for b in recorder.messages[1]["content"]], ["a", "b"])
        self.assertEqual(recorder.tool_names, {"a": "get_today", "b": "get_daily_feel"})


class TestParseStreamLine(unittest.TestCase):
    def events(self):
        out = []
        for line in fake_stream():
            out.extend(coach_analysis.parse_stream_line(line))
        return out

    def test_event_sequence(self):
        self.assertEqual(
            [e.event for e in self.events()],
            ["tool_use", "tool_result", "tool_use", "denied", "tool_result", "text", "done"],
        )

    def test_tool_names_shortened_and_thinking_dropped(self):
        evs = self.events()
        self.assertEqual(evs[0].data["name"], "get_today")
        self.assertNotIn("thinking", [e.event for e in evs])

    def test_tool_result_preview_and_error_flag(self):
        evs = self.events()
        self.assertIn("2026-03-10", evs[1].data["preview"])
        self.assertTrue(evs[4].data["is_error"])

    def test_done_carries_report_and_denials(self):
        done = self.events()[-1].data
        self.assertEqual(done["report"], "## 目前狀態\n一切正常")
        self.assertEqual(done["denied_tools"], ["add_context_note"])

    def test_garbage_lines_ignored(self):
        self.assertEqual(coach_analysis.parse_stream_line("not json"), [])
        self.assertEqual(coach_analysis.parse_stream_line("   "), [])

    def test_long_tool_result_truncated(self):
        long = "x" * (coach_analysis.TOOL_RESULT_PREVIEW_CHARS + 50)
        ev = coach_analysis.parse_stream_line(_line({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t", "content": long}]}}))[0]
        self.assertTrue(ev.data["truncated"])
        self.assertEqual(len(ev.data["preview"]), coach_analysis.TOOL_RESULT_PREVIEW_CHARS)


class TestRunAnalysis(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = enabled_config(Path(self._tmp.name))

    def run_events(self, runner):
        return [e.event for e in coach_analysis.run_analysis(self.cfg, "問題", runner)]

    def test_ends_with_done(self):
        self.assertEqual(self.run_events(runner_of(fake_stream()))[-1], "done")

    def test_missing_result_is_error(self):
        self.assertEqual(self.run_events(runner_of(fake_stream()[:-1]))[-1], "error")

    def test_timeout_is_error(self):
        evs = self.run_events(runner_of(fake_stream()[:2], raise_after=TimeoutError("逾時")))
        self.assertEqual(evs[-1], "error")

    def test_error_after_complete_report_is_ignored(self):
        evs = self.run_events(runner_of(fake_stream(), raise_after=RuntimeError("結束碼 1")))
        self.assertEqual(evs[-1], "done")
        self.assertNotIn("error", evs)


class TestReportStore(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.addCleanup(self.conn.close)
        self.athlete_id = self.conn.execute(
            "INSERT INTO athlete_profile (name, updated_at) VALUES ('測試', '2026-03-10')"
        ).lastrowid

    def test_create_finish_get_list(self):
        rid = store.create_report(self.conn, self.athlete_id, "問題")
        store.finish_report(self.conn, rid, report_text="報告內容", tools=[{"name": "get_today"}],
                            num_turns=3, duration_ms=5000)
        report = store.get_report(self.conn, self.athlete_id, rid)
        self.assertEqual((report["status"], report["report_text"]), ("done", "報告內容"))
        self.assertEqual(report["tools"], [{"name": "get_today"}])
        self.assertEqual(store.list_reports(self.conn, self.athlete_id)[0]["preview"], "報告內容")

    def test_error_status(self):
        rid = store.create_report(self.conn, self.athlete_id, "問題")
        store.finish_report(self.conn, rid, report_text=None, tools=[], error="逾時")
        self.assertEqual(store.get_report(self.conn, self.athlete_id, rid)["status"], "error")

    def test_stale_running_marked_error(self):
        rid = store.create_report(self.conn, self.athlete_id, "問題")
        self.assertEqual(store.mark_stale_running_as_error(self.conn), 1)
        self.assertEqual(store.get_report(self.conn, self.athlete_id, rid)["status"], "error")


class TestMigrateReportsToConversation(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.addCleanup(self.conn.close)
        self.athlete_id = self.conn.execute(
            "INSERT INTO athlete_profile (name, updated_at) VALUES ('測試', '2026-03-10')"
        ).lastrowid

    def report(self, created_at, text, status="done"):
        rid = store.create_report(self.conn, self.athlete_id, "當時的問題")
        store.finish_report(self.conn, rid, report_text=text, tools=[],
                            error=None if status == "done" else "壞了")
        self.conn.execute("UPDATE analysis_reports SET created_at = ? WHERE id = ?", (created_at, rid))
        self.conn.commit()
        return rid

    def test_reports_become_one_conversation_in_time_order(self):
        self.report("2026-03-09T21:00:00", "第二份")
        self.report("2026-03-01T08:00:00", "第一份")
        self.report("2026-03-05T08:00:00", "失敗的", status="error")
        self.report("2026-03-06T08:00:00", "   ")

        self.assertEqual(coach_analysis.migrate_reports_to_conversation(self.conn), 2)

        conversations = coach_chat_store.list_conversations(self.conn, self.athlete_id)
        self.assertEqual([c["title"] for c in conversations], ["分析近況紀錄"])
        full = coach_chat_store.get_conversation(self.conn, self.athlete_id, conversations[0]["id"])
        messages = full["messages"]
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "user", "assistant"])
        self.assertTrue(all(m["kind"] == "analysis" for m in messages))
        self.assertEqual(messages[1]["content"], [{"type": "text", "text": "第一份"}])
        self.assertEqual(messages[3]["content"], [{"type": "text", "text": "第二份"}])
        self.assertEqual(messages[0]["content"][0]["text"], "〔分析近況〕\n當時的問題")
        self.assertEqual(messages[0]["content"][1]["text"], "〔日期〕2026-03-01（週日）")
        # 訊息時間用報告時間（本機時間轉 UTC 後，本機日期不變）
        self.assertEqual(coach_chat_store.local_date_of(messages[2]["created_at"]), datetime.date(2026, 3, 9))

    def test_rerun_does_not_duplicate(self):
        self.report("2026-03-01T08:00:00", "第一份")
        coach_analysis.migrate_reports_to_conversation(self.conn)
        self.assertEqual(coach_analysis.migrate_reports_to_conversation(self.conn), 0)
        cid = coach_chat_store.list_conversations(self.conn, self.athlete_id)[0]["id"]
        self.assertEqual(len(coach_chat_store.get_messages(self.conn, cid)), 2)
        # 之後才補進來的舊報告接在同一則對話
        self.report("2026-03-02T08:00:00", "後補")
        coach_analysis.migrate_reports_to_conversation(self.conn)
        self.assertEqual(len(coach_chat_store.list_conversations(self.conn, self.athlete_id)), 1)
        self.assertEqual(len(coach_chat_store.get_messages(self.conn, cid)), 4)

    def test_nothing_to_convert_creates_nothing(self):
        self.assertEqual(coach_analysis.migrate_reports_to_conversation(self.conn), 0)
        self.assertEqual(coach_chat_store.list_conversations(self.conn, self.athlete_id), [])


class TestAnalysisRoutes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.db_path = tmp / "test.db"
        build_fixture_db(self.db_path).close()
        assets = tmp / "assets"
        assets.mkdir()
        self.app = create_app(
            str(self.db_path), assets_dir=assets, analysis_config=enabled_config(tmp),
            chat_config=coach_chat.ChatConfig(claude=claude_settings(tmp)),
        )
        self.runner = runner_of(fake_stream())
        self.app.dependency_overrides[routes_analysis.get_runner] = lambda: self.runner
        self.app.dependency_overrides[routes_chat.get_session_runner] = lambda: refusing_runner
        self.app.dependency_overrides[routes_chat.get_today] = lambda: TODAY

    def client(self, client=LOCAL_CLIENT):
        return TestClient(self.app, base_url="http://127.0.0.1", client=client)

    def conn(self):
        conn = get_connection(self.db_path)
        self.addCleanup(conn.close)
        return conn

    def athlete_id(self):
        return self.conn().execute("SELECT id FROM athlete_profile ORDER BY id LIMIT 1").fetchone()["id"]

    @staticmethod
    def sse_events(text):
        out = []
        for frame in text.strip().split("\n\n"):
            lines = dict(l.split(": ", 1) for l in frame.split("\n") if ": " in l)
            out.append((lines["event"], json.loads(lines["data"])))
        return out

    def test_stream_and_saved_report(self):
        resp = self.client().post("/api/analysis")
        self.assertEqual(resp.status_code, 200)
        events = self.sse_events(resp.text)
        self.assertEqual(events[0][0], "start")
        self.assertEqual(events[-1][0], "done")
        rid = events[0][1]["id"]
        report = self.client().get(f"/api/analysis/{rid}").json()
        self.assertEqual(report["status"], "done")
        self.assertEqual([t["name"] for t in report["tools"]], ["get_today", "add_context_note"])
        self.assertTrue(report["tools"][1]["is_error"])
        self.assertEqual(len(self.client().get("/api/analysis").json()["reports"]), 1)

    def test_prompt_is_server_side_constant_with_date(self):
        self.client().post("/api/analysis")
        self.assertTrue(self.runner.prompt.endswith(coach_analysis.ANALYSIS_PROMPT))
        self.assertIn("〔日期〕2026-03-10（週二）", self.runner.prompt)

    def test_report_saved_as_analysis_turn_in_new_conversation(self):
        """沒有任何對話時新開一則「分析近況 MM/DD」，報告存成 kind=analysis 的一回合。"""
        events = self.sse_events(self.client().post("/api/analysis").text)
        start, done = events[0][1], events[-1][1]
        cid = start["conversation_id"]
        self.assertEqual(done["conversation_id"], cid)
        self.assertIsInstance(done["message_id"], int)

        conversation = self.client().get(f"/api/chat/conversations/{cid}").json()
        self.assertEqual(conversation["title"], "分析近況 03/10")
        messages = conversation["messages"]
        self.assertTrue(all(m["kind"] == "analysis" for m in messages))
        self.assertEqual([m["role"] for m in messages],
                         ["user", "assistant", "user", "assistant", "user", "assistant"])
        self.assertEqual(messages[0]["content"][0]["text"],
                         f"〔分析近況〕\n{coach_analysis.ANALYSIS_PROMPT}")
        self.assertEqual(messages[0]["content"][1]["text"], "〔日期〕2026-03-10（週二）")
        self.assertEqual(messages[1]["content"][0]["name"], "get_today")
        self.assertEqual(messages[-1]["content"], [{"type": "text", "text": "## 目前狀態\n一切正常"}])
        self.assertEqual(done["message_id"], messages[-1]["id"])
        # 執行紀錄照舊，且記下對話
        report = self.client().get(f"/api/analysis/{start['id']}").json()
        self.assertEqual(report["conversation_id"], cid)

    def test_uses_latest_conversation_when_not_given(self):
        conn = self.conn()
        aid = self.athlete_id()
        older = coach_chat_store.create_conversation(conn, aid, title="舊")["id"]
        newer = coach_chat_store.create_conversation(conn, aid, title="新")["id"]
        conn.execute("UPDATE coach_conversations SET updated_at = '2000-01-01' WHERE id = ?", (newer,))
        conn.commit()
        start = self.sse_events(self.client().post("/api/analysis").text)[0][1]
        self.assertEqual(start["conversation_id"], older)
        title = self.client().get(f"/api/chat/conversations/{older}").json()["title"]
        self.assertEqual(title, "舊")  # 已有標題不改

    def test_explicit_conversation_and_untitled_gets_title(self):
        conn = self.conn()
        aid = self.athlete_id()
        cid = coach_chat_store.create_conversation(conn, aid)["id"]
        coach_chat_store.create_conversation(conn, aid, title="比較新的")
        start = self.sse_events(self.client().post(f"/api/analysis?conversation_id={cid}").text)[0][1]
        self.assertEqual(start["conversation_id"], cid)
        self.assertEqual(self.client().get(f"/api/chat/conversations/{cid}").json()["title"], "分析近況 03/10")

    def test_unknown_conversation_404_without_running(self):
        self.assertEqual(self.client().post("/api/analysis?conversation_id=999").status_code, 404)
        self.assertEqual(self.runner.calls, 0)
        self.assertFalse(claude_session.SESSION_LOCK.locked())

    def test_failed_run_saved_as_error_and_not_in_conversation(self):
        self.runner = runner_of(fake_stream()[:2], raise_after=TimeoutError("逾時"))
        events = self.sse_events(self.client().post("/api/analysis").text)
        self.assertEqual(events[-1][0], "error")
        report = self.client().get(f"/api/analysis/{events[0][1]['id']}").json()
        self.assertEqual((report["status"], report["error"]), ("error", "逾時"))
        cid = events[0][1]["conversation_id"]
        self.assertEqual(self.client().get(f"/api/chat/conversations/{cid}").json()["messages"], [])

    def test_prompt_carries_recent_advice_brief(self):
        # 落地時間是真的現在，近期建議看 30 天內，所以這裡的「今天」要用真的今天
        self.app.dependency_overrides[routes_chat.get_today] = lambda: datetime.date.today()
        self.client().post("/api/analysis")
        self.runner = runner_of(fake_stream())
        self.client().post("/api/analysis")
        self.assertIn("〔備忘〕", self.runner.prompt)
        self.assertIn("當時的建議，不是現在的事實，數字要重新查", self.runner.prompt)
        self.assertIn("一切正常", self.runner.prompt)

    def test_concurrent_run_rejected(self):
        self.assertTrue(claude_session.SESSION_LOCK.acquire(blocking=False))
        try:
            resp = self.client().post("/api/analysis")
            self.assertEqual(resp.status_code, 409)
            self.assertIn("請等它完成", resp.json()["detail"])
        finally:
            claude_session.SESSION_LOCK.release()

    def test_lock_released_after_run(self):
        self.client().post("/api/analysis")
        self.assertFalse(claude_session.SESSION_LOCK.locked())

    def test_disabled_returns_503(self):
        self.app.dependency_overrides[routes_analysis.get_analysis_config] = (
            lambda: AnalysisConfig(claude=ClaudeCodeSettings(claude_bin="definitely-not-a-real-binary-xyz"))
        )
        self.assertEqual(self.client().post("/api/analysis").status_code, 503)
        status = self.client().get("/api/analysis/status").json()
        self.assertFalse(status["enabled"])

    def test_private_network_rejected(self):
        self.assertEqual(self.client(client=("192.168.1.5", 1)).post("/api/analysis").status_code, 403)
        self.assertEqual(self.client(client=("testclient", 1)).get("/api/analysis").status_code, 403)

    def test_unknown_report_404(self):
        self.assertEqual(self.client().get("/api/analysis/999").status_code, 404)


if __name__ == "__main__":
    unittest.main()
