"""coach_chat 的單元測試——對話迴圈、工具閘門、體積控制、串流事件解析。

LLM 端多數測試以 FakeBackend 取代（腳本化的事件序列），**工具端不假**：真的走
in-process MCP 去查 fixtures.build_fixture_db() 建的合成資料庫，因為
「唯讀工具真的查得到資料」正是這一層最容易寫錯、也最值得測的地方。

`AnthropicBackend` 另有一組測試，用 mock transport 餵**真實格式的 SSE 位元組**
給已安裝的 anthropic SDK，藉此釘住串流事件的形狀（thinking＋signature、
分段累積的 tool input JSON、stop_reason），不憑印象寫。

⚠️ 這樣仍驗不到的事：真實 API 實際會送什麼。地端拿真金鑰做過煙霧測試才算數
（DASHBOARD_TASKS.md D.6 S2 那一列）。

全部使用合成假資料，不含真實個人資料，也不需要 ANTHROPIC_API_KEY。
"""

import asyncio
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from anthropic import AsyncAnthropic

from src.main.python.mcp.server import READ_ONLY_TOOL_NAMES, WRITE_TOOL_NAMES, coach
from src.main.python.models.db import get_connection
from src.main.python.services import coach_chat, coach_chat_store
from src.test.unit.fixtures import FAKE_ATHLETE_NAME, build_fixture_db

# anthropic SDK 用 httpx2（舊版 httpx）發請求，每次都以 INFO 印一行，
# 會把整份測試輸出洗掉。只調這兩個 logger，不碰全域設定。
for _noisy in ("httpx", "httpx2"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


# --- 腳本化的假 LLM 後端 ---


class FakeBackend:
    """依腳本逐回合吐事件；同時記下每次收到的 system／tools／messages。"""

    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.calls = []

    async def astream(self, *, system, tools, messages):
        self.calls.append({"system": system, "tools": tools, "messages": messages})
        if not self.rounds:
            raise AssertionError("FakeBackend 被呼叫的次數超過腳本所定義的回合數")
        for event in self.rounds.pop(0):
            yield event


def text_round(text, stop_reason="end_turn"):
    """一個只回文字的回合。"""
    return [
        {"type": "text", "delta": text},
        {
            "type": "final",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
        },
    ]


def tool_round(tool_calls):
    """一個要求呼叫工具的回合。tool_calls 為 [(id, name, input), ...]。"""
    blocks = [
        {"type": "tool_use", "id": tool_id, "name": name, "input": arguments}
        for tool_id, name, arguments in tool_calls
    ]
    events = [
        {"type": "tool_use", "id": b["id"], "name": b["name"], "input": b["input"]}
        for b in blocks
    ]
    events.append({"type": "final", "content": blocks, "stop_reason": "tool_use"})
    return events


class CoachChatTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db_path = Path(self._tmp.name) / "test.db"
        build_fixture_db(db_path).close()
        # MCP SDK 用 anyio.to_thread.run_sync 在別的 thread 跑同步 tool，
        # 連線必須 check_same_thread=False（否則 sqlite3.ProgrammingError）。
        self.conn = get_connection(db_path, check_same_thread=False)
        self.addCleanup(self.conn.close)
        self.config = coach_chat.ChatConfig(api_key_present=True)
        self.athlete_id = self.conn.execute(
            "SELECT id FROM athlete_profile ORDER BY id LIMIT 1"
        ).fetchone()["id"]
        self.conversation_id = coach_chat_store.create_conversation(
            self.conn, self.athlete_id
        )["id"]

    def run_turn(self, backend, text="今天該練嗎？", config=None):
        async def collect():
            return [
                event
                async for event in coach_chat.stream_turn(
                    conn=self.conn,
                    config=config or self.config,
                    backend=backend,
                    conversation_id=self.conversation_id,
                    text=text,
                )
            ]

        return asyncio.run(collect())

    def events_of(self, events, name):
        return [e for e in events if e.event == name]


# --- 系統提示 ---


class TestSystemPrompt(unittest.TestCase):
    def test_starts_with_coach_prompt_verbatim(self):
        """行為約束層一字不改：系統提示的前段必須逐字等於 coach() 原文。"""
        self.assertTrue(coach_chat.build_system_prompt().startswith(coach()))

    def test_panel_note_follows_and_says_no_write_tools(self):
        prompt = coach_chat.build_system_prompt()
        tail = prompt[len(coach()) :]
        self.assertIn("〔檢視脈絡〕", tail)
        self.assertIn("Claude Code", tail)
        for name in ("add_context_note", "generate_training_plan", "sync_garmin_data"):
            self.assertIn(name, tail)

    def test_system_blocks_carry_cache_control(self):
        blocks = coach_chat.build_system_blocks()
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["cache_control"], {"type": "ephemeral"})
        self.assertTrue(blocks[0]["text"].startswith(coach()))


