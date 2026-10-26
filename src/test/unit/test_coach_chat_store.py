"""coach_chat_store 的單元測試——聊天紀錄的落地行為。

重點在三件事：訊息原樣存回原樣（thinking 的 signature 不可被動到）、
一回合一交易（失敗整批 rollback，絕不留下沒有 tool_result 的 tool_use）、
刪除連 messages 一起刪（不依賴 CASCADE）。

全部使用合成假資料建立臨時 SQLite，不含真實個人資料。
"""

import datetime
import json
import tempfile
import unittest
from pathlib import Path

from src.main.python.models.db import get_connection
from src.main.python.services import coach_chat_store as store


def _user(text):
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def _assistant_tool_use(tool_id, name):
    return {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "先查今天的狀況", "signature": "sig-abc"},
            {"type": "tool_use", "id": tool_id, "name": name, "input": {}},
        ],
    }


def _tool_result(tool_id, content):
    return {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": tool_id, "content": content, "is_error": False}
        ],
    }


class CoachChatStoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = get_connection(Path(self._tmp.name) / "test.db")
        self.addCleanup(self.conn.close)
        self.athlete_id = self.conn.execute(
            "INSERT INTO athlete_profile (name, updated_at) VALUES ('測試學員', '2026-01-01T00:00:00')"
        ).lastrowid
        self.conn.commit()


class TestConversationCrud(CoachChatStoreTestCase):
    def test_create_returns_row_with_null_title(self):
        conversation = store.create_conversation(self.conn, self.athlete_id)
        self.assertIsNone(conversation["title"])
        self.assertEqual(conversation["created_at"], conversation["updated_at"])

    def test_list_is_ordered_by_updated_at_desc(self):
        first = store.create_conversation(self.conn, self.athlete_id)
        second = store.create_conversation(self.conn, self.athlete_id)
        # 讓 first 變成最近更新的那一則
        store.append_turn(self.conn, first["id"], [_user("今天該練嗎")])

        ids = [c["id"] for c in store.list_conversations(self.conn, self.athlete_id)]
        self.assertEqual(ids[0], first["id"])
        self.assertIn(second["id"], ids)

    def test_list_is_scoped_to_athlete(self):
        other = self.conn.execute(
            "INSERT INTO athlete_profile (name, updated_at) VALUES ('另一位', '2026-01-01T00:00:00')"
        ).lastrowid
        store.create_conversation(self.conn, self.athlete_id)
        self.assertEqual(store.list_conversations(self.conn, other), [])

    def test_get_conversation_of_other_athlete_returns_none(self):
        other = self.conn.execute(
            "INSERT INTO athlete_profile (name, updated_at) VALUES ('另一位', '2026-01-01T00:00:00')"
        ).lastrowid
        conversation = store.create_conversation(self.conn, self.athlete_id)
        self.assertIsNone(store.get_conversation(self.conn, other, conversation["id"]))


