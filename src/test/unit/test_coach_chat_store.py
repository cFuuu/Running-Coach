"""coach_chat_store 的單元測試——聊天紀錄的落地行為。

重點在三件事：訊息原樣存回原樣（thinking 的 signature 不可被動到）、
一回合一交易（失敗整批 rollback，絕不留下沒有 tool_result 的 tool_use）、
刪除連 messages 一起刪（不依賴 CASCADE）。

全部使用合成假資料建立臨時 SQLite，不含真實個人資料。
"""

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


if __name__ == "__main__":
    unittest.main()