class TestToolSchemas(unittest.TestCase):
    def setUp(self):
        self.schemas = asyncio.run(coach_chat.build_tool_schemas())

    def test_only_read_only_tools_are_exposed(self):
        names = {schema["name"] for schema in self.schemas}
        self.assertEqual(names, set(READ_ONLY_TOOL_NAMES))
        self.assertFalse(names & set(WRITE_TOOL_NAMES))

    def test_schema_shape_is_anthropic_ready(self):
        for schema in self.schemas:
            with self.subTest(tool=schema["name"]):
                self.assertIn("input_schema", schema)
                self.assertIsInstance(schema["input_schema"], dict)
                self.assertTrue(schema["description"])
                # SDK 已排除 ctx 參數，不該出現在送模型的 schema 裡
                self.assertNotIn("ctx", schema["input_schema"].get("properties", {}))

    def test_last_tool_carries_cache_control(self):
        self.assertEqual(self.schemas[-1]["cache_control"], {"type": "ephemeral"})


# --- 設定 ---


class TestChatConfig(unittest.TestCase):
    def test_defaults_when_environment_is_empty(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            config = coach_chat.ChatConfig.from_env()
        self.assertFalse(config.enabled)
        self.assertEqual(config.disabled_reason, "未設定 ANTHROPIC_API_KEY")
        self.assertEqual(config.model, coach_chat.DEFAULT_MODEL)
        self.assertEqual(config.max_tokens, coach_chat.DEFAULT_MAX_TOKENS)
        self.assertFalse(config.allow_private_network)

    def test_reads_overrides_from_environment(self):
        env = {
            "ANTHROPIC_API_KEY": "not-a-real-key",
            "RC_COACH_MODEL": "claude-opus-5",
            "RC_COACH_MAX_TOKENS": "4096",
            "RC_COACH_HISTORY_MAX_CHARS": "999",
            "RC_COACH_TOOL_RESULT_MAX_CHARS": "111",
            "RC_CHAT_ALLOW_PRIVATE_NETWORK": "1",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            config = coach_chat.ChatConfig.from_env()
        self.assertTrue(config.enabled)
        self.assertIsNone(config.disabled_reason)
        self.assertEqual(config.model, "claude-opus-5")
        self.assertEqual(config.max_tokens, 4096)
        self.assertEqual(config.history_max_chars, 999)
        self.assertEqual(config.tool_result_max_chars, 111)
        self.assertTrue(config.allow_private_network)

    def test_api_key_never_appears_in_config(self):
        """金鑰不進本專案的資料結構——repr 被 log 出去也不會外洩（CLAUDE.md §🔐 3）。"""
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-secret-value"}, clear=True):
            config = coach_chat.ChatConfig.from_env()
        self.assertNotIn("sk-secret-value", repr(config))
        self.assertNotIn("sk-secret-value", json.dumps(config.__dict__))

    def test_invalid_integer_is_reported_clearly(self):
        with mock.patch.dict(os.environ, {"RC_COACH_MAX_TOKENS": "八千"}, clear=True):
            with self.assertRaises(ValueError) as raised:
                coach_chat.ChatConfig.from_env()
        self.assertIn("RC_COACH_MAX_TOKENS", str(raised.exception))


# --- 工具執行（真的走 in-process MCP 查 fixture DB）---


class TestToolExecution(CoachChatTestCase):
    def test_read_only_tool_really_queries_the_database(self):
        backend = FakeBackend(
            [tool_round([("toolu_1", "get_athlete_meta", {})]), text_round("查到了。")]
        )
        events = self.run_turn(backend)

        results = self.events_of(events, "tool_result")
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].data["is_error"])
        self.assertIn(FAKE_ATHLETE_NAME, results[0].data["content"])

    def test_list_returning_tool_merges_all_text_blocks(self):
        """`-> list[dict]` 的工具每項各一個區塊，聊天層必須併成一段。"""
        backend = FakeBackend(
            [
                tool_round([("toolu_1", "get_active_training_plan", {})]),
                text_round("好。"),
            ]
        )
        events = self.run_turn(backend)
        content = self.events_of(events, "tool_result")[0].data["content"]
        self.assertIn("lsd", content)

    def test_empty_result_becomes_null(self):
        """空 list 得到 content=[]，必須送 "null" 而不是空字串。"""
        backend = FakeBackend(
            [
                tool_round(
                    [
                        (
                            "toolu_1",
                            "get_context_notes",
                            {"start_date": "2020-01-01", "end_date": "2020-01-02"},
                        )
                    ]
                ),
                text_round("沒有紀錄。"),
            ]
        )
        events = self.run_turn(backend)
        self.assertEqual(
            self.events_of(events, "tool_result")[0].data["content"],
            coach_chat.EMPTY_TOOL_RESULT,
        )

    def test_write_tool_is_refused_and_never_executed(self):
        backend = FakeBackend(
            [
                tool_round(
                    [("toolu_1", "add_context_note", {"note": "出差", "start_date": "2026-03-09"})]
                ),
                text_round("這個面板做不到。"),
            ]
        )
        events = self.run_turn(backend)

        result = self.events_of(events, "tool_result")[0].data
        self.assertTrue(result["is_error"])
        self.assertIn("Claude Code", result["content"])
        # 真的沒有寫進去
        written = self.conn.execute("SELECT COUNT(*) AS n FROM context_notes").fetchone()["n"]
        self.assertEqual(written, 0)

    def test_unknown_tool_name_is_refused(self):
        backend = FakeBackend(
            [tool_round([("toolu_1", "definitely_not_a_tool", {})]), text_round("抱歉。")]
        )
        events = self.run_turn(backend)
        self.assertTrue(self.events_of(events, "tool_result")[0].data["is_error"])

    def test_tool_error_becomes_is_error_not_an_exception(self):
        """參數驗證失敗時 SDK 拋 ToolError，必須轉成 tool_result is_error=True。"""
        backend = FakeBackend(
            [tool_round([("toolu_1", "get_session_detail", {})]), text_round("參數不對。")]
        )
        events = self.run_turn(backend)

        result = self.events_of(events, "tool_result")[0].data
        self.assertTrue(result["is_error"])
        self.assertEqual(self.events_of(events, "error"), [])
        self.assertEqual(len(self.events_of(events, "done")), 1)

    def test_parallel_tool_uses_are_merged_into_one_user_message(self):
        backend = FakeBackend(
            [
                tool_round(
                    [
                        ("toolu_1", "get_athlete_meta", {}),
                        ("toolu_2", "get_active_training_plan", {}),
                    ]
                ),
                text_round("兩個都查過了。"),
            ]
        )
        events = self.run_turn(backend)
        self.assertEqual(len(self.events_of(events, "tool_result")), 2)

        stored = coach_chat_store.get_messages(self.conn, self.conversation_id)
        # user 提問 / assistant tool_use / user 兩個 tool_result / assistant 回覆
        self.assertEqual([m["role"] for m in stored], ["user", "assistant", "user", "assistant"])
        self.assertEqual(len(stored[2]["content"]), 2)
        self.assertEqual(
            [b["tool_use_id"] for b in stored[2]["content"]], ["toolu_1", "toolu_2"]
        )