class TestAppendTurn(CoachChatStoreTestCase):
    def setUp(self):
        super().setUp()
        self.conversation_id = store.create_conversation(self.conn, self.athlete_id)["id"]

    def test_messages_round_trip_verbatim(self):
        turn = [
            _user("今天該練嗎？"),
            _assistant_tool_use("toolu_1", "get_today"),
            _tool_result("toolu_1", '{"date": "2026-09-08"}'),
            {"role": "assistant", "content": [{"type": "text", "text": "今天照課表練。"}]},
        ]
        store.append_turn(self.conn, self.conversation_id, turn)

        stored = store.get_messages(self.conn, self.conversation_id)
        self.assertEqual(stored, turn)
        # thinking 的 signature 特別確認一次：被動過的話 API 會拒絕重播
        self.assertEqual(stored[1]["content"][0]["signature"], "sig-abc")

    def test_seq_is_continuous_across_turns(self):
        store.append_turn(self.conn, self.conversation_id, [_user("第一問")])
        store.append_turn(self.conn, self.conversation_id, [_user("第二問")])
        seqs = [
            row["seq"]
            for row in self.conn.execute(
                "SELECT seq FROM coach_messages WHERE conversation_id = ? ORDER BY seq",
                (self.conversation_id,),
            )
        ]
        self.assertEqual(seqs, [0, 1])

    def test_title_taken_from_first_user_message(self):
        store.append_turn(self.conn, self.conversation_id, [_user("今天該練嗎？")])
        conversation = store.get_conversation(self.conn, self.athlete_id, self.conversation_id)
        self.assertEqual(conversation["title"], "今天該練嗎？")

    def test_title_ignores_the_view_context_line_appended_by_the_panel(self):
        """面板會在訊息末尾自動附一行〔檢視脈絡〕，那行不該進對話清單的標題。"""
        store.append_turn(
            self.conn,
            self.conversation_id,
            [_user("今天該練嗎？\n〔檢視脈絡〕view=today range=30d")],
        )
        conversation = store.get_conversation(self.conn, self.athlete_id, self.conversation_id)
        self.assertEqual(conversation["title"], "今天該練嗎？")

    def test_title_truncated_and_not_overwritten_by_later_turns(self):
        long_question = "今" * 100
        store.append_turn(self.conn, self.conversation_id, [_user(long_question)])
        store.append_turn(self.conn, self.conversation_id, [_user("後來又問了別的")])

        conversation = store.get_conversation(self.conn, self.athlete_id, self.conversation_id)
        self.assertEqual(conversation["title"], "今" * store.TITLE_MAX_CHARS)

    def test_updated_at_advances(self):
        before = store.get_conversation(self.conn, self.athlete_id, self.conversation_id)
        store.append_turn(self.conn, self.conversation_id, [_user("今天該練嗎")])
        after = store.get_conversation(self.conn, self.athlete_id, self.conversation_id)
        self.assertGreaterEqual(after["updated_at"], before["updated_at"])

    def test_empty_turn_writes_nothing(self):
        self.assertEqual(store.append_turn(self.conn, self.conversation_id, []), [])
        self.assertEqual(store.get_messages(self.conn, self.conversation_id), [])

    def test_failed_turn_rolls_back_entire_batch(self):
        """一回合一交易：中間任何一列寫不進去，整批都不落地。

        絕不能留下「有 tool_use 卻沒有 tool_result」的殘骸——那會讓下一回合
        送 API 直接 400。這裡用不合法的 role 觸發失敗。
        """
        turn = [
            _user("今天該練嗎？"),
            _assistant_tool_use("toolu_1", "get_today"),
            {"role": "system", "content": [{"type": "text", "text": "不合法"}]},
        ]
        with self.assertRaises(ValueError):
            store.append_turn(self.conn, self.conversation_id, turn)

        self.assertEqual(store.get_messages(self.conn, self.conversation_id), [])
        conversation = store.get_conversation(self.conn, self.athlete_id, self.conversation_id)
        self.assertIsNone(conversation["title"])

    def test_get_conversation_exposes_ids_and_seq(self):
        store.append_turn(self.conn, self.conversation_id, [_user("今天該練嗎？")])
        conversation = store.get_conversation(self.conn, self.athlete_id, self.conversation_id)
        message = conversation["messages"][0]
        self.assertEqual(message["seq"], 0)
        self.assertEqual(message["role"], "user")
        self.assertEqual(message["content"], [{"type": "text", "text": "今天該練嗎？"}])
        self.assertIsInstance(message["id"], int)
        self.assertEqual(message["kind"], "chat")

    def test_kind_and_created_at_apply_to_the_whole_turn(self):
        store.append_turn(
            self.conn, self.conversation_id,
            [_user("問"), {"role": "assistant", "content": [{"type": "text", "text": "答"}]}],
            kind=store.KIND_ANALYSIS, created_at="2026-03-01T00:00:00+00:00",
        )
        messages = store.get_conversation(self.conn, self.athlete_id, self.conversation_id)["messages"]
        self.assertEqual({m["kind"] for m in messages}, {"analysis"})
        self.assertEqual({m["created_at"] for m in messages}, {"2026-03-01T00:00:00+00:00"})

    def test_invalid_kind_rejected(self):
        with self.assertRaises(ValueError):
            store.append_turn(self.conn, self.conversation_id, [_user("問")], kind="other")


