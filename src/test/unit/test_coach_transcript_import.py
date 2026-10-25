"""coach_transcript_import 的單元測試——Claude Code session 紀錄 → 唯讀教練對話。

重點：回合篩選（只收碰過 running-coach 工具的回合）、該丟的一律丟掉
（插入的標籤段落、非教練工具、thinking、工具回傳）、重跑不重複、刪掉的不回來、
命中憑證樣式整批中止。

全部使用合成的 JSONL，不讀真實的 session 紀錄。
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.main.python.models.db import get_connection
from src.main.python.services import coach_chat_store as store
from src.main.python.services import coach_transcript_import as imp

P = imp.TOOL_PREFIX


def _user(text, ts="2026-10-05T09:21:00.000Z", **extra):
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}, **extra}


def _assistant(*blocks, ts="2026-10-05T09:21:05.000Z"):
    return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": list(blocks)}}


def _tool_result(tool_id, ts="2026-10-05T09:21:06.000Z"):
    return {
        "type": "user", "timestamp": ts, "toolUseResult": {},
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_id, "content": "HRV 45 ms"}
        ]},
    }


def _text(text):
    return {"type": "text", "text": text}


def _tool(tool_id, name, **inp):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inp}


COACH_TURN = [
    _user("<ide_opened_file>The user opened foo.py</ide_opened_file>目前身體狀況如何"),
    _assistant({"type": "thinking", "thinking": "先查", "signature": "s"},
               _tool("t1", P + "get_today"), _tool("t2", "Bash", command="cat .env")),
    _tool_result("t1"),
    _assistant(_text("整體還不錯，睡眠偏少。"), ts="2026-10-05T09:21:30.000Z"),
]
DEV_TURN = [
    _user("幫我 commit", ts="2026-10-05T10:00:00.000Z"),
    _assistant(_tool("t3", "Bash", command="git commit"), ts="2026-10-05T10:00:05.000Z"),
    _assistant(_text("已 commit。"), ts="2026-10-05T10:00:09.000Z"),
]


class TestCoachMessages(unittest.TestCase):
    def test_keeps_only_coaching_turns(self):
        messages = imp.coach_messages(COACH_TURN + DEV_TURN)
        texts = json.dumps(messages, ensure_ascii=False)
        self.assertIn("目前身體狀況如何", texts)
        self.assertNotIn("commit", texts)

    def test_strips_everything_but_question_answer_and_coach_tools(self):
        messages = imp.coach_messages(COACH_TURN)
        self.assertEqual(messages, [
            {"role": "user", "content": [_text("目前身體狀況如何")],
             "created_at": "2026-10-05T09:21:00+00:00"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "get_today", "input": {}},
                _text("整體還不錯，睡眠偏少。"),
            ], "created_at": "2026-10-05T09:21:05+00:00"},
        ])

    def test_loading_coach_tools_via_tool_search_counts_as_coaching(self):
        """10/05 實例：教練只用 ToolSearch 載入 add_context_note 就回答了。"""
        records = [
            _user("9/16~9/25 去日本玩"),
            _assistant(_tool("t1", "ToolSearch", query=f"select:{P}add_context_note")),
            _assistant(_text("了解，那段期間步數就說得通了。")),
        ]
        self.assertEqual(len(imp.coach_messages(records)), 2)

    def test_tool_search_for_other_tools_does_not_count(self):
        records = [_user("查網路"), _assistant(_tool("t1", "ToolSearch", query="select:WebSearch"))]
        self.assertEqual(imp.coach_messages(records), [])

    def test_skips_meta_sidechain_and_compact_summary_records(self):
        records = COACH_TURN + [
            _user("[Image: source: C:\\tmp\\a.png]", isMeta=True),
            _user("子代理的提示", isSidechain=True),
            _user("前情提要……", isCompactSummary=True),
        ]
        self.assertEqual(len(imp.coach_messages(records)), 2)

    def test_mid_turn_message_is_kept_as_user_text(self):
        records = COACH_TURN[:2] + [
            {"type": "attachment", "timestamp": "2026-10-05T09:21:10.000Z",
             "attachment": {"type": "queued_command", "prompt": [_text("順便看 HRV")]}},
        ] + COACH_TURN[2:]
        roles = [m["role"] for m in imp.coach_messages(records)]
        self.assertEqual(roles, ["user", "assistant", "user", "assistant"])


class TestCleanUserText(unittest.TestCase):
    def test_drops_injected_blocks_and_image_lines(self):
        raw = [
            _text("<browser_instruction># Chrome\n很長的說明</browser_instruction>"),
            _text("[Image: source: C:\\tmp\\x.png]\n這週課表是什麼"),
        ]
        self.assertEqual(imp.clean_user_text(raw), "這週課表是什麼")

    def test_slash_command_keeps_only_the_arguments(self):
        raw = ("<command-message>coach is running…</command-message>\n<command-name>/coach</command-name>\n"
               "<command-args>今天要練嗎</command-args>")
        self.assertEqual(imp.clean_user_text(raw), "今天要練嗎")


class ImportTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.conn = get_connection(tmp / "test.db")
        self.addCleanup(self.conn.close)
        self.conn.execute("INSERT INTO athlete_profile (name, updated_at) VALUES ('測試學員', '2026-01-01T00:00:00')")
        self.conn.commit()
        self.dir = tmp / "transcripts"
        self.dir.mkdir()

    def write_session(self, session_id, records):
        path = self.dir / f"{session_id}.jsonl"
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")

    def run_import(self, **kw):
        return imp.import_transcripts(self.conn, self.dir, **kw)


class TestImportTranscripts(ImportTestCase):
    def test_imports_coach_sessions_and_skips_dev_only_sessions(self):
        self.write_session("coach", COACH_TURN + DEV_TURN)
        self.write_session("dev", DEV_TURN)
        summary = self.run_import()
        self.assertEqual(summary["created"], 1)
        listed = store.list_conversations(self.conn, 1)
        self.assertEqual([(c["title"], c["source"]) for c in listed], [("目前身體狀況如何", "claude_code")])

    def test_rerun_is_unchanged_and_growth_updates_in_place(self):
        self.write_session("coach", COACH_TURN)
        self.run_import()
        self.assertEqual(self.run_import()["unchanged"], 1)

        later = [_user("那明天呢", ts="2026-10-06T08:00:00.000Z"),
                 _assistant(_tool("t9", P + "get_today"), _text("明天輕鬆跑。"), ts="2026-10-06T08:00:05.000Z")]
        self.write_session("coach", COACH_TURN + later)
        self.assertEqual(self.run_import()["updated"], 1)
        (conversation,) = store.list_conversations(self.conn, 1)
        self.assertEqual(conversation["updated_at"], "2026-10-06T08:00:05+00:00")
        self.assertEqual(len(store.get_conversation(self.conn, 1, conversation["id"])["messages"]), 4)

    def test_deleted_conversation_does_not_come_back(self):
        self.write_session("coach", COACH_TURN)
        self.run_import()
        (conversation,) = store.list_conversations(self.conn, 1)
        self.assertTrue(store.delete_conversation(self.conn, 1, conversation["id"]))
        self.assertEqual(self.run_import()["dismissed"], 1)
        self.assertEqual(store.list_conversations(self.conn, 1), [])

    def test_imported_conversation_is_read_only(self):
        self.write_session("coach", COACH_TURN)
        self.run_import()
        (conversation,) = store.list_conversations(self.conn, 1)
        self.assertTrue(store.is_read_only(self.conn, conversation["id"]))
        dashboard = store.create_conversation(self.conn, 1)
        self.assertFalse(store.is_read_only(self.conn, dashboard["id"]))

    def test_dry_run_writes_nothing(self):
        self.write_session("coach", COACH_TURN)
        self.assertEqual(self.run_import(dry_run=True)["created"], 1)
        self.assertEqual(store.list_conversations(self.conn, 1), [])

    def test_secret_in_any_session_aborts_the_whole_import(self):
        self.write_session("a-good", COACH_TURN)
        leaky = [_user("我的帳號是 runner@example.com"),
                 _assistant(_tool("t1", P + "get_today"), _text("好"))]
        self.write_session("b-leaky", leaky)
        with self.assertRaises(imp.TranscriptSecurityError) as ctx:
            self.run_import()
        self.assertNotIn("runner@example.com", str(ctx.exception))
        self.assertEqual(store.list_conversations(self.conn, 1), [])


class TestLegacyDatabaseMigration(unittest.TestCase):
    def test_existing_conversations_become_dashboard_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            legacy = sqlite3.connect(db_path)
            legacy.execute(
                "CREATE TABLE coach_conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, athlete_id INTEGER NOT NULL,"
                " title TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            legacy.execute("INSERT INTO coach_conversations (athlete_id, title, created_at, updated_at)"
                           " VALUES (1, '舊對話', 'x', 'x')")
            legacy.commit()
            legacy.close()

            conn = get_connection(db_path)
            try:
                row = conn.execute("SELECT source, source_session_id FROM coach_conversations").fetchone()
                self.assertEqual((row["source"], row["source_session_id"]), ("dashboard", None))
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