# --- 迴圈與落地 ---


class TestTurnLoop(CoachChatTestCase):
    def test_plain_answer_streams_text_and_persists(self):
        backend = FakeBackend([text_round("今天照課表練。")])
        events = self.run_turn(backend)

        self.assertEqual([e.data["delta"] for e in self.events_of(events, "text")], ["今天照課表練。"])
        done = self.events_of(events, "done")
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0].data["stop_reason"], "end_turn")

        stored = coach_chat_store.get_messages(self.conn, self.conversation_id)
        self.assertEqual(len(stored), 2)
        self.assertEqual(stored[0]["content"][0]["text"], "今天該練嗎？")
        self.assertEqual(done[0].data["message_id"], self.last_message_id())

    def last_message_id(self):
        conversation = coach_chat_store.get_conversation(
            self.conn, self.athlete_id, self.conversation_id
        )
        return conversation["messages"][-1]["id"]

    def test_history_is_replayed_on_the_next_turn(self):
        self.run_turn(FakeBackend([text_round("第一次回覆。")]), text="第一問")
        backend = FakeBackend([text_round("第二次回覆。")])
        self.run_turn(backend, text="第二問")

        sent = backend.calls[0]["messages"]
        self.assertEqual([m["role"] for m in sent], ["user", "assistant", "user"])
        self.assertEqual(sent[0]["content"][0]["text"], "第一問")
        self.assertEqual(sent[2]["content"][0]["text"], "第二問")

    def test_thinking_blocks_are_stored_and_replayed_verbatim(self):
        thinking = {"type": "thinking", "thinking": "先想一下", "signature": "sig-xyz"}
        backend = FakeBackend(
            [
                [
                    {
                        "type": "final",
                        "content": [thinking, {"type": "text", "text": "結論。"}],
                        "stop_reason": "end_turn",
                    }
                ]
            ]
        )
        self.run_turn(backend)

        stored = coach_chat_store.get_messages(self.conn, self.conversation_id)
        self.assertEqual(stored[1]["content"][0], thinking)

    def test_max_tokens_emits_error_and_persists_nothing(self):
        backend = FakeBackend([text_round("被截斷的開頭", stop_reason="max_tokens")])
        events = self.run_turn(backend)

        errors = self.events_of(events, "error")
        self.assertEqual(len(errors), 1)
        self.assertIn("max_tokens", errors[0].data["message"])
        self.assertEqual(self.events_of(events, "done"), [])
        self.assertEqual(coach_chat_store.get_messages(self.conn, self.conversation_id), [])

    def test_backend_failure_emits_error_and_persists_nothing(self):
        class BoomBackend:
            async def astream(self, *, system, tools, messages):
                raise RuntimeError("網路斷了")
                yield  # pragma: no cover - 讓它是 async generator

        events = self.run_turn(BoomBackend())
        self.assertEqual(len(self.events_of(events, "error")), 1)
        self.assertEqual(coach_chat_store.get_messages(self.conn, self.conversation_id), [])

    def test_anthropic_exception_message_is_not_echoed_verbatim(self):
        """API 例外訊息可能夾帶 request/response 原文，不可原樣送到前端。"""

        class FakeAPIError(Exception):
            status_code = 401

        FakeAPIError.__module__ = "anthropic"

        class FailingBackend:
            async def astream(self, *, system, tools, messages):
                raise FakeAPIError("invalid x-api-key: sk-secret-value")
                yield  # pragma: no cover

        events = self.run_turn(FailingBackend())
        message = self.events_of(events, "error")[0].data["message"]
        self.assertNotIn("sk-secret-value", message)
        self.assertIn("401", message)

    def test_runaway_tool_loop_is_stopped(self):
        rounds = [tool_round([("toolu_x", "get_athlete_meta", {})])] * (
            coach_chat.MAX_TOOL_ROUNDS + 1
        )
        events = self.run_turn(FakeBackend(rounds))

        errors = self.events_of(events, "error")
        self.assertEqual(len(errors), 1)
        self.assertIn(str(coach_chat.MAX_TOOL_ROUNDS), errors[0].data["message"])
        self.assertEqual(coach_chat_store.get_messages(self.conn, self.conversation_id), [])

    def test_stop_reason_tool_use_without_tool_blocks_does_not_loop(self):
        backend = FakeBackend(
            [
                [
                    {
                        "type": "final",
                        "content": [{"type": "text", "text": "說要用工具卻沒給"}],
                        "stop_reason": "tool_use",
                    }
                ]
            ]
        )
        events = self.run_turn(backend)
        self.assertEqual(len(self.events_of(events, "done")), 1)