class TestTitleAndDate(CoachChatStoreTestCase):
    def test_title_is_first_sentence_up_to_16_chars(self):
        self.assertEqual(store.TITLE_MAX_CHARS, 16)
        self.assertEqual(store.title_from_text("膝蓋有點痛。昨天跑完就這樣"), "膝蓋有點痛。")
        self.assertEqual(store.title_from_text("  今天要練嗎?還是休息"), "今天要練嗎?")
        self.assertEqual(store.title_from_text("第一行\n〔檢視脈絡〕view=today"), "第一行")
        self.assertEqual(store.title_from_text("一" * 30), "一" * 16)

    def test_set_title_if_empty_does_not_overwrite(self):
        cid = store.create_conversation(self.conn, self.athlete_id)["id"]
        store.set_title_if_empty(self.conn, cid, "第一次")
        store.set_title_if_empty(self.conn, cid, "第二次")
        self.assertEqual(store.get_conversation(self.conn, self.athlete_id, cid)["title"], "第一次")

    def test_user_message_carries_separate_date_block(self):
        message = store.user_message("今天狀況如何？", datetime.date(2026, 10, 27))
        self.assertEqual(message["content"], [
            {"type": "text", "text": "今天狀況如何？"},
            {"type": "text", "text": "〔日期〕2026-10-27（週二）"},
        ])

    def test_title_skips_date_block(self):
        cid = store.create_conversation(self.conn, self.athlete_id)["id"]
        message = store.user_message("問題", datetime.date(2026, 10, 27))
        message["content"].reverse()  # 日期區塊在前也不該被當成標題
        store.append_turn(self.conn, cid, [message])
        self.assertEqual(store.get_conversation(self.conn, self.athlete_id, cid)["title"], "問題")

    def test_latest_conversation_id(self):
        self.assertIsNone(store.latest_conversation_id(self.conn, self.athlete_id))
        first = store.create_conversation(self.conn, self.athlete_id)["id"]
        store.create_conversation(self.conn, self.athlete_id)
        store.append_turn(self.conn, first, [_user("問")])
        self.assertEqual(store.latest_conversation_id(self.conn, self.athlete_id), first)


