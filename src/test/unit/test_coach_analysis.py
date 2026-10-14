"""總覽「AI 分析」的單元測試：claude -p 指令的安全旗標、stream-json 解析、
執行流程、報告保存、HTTP 路由。

全部不啟動真的 claude：執行層以假的 runner 逐行餵入與 2026-10-14 實測輸出
同形狀的合成 stream-json（內容為虛構，不含真實個人資料）。
"""

import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.main.python.api import routes_analysis
from src.main.python.api.app import create_app
from src.main.python.mcp.server import READ_ONLY_TOOL_NAMES, WRITE_TOOL_NAMES, coach
from src.main.python.models.db import get_connection
from src.main.python.services import analysis_report_store as store
from src.main.python.services import coach_analysis
from src.main.python.services.coach_analysis import AnalysisConfig
from src.test.unit.fixtures import build_fixture_db

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
    def runner(cmd, timeout):
        runner.cmd = cmd
        yield from lines
        if raise_after:
            raise raise_after
    runner.cmd = None
    return runner


def enabled_config(tmp: Path) -> AnalysisConfig:
    """claude_bin 指向一定存在的執行檔（本測試的 python），mcp_config 指向暫存檔，
    讓 enabled 為真；實際不會被執行（runner 已換成假的）。"""
    mcp = tmp / "mcp.json"
    mcp.write_text("{}", encoding="utf-8")
    return AnalysisConfig(claude_bin=sys.executable, mcp_config=mcp)


class TestBuildCommand(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cmd = coach_analysis.build_command(enabled_config(Path(self._tmp.name)), "問題")

    def _value(self, flag):
        return self.cmd[self.cmd.index(flag) + 1]

    def test_builtin_tools_disabled(self):
        self.assertEqual(self._value("--tools"), "")

    def test_only_read_only_mcp_tools_preapproved(self):
        allowed = set(self._value("--allowedTools").split(","))
        self.assertEqual(allowed, {f"mcp__running-coach__{n}" for n in READ_ONLY_TOOL_NAMES})

    def test_write_tools_explicitly_disallowed(self):
        disallowed = set(self._value("--disallowedTools").split(","))
        self.assertEqual(disallowed, {f"mcp__running-coach__{n}" for n in WRITE_TOOL_NAMES})

    def test_unapproved_tools_are_denied_without_prompting(self):
        self.assertEqual(self._value("--permission-mode"), "dontAsk")
        self.assertIn("--strict-mcp-config", self.cmd)
        self.assertIn("--no-session-persistence", self.cmd)

    def test_system_prompt_starts_with_coach_constraints_verbatim(self):
        self.assertTrue(self._value("--append-system-prompt").startswith(coach()))

    def test_disabled_when_claude_missing(self):
        cfg = AnalysisConfig(claude_bin="definitely-not-a-real-binary-xyz")
        self.assertFalse(cfg.enabled)
        self.assertIn("找不到 Claude Code", cfg.disabled_reason)


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


class TestAnalysisRoutes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.db_path = tmp / "test.db"
        build_fixture_db(self.db_path).close()
        assets = tmp / "assets"
        assets.mkdir()
        self.app = create_app(str(self.db_path), assets_dir=assets,
                              analysis_config=enabled_config(tmp))
        self.runner = runner_of(fake_stream())
        self.app.dependency_overrides[routes_analysis.get_runner] = lambda: self.runner

    def client(self, client=LOCAL_CLIENT):
        return TestClient(self.app, client=client)

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

    def test_prompt_is_server_side_constant(self):
        self.client().post("/api/analysis")
        cmd = self.runner.cmd
        self.assertEqual(cmd[cmd.index("-p") + 1], routes_analysis.ANALYSIS_PROMPT)

    def test_failed_run_saved_as_error(self):
        self.runner = runner_of(fake_stream()[:2], raise_after=TimeoutError("逾時"))
        events = self.sse_events(self.client().post("/api/analysis").text)
        self.assertEqual(events[-1][0], "error")
        report = self.client().get(f"/api/analysis/{events[0][1]['id']}").json()
        self.assertEqual((report["status"], report["error"]), ("error", "逾時"))

    def test_concurrent_run_rejected(self):
        self.assertTrue(routes_analysis._running_lock.acquire(blocking=False))
        try:
            self.assertEqual(self.client().post("/api/analysis").status_code, 409)
        finally:
            routes_analysis._running_lock.release()

    def test_lock_released_after_run(self):
        self.client().post("/api/analysis")
        self.assertFalse(routes_analysis._running_lock.locked())

    def test_disabled_returns_503(self):
        self.app.dependency_overrides[routes_analysis.get_analysis_config] = (
            lambda: AnalysisConfig(claude_bin="definitely-not-a-real-binary-xyz")
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