# --- 體積控制 ---


def _user_text(text):
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def _assistant_tool_use(tool_id):
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": tool_id, "name": "get_today", "input": {}}],
    }


def _tool_result(tool_id, content):
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content}],
    }


class TestSizeControl(unittest.TestCase):
    def setUp(self):
        self.config = coach_chat.ChatConfig(
            api_key_present=True,
            history_max_chars=10_000,
            tool_result_max_chars=50,
        )

    def test_oversized_tool_result_is_truncated_with_a_marker(self):
        messages = [
            _user_text("問"),
            _assistant_tool_use("t1"),
            _tool_result("t1", "x" * 500),
            {"role": "assistant", "content": [{"type": "text", "text": "答"}]},
        ]
        prepared = coach_chat.prepare_messages_for_model(messages, self.config)
        content = prepared[2]["content"][0]["content"]
        self.assertTrue(content.startswith("x" * 50))
        self.assertTrue(content.endswith(coach_chat.TRUNCATED_SUFFIX))

    def test_original_messages_are_not_mutated(self):
        """落地用的資料要原樣不裁——prepare 不可就地改到呼叫端的 list。"""
        messages = [_user_text("問"), _assistant_tool_use("t1"), _tool_result("t1", "x" * 500)]
        coach_chat.prepare_messages_for_model(messages, self.config)
        self.assertEqual(messages[2]["content"][0]["content"], "x" * 500)

    def test_only_the_two_most_recent_tool_result_rounds_keep_content(self):
        messages = [_user_text("問")]
        for index in range(4):
            messages.append(_assistant_tool_use(f"t{index}"))
            messages.append(_tool_result(f"t{index}", f"result-{index}"))
        prepared = coach_chat.prepare_messages_for_model(messages, self.config)

        contents = [
            m["content"][0]["content"] for m in prepared if m["content"][0]["type"] == "tool_result"
        ]
        self.assertEqual(
            contents,
            [
                coach_chat.ELIDED_TOOL_RESULT,
                coach_chat.ELIDED_TOOL_RESULT,
                "result-2",
                "result-3",
            ],
        )

    def test_elision_keeps_the_tool_use_tool_result_pairing(self):
        """只換內容、不刪訊息：少了任何一邊，API 會 400。"""
        messages = [_user_text("問")]
        for index in range(4):
            messages.append(_assistant_tool_use(f"t{index}"))
            messages.append(_tool_result(f"t{index}", "x"))
        prepared = coach_chat.prepare_messages_for_model(messages, self.config)

        self.assertEqual(len(prepared), len(messages))
        tool_use_ids = [
            b["id"] for m in prepared for b in m["content"] if b.get("type") == "tool_use"
        ]
        result_ids = [
            b["tool_use_id"]
            for m in prepared
            for b in m["content"]
            if b.get("type") == "tool_result"
        ]
        self.assertEqual(tool_use_ids, result_ids)

    def test_history_is_cut_only_at_a_plain_text_user_turn(self):
        config = coach_chat.ChatConfig(
            api_key_present=True, history_max_chars=400, tool_result_max_chars=10_000
        )
        messages = []
        for index in range(4):
            messages.append(_user_text(f"第{index}問" + "填" * 60))
            messages.append(_assistant_tool_use(f"t{index}"))
            messages.append(_tool_result(f"t{index}", "短"))
            messages.append({"role": "assistant", "content": [{"type": "text", "text": "答"}]})

        prepared = coach_chat.prepare_messages_for_model(messages, config)

        self.assertLess(len(prepared), len(messages))
        self.assertTrue(coach_chat._is_plain_text_user_message(prepared[0]))
        self.assertLessEqual(
            sum(len(json.dumps(m, ensure_ascii=False)) for m in prepared),
            config.history_max_chars,
        )

    def test_history_under_the_limit_is_untouched(self):
        messages = [_user_text("問"), {"role": "assistant", "content": [{"type": "text", "text": "答"}]}]
        self.assertEqual(coach_chat.prepare_messages_for_model(messages, self.config), messages)

    def test_single_oversized_turn_is_kept_rather_than_cut_mid_pair(self):
        """連最後一個純文字 user 回合起都超標時，寧可原樣送，也不拆散成對結構。"""
        config = coach_chat.ChatConfig(
            api_key_present=True, history_max_chars=10, tool_result_max_chars=10_000
        )
        messages = [_user_text("問"), _assistant_tool_use("t1"), _tool_result("t1", "x" * 100)]
        prepared = coach_chat.prepare_messages_for_model(messages, config)
        self.assertEqual(len(prepared), 3)