class TestRecentAdvice(CoachChatStoreTestCase):
    TODAY = datetime.date(2026, 3, 31)

    def analysis(self, day: str, report: str, cid=None):
        cid = cid or store.create_conversation(self.conn, self.athlete_id)["id"]
        store.append_turn(
            self.conn, cid,
            [
                _user("〔分析近況〕\n問題"),
                {"role": "assistant", "content": [{"type": "text", "text": "我先查一下"},
                                                   {"type": "tool_use", "id": "t", "name": "get_today", "input": {}}]},
                _tool_result("t", "{}"),
                {"role": "assistant", "content": [{"type": "text", "text": report}]},
            ],
            kind=store.KIND_ANALYSIS, created_at=f"{day}T04:00:00+00:00",
        )
        return cid

    def test_final_report_without_basis_line_newest_first(self):
        cid = self.analysis("2026-03-20", "**目前狀態**：還行。\n**依據**：TSB −20；HRV 45")
        self.analysis("2026-03-25", "**目前狀態**：很好。")
        advice = store.list_recent_advice(self.conn, self.athlete_id, self.TODAY)
        self.assertEqual([a["date"] for a in advice], ["2026-03-25", "2026-03-20"])
        self.assertEqual(advice[1], {"date": "2026-03-20", "conversation_id": cid,
                                     "text": "**目前狀態**：還行。"})

    def test_window_count_and_chat_turns_ignored(self):
        self.analysis("2026-02-20", "太舊")  # 39 天前
        for day in range(20, 27):
            self.analysis(f"2026-03-{day}", f"第{day}天")
        chat = store.create_conversation(self.conn, self.athlete_id)["id"]
        store.append_turn(self.conn, chat, [_user("聊天"), {"role": "assistant", "content": [
            {"type": "text", "text": "不是分析"}]}])
        advice = store.list_recent_advice(self.conn, self.athlete_id, self.TODAY)
        self.assertEqual(len(advice), store.RECENT_ADVICE_MAX_ITEMS)
        self.assertEqual(advice[0]["text"], "第26天")
        self.assertNotIn("太舊", [a["text"] for a in advice])

    def test_total_chars_capped(self):
        self.analysis("2026-03-29", "新" * 2000)
        self.analysis("2026-03-28", "舊" * 2000)
        advice = store.list_recent_advice(self.conn, self.athlete_id, self.TODAY)
        self.assertEqual([a["text"][0] for a in advice], ["新"])
        self.assertLessEqual(sum(len(a["text"]) for a in advice), store.RECENT_ADVICE_MAX_CHARS)

    def test_deleted_conversation_no_longer_advises(self):
        cid = self.analysis("2026-03-29", "建議")
        store.delete_conversation(self.conn, self.athlete_id, cid)
        self.assertEqual(store.list_recent_advice(self.conn, self.athlete_id, self.TODAY), [])


class TestKindColumnMigration(unittest.TestCase):
    def test_existing_database_gets_kind_and_conversation_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "old.db"
            conn = get_connection(db_path)
            conn.execute("DROP TABLE coach_messages")
            conn.execute(
                "CREATE TABLE coach_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " conversation_id INTEGER NOT NULL, seq INTEGER NOT NULL, role TEXT NOT NULL,"
                " content_json TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            conn.execute("INSERT INTO coach_messages (conversation_id, seq, role, content_json, created_at)"
                         " VALUES (1, 0, 'user', '[]', 'x')")
            conn.execute("ALTER TABLE analysis_reports DROP COLUMN conversation_id")
            conn.commit()
            conn.close()

            conn = get_connection(db_path)
            try:
                self.assertEqual(conn.execute("SELECT kind FROM coach_messages").fetchone()[0], "chat")
                columns = {r["name"] for r in conn.execute("PRAGMA table_info(analysis_reports)")}
                self.assertIn("conversation_id", columns)
                with self.assertRaises(Exception):
                    conn.execute("UPDATE coach_messages SET kind = 'bogus'")
            finally:
                conn.close()