# --- AnthropicBackend 的串流解析 ---
#
# anthropic SDK 的 client 建在 httpx2 上（舊版用 httpx），mock transport 必須
# 來自同一個套件，否則型別對不上。
try:  # pragma: no cover - 取決於安裝到哪個版本
    import httpx2 as anthropic_httpx
except ModuleNotFoundError:  # pragma: no cover
    import httpx as anthropic_httpx


def _sse_bytes(events):
    return "".join(
        f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events
    ).encode()


# 一段涵蓋四種區塊型別的回應：thinking（含 signature）、text（兩個 delta）、
# tool_use（input JSON 分兩段送），最後 stop_reason=tool_use。
_STREAM_EVENTS = [
    (
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
        },
    ),
    (
        "content_block_start",
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        },
    ),
    (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "先查今天"},
        },
    ),
    (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "sig-abc"},
        },
    ),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    (
        "content_block_start",
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "text", "text": ""},
        },
    ),
    (
        "content_block_delta",
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "今天"}},
    ),
    (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": "該練嗎"},
        },
    ),
    ("content_block_stop", {"type": "content_block_stop", "index": 1}),
    (
        "content_block_start",
        {
            "type": "content_block_start",
            "index": 2,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "get_today",
                "input": {},
            },
        },
    ),
    (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "input_json_delta", "partial_json": '{"athlete_id"'},
        },
    ),
    (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "input_json_delta", "partial_json": ": 1}"},
        },
    ),
    ("content_block_stop", {"type": "content_block_stop", "index": 2}),
    (
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": {"output_tokens": 20},
        },
    ),
    ("message_stop", {"type": "message_stop"}),
]


class TestAnthropicBackendStreamParsing(unittest.TestCase):
    def setUp(self):
        self.requests = []

        def handler(request):
            self.requests.append(json.loads(request.content))
            return anthropic_httpx.Response(
                200,
                content=_sse_bytes(_STREAM_EVENTS),
                headers={"content-type": "text/event-stream"},
            )

        client = AsyncAnthropic(
            api_key="not-a-real-key",
            http_client=anthropic_httpx.AsyncClient(
                transport=anthropic_httpx.MockTransport(handler)
            ),
        )
        backend = coach_chat.AnthropicBackend(
            model="claude-sonnet-5", max_tokens=1024, client=client
        )

        async def collect():
            return [
                event
                async for event in backend.astream(
                    system=coach_chat.build_system_blocks("系統提示"),
                    tools=[
                        {
                            "name": "get_today",
                            "description": "今日",
                            "input_schema": {"type": "object", "properties": {}},
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": [{"type": "text", "text": "今天該練嗎"}]}],
                )
            ]

        self.events = asyncio.run(collect())

    def test_text_deltas_are_streamed_as_they_arrive(self):
        self.assertEqual(
            [e["delta"] for e in self.events if e["type"] == "text"], ["今天", "該練嗎"]
        )

    def test_tool_use_is_emitted_with_fully_accumulated_input(self):
        tool_events = [e for e in self.events if e["type"] == "tool_use"]
        self.assertEqual(len(tool_events), 1)
        self.assertEqual(tool_events[0]["name"], "get_today")
        self.assertEqual(tool_events[0]["input"], {"athlete_id": 1})

    def test_final_content_preserves_thinking_and_signature(self):
        final = self.events[-1]
        self.assertEqual(final["type"], "final")
        self.assertEqual(final["stop_reason"], "tool_use")
        self.assertEqual(
            final["content"],
            [
                {"type": "thinking", "thinking": "先查今天", "signature": "sig-abc"},
                {"type": "text", "text": "今天該練嗎"},
                {"type": "tool_use", "id": "toolu_1", "name": "get_today", "input": {"athlete_id": 1}},
            ],
        )

    def test_final_content_blocks_are_json_serialisable_for_storage(self):
        """落地要存 JSON，含 SDK 內部欄位（例如 partial JSON 緩衝）就會炸。"""
        json.dumps(self.events[-1]["content"], ensure_ascii=False)

    def test_request_carries_streaming_system_and_cache_control(self):
        body = self.requests[0]
        self.assertTrue(body["stream"])
        self.assertEqual(body["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(body["tools"][-1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(body["max_tokens"], 1024)


if __name__ == "__main__":
    unittest.main()