class TestDeleteConversation(CoachChatStoreTestCase):
    def test_delete_removes_messages_too(self):
        conversation_id = store.create_conversation(self.conn, self.athlete_id)["id"]
        store.append_turn(
            self.conn,
            conversation_id,
            [_user("今天該練嗎？"), {"role": "assistant", "content": [{"type": "text", "text": "好"}]}],
        )

        self.assertTrue(store.delete_conversation(self.conn, self.athlete_id, conversation_id))

        remaining = self.conn.execute(
            "SELECT COUNT(*) AS n FROM coach_messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()["n"]
        self.assertEqual(remaining, 0)
        self.assertIsNone(store.get_conversation(self.conn, self.athlete_id, conversation_id))

    def test_delete_missing_conversation_returns_false(self):
        self.assertFalse(store.delete_conversation(self.conn, self.athlete_id, 9999))

    def test_delete_does_not_cross_athletes(self):
        other = self.conn.execute(
            "INSERT INTO athlete_profile (name, updated_at) VALUES ('另一位', '2026-01-01T00:00:00')"
        ).lastrowid
        conversation_id = store.create_conversation(self.conn, self.athlete_id)["id"]
        self.assertFalse(store.delete_conversation(self.conn, other, conversation_id))
        self.assertIsNotNone(store.get_conversation(self.conn, self.athlete_id, conversation_id))


class TestContentJsonIsUnicodeReadable(CoachChatStoreTestCase):
    def test_stored_json_keeps_chinese_characters(self):
        """content_json 存 ensure_ascii=False 的 JSON，日後直接看資料庫也讀得懂。"""
        conversation_id = store.create_conversation(self.conn, self.athlete_id)["id"]
        store.append_turn(self.conn, conversation_id, [_user("今天該練嗎？")])
        raw = self.conn.execute(
            "SELECT content_json FROM coach_messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()["content_json"]
        self.assertIn("今天該練嗎？", raw)
        self.assertEqual(json.loads(raw)[0]["text"], "今天該練嗎？")


class TestDropClaudeCodeImport(unittest.TestCase):
    """2026-10-27 移除 N-33 匯入：跑過那版的資料庫，開啟時清掉匯入的對話與欄位。"""

    def _db_with_import(self, inline_check: bool) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "n33.db"
        conn = get_connection(db_path)
        if inline_check:
            # N-33 版 schema.sql 全新建立的樣子：欄位與 CHECK 寫在建表語句裡
            conn.execute("DROP TABLE coach_conversations")
            conn.execute(
                "CREATE TABLE coach_conversations (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " athlete_id INTEGER NOT NULL, title TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,"
                " source TEXT NOT NULL DEFAULT 'dashboard' CHECK (source IN ('dashboard', 'claude_code')),"
                " source_session_id TEXT)"
            )
        else:
            # 真實資料庫的樣子：由遷移以 ALTER TABLE 補上
            conn.execute("ALTER TABLE coach_conversations ADD COLUMN source TEXT NOT NULL DEFAULT 'dashboard'")
            conn.execute("ALTER TABLE coach_conversations ADD COLUMN source_session_id TEXT")
        conn.execute("CREATE UNIQUE INDEX idx_coach_conversations_source_session"
                     " ON coach_conversations (source_session_id)")
        conn.execute("CREATE TABLE coach_import_dismissed (source_session_id TEXT PRIMARY KEY, dismissed_at TEXT NOT NULL)")
        athlete_id = conn.execute(
            "INSERT INTO athlete_profile (name, updated_at) VALUES ('測試學員', '2026-01-01T00:00:00')"
        ).lastrowid
        for cid, source, session in ((1, "dashboard", None), (2, "claude_code", "session-a")):
            conn.execute(
                "INSERT INTO coach_conversations (id, athlete_id, title, created_at, updated_at, source, source_session_id)"
                " VALUES (?, ?, 't', 'x', 'x', ?, ?)",
                (cid, athlete_id, source, session),
            )
            conn.execute(
                "INSERT INTO coach_messages (conversation_id, seq, role, content_json, created_at)"
                " VALUES (?, 0, 'user', '[]', 'x')",
                (cid,),
            )
        conn.commit()
        conn.close()
        return db_path

    def assert_cleaned(self, db_path: Path) -> None:
        conn = get_connection(db_path)
        try:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(coach_conversations)")}
            self.assertNotIn("source", columns)
            self.assertNotIn("source_session_id", columns)
            self.assertEqual([r["id"] for r in conn.execute("SELECT id FROM coach_conversations")], [1])
            self.assertEqual([r[0] for r in conn.execute("SELECT conversation_id FROM coach_messages")], [1])
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertNotIn("coach_import_dismissed", tables)
        finally:
            conn.close()

    def test_database_migrated_by_alter_table(self):
        self.assert_cleaned(self._db_with_import(inline_check=False))

    def test_database_created_by_n33_schema(self):
        self.assert_cleaned(self._db_with_import(inline_check=True))


if __name__ == "__main__":
    unittest.main()
