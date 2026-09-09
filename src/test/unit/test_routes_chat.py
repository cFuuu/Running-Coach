"""routes_chat 的單元測試——來源限制、狀態碼、SSE 框架格式、對話 CRUD。

LLM 端一律用 test_coach_chat.FakeBackend（不呼叫真實 API、不需要金鑰）；
工具端仍是真的 in-process MCP 查合成 fixture DB。

⚠️ Starlette 的 TestClient 預設 client host 是字串 `"testclient"`，
不是合法 IP。來源限制是 fail closed 的，所以每個要通過的測試都必須明確傳
`client=("127.0.0.1", 1)`——順帶讓「解析不出來的 host 一律 403」有得測。
"""

import json
import logging
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.main.python.api import routes_chat
from src.main.python.api.app import create_app
from src.main.python.models.db import get_connection
from src.main.python.services import coach_chat, coach_chat_store
from src.test.unit.fixtures import build_fixture_db
from src.test.unit.test_coach_chat import FakeBackend, text_round, tool_round

LOCAL_CLIENT = ("127.0.0.1", 1)

# TestClient 底層是 httpx，它會把每個請求以 INFO 印出來，把整份測試輸出洗掉。
# Starlette 有 httpx2 時優先用它（anthropic 會帶入），沒有才退回 httpx，
# 所以兩個 logger 都要調。只動自己這支測試會吵的 logger，不碰全域設定。
for _noisy in ("httpx", "httpx2"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


class ChatRouteTestCase(unittest.TestCase):
    """共用 setUp：合成 fixture DB ＋ 已啟用的聊天設定 ＋ 假 LLM 後端。"""

    config = coach_chat.ChatConfig(api_key_present=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "test.db"
        build_fixture_db(self.db_path).close()
        # 前端資產不是本測試的主題，掛一個空目錄避免 create_app 印警告
        assets_dir = Path(self._tmp.name) / "assets"
        assets_dir.mkdir()

        self.app = create_app(str(self.db_path), assets_dir=assets_dir, chat_config=self.config)
        self.backend = FakeBackend([])
        self.app.dependency_overrides[routes_chat.get_backend] = lambda: self.backend

    def client(self, client=LOCAL_CLIENT):
        return TestClient(self.app, client=client)

    def new_conversation(self) -> int:
        return self.client().post("/api/chat/conversations").json()["id"]


# --- 來源限制 ---


class TestClientRestriction(ChatRouteTestCase):
    def test_loopback_is_allowed(self):
        self.assertEqual(self.client().get("/api/chat/conversations").status_code, 200)

    def test_ipv6_loopback_is_allowed(self):
        response = self.client(client=("::1", 1)).get("/api/chat/conversations")
        self.assertEqual(response.status_code, 200)

    def test_private_network_is_rejected_by_default(self):
        response = self.client(client=("192.168.1.5", 1)).get("/api/chat/conversations")
        self.assertEqual(response.status_code, 403)

    def test_unparseable_host_is_rejected(self):
        """fail closed：TestClient 預設的 "testclient" 不是 IP，必須被擋。"""
        response = self.client(client=("testclient", 1)).get("/api/chat/conversations")
        self.assertEqual(response.status_code, 403)

    def test_public_address_is_rejected_even_with_the_flag_on(self):
        app = create_app(
            str(self.db_path),
            assets_dir=Path(self._tmp.name) / "assets",
            chat_config=coach_chat.ChatConfig(api_key_present=True, allow_private_network=True),
        )
        response = TestClient(app, client=("8.8.8.8", 1)).get("/api/chat/conversations")
        self.assertEqual(response.status_code, 403)

    def test_status_stays_200_for_a_disallowed_client(self):
        """面板要能解釋「為什麼不能用」，所以 /status 不套 403。"""
        response = self.client(client=("192.168.1.5", 1)).get("/api/chat/status")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["client_allowed"])


class TestPrivateNetworkFlag(ChatRouteTestCase):
    config = coach_chat.ChatConfig(api_key_present=True, allow_private_network=True)

    def test_rfc1918_address_is_allowed(self):
        response = self.client(client=("192.168.1.5", 1)).get("/api/chat/conversations")
        self.assertEqual(response.status_code, 200)

    def test_tailscale_cgnat_address_is_allowed(self):
        """100.64.0.0/10 的 is_private 在 Python 3.12 為 False，必須另外放行。"""
        response = self.client(client=("100.101.102.103", 1)).get("/api/chat/conversations")
        self.assertEqual(response.status_code, 200)


# --- 未設金鑰 ---


class TestDisabledWithoutApiKey(ChatRouteTestCase):
    config = coach_chat.ChatConfig(api_key_present=False)

    def test_status_explains_why(self):
        payload = self.client().get("/api/chat/status").json()
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["reason"], "未設定 ANTHROPIC_API_KEY")
        self.assertTrue(payload["client_allowed"])
        self.assertEqual(payload["model"], coach_chat.DEFAULT_MODEL)

    def test_client_restriction_wins_over_the_missing_key(self):
        """「所有端點先過來源限制」：非本機來源看到的是 403，不是 503。"""
        client = self.client(client=("192.168.1.5", 1))
        self.assertEqual(client.get("/api/chat/conversations").status_code, 403)
        self.assertEqual(
            client.post("/api/chat/conversations/1/messages", json={"text": "嗨"}).status_code,
            403,
        )

    def test_other_endpoints_return_503(self):
        client = self.client()
        self.assertEqual(client.get("/api/chat/conversations").status_code, 503)
        self.assertEqual(client.post("/api/chat/conversations").status_code, 503)
        self.assertEqual(client.get("/api/chat/conversations/1").status_code, 503)
        self.assertEqual(client.delete("/api/chat/conversations/1").status_code, 503)
        self.assertEqual(
            client.post("/api/chat/conversations/1/messages", json={"text": "嗨"}).status_code,
            503,
        )


# --- 對話 CRUD ---


class TestConversationEndpoints(ChatRouteTestCase):
    def test_status_reports_enabled(self):
        payload = self.client().get("/api/chat/status").json()
        self.assertEqual(
            payload,
            {
                "enabled": True,
                "reason": None,
                "model": coach_chat.DEFAULT_MODEL,
                "client_allowed": True,
            },
        )

    def test_create_list_and_delete(self):
        client = self.client()
        created = client.post("/api/chat/conversations")
        self.assertEqual(created.status_code, 201)
        conversation_id = created.json()["id"]
        self.assertIsNone(created.json()["title"])

        listed = client.get("/api/chat/conversations").json()["conversations"]
        self.assertEqual([c["id"] for c in listed], [conversation_id])

        self.assertEqual(
            client.delete(f"/api/chat/conversations/{conversation_id}").status_code, 204
        )
        self.assertEqual(client.get("/api/chat/conversations").json()["conversations"], [])

    def test_read_missing_conversation_returns_404(self):
        self.assertEqual(self.client().get("/api/chat/conversations/9999").status_code, 404)

    def test_delete_missing_conversation_returns_404(self):
        self.assertEqual(self.client().delete("/api/chat/conversations/9999").status_code, 404)

    def test_blank_text_returns_422(self):
        conversation_id = self.new_conversation()
        response = self.client().post(
            f"/api/chat/conversations/{conversation_id}/messages", json={"text": "   "}
        )
        self.assertEqual(response.status_code, 422)

    def test_missing_body_returns_422(self):
        conversation_id = self.new_conversation()
        response = self.client().post(f"/api/chat/conversations/{conversation_id}/messages")
        self.assertEqual(response.status_code, 422)

    def test_non_object_body_returns_422(self):
        conversation_id = self.new_conversation()
        response = self.client().post(
            f"/api/chat/conversations/{conversation_id}/messages", json=["今天該練嗎"]
        )
        self.assertEqual(response.status_code, 422)

    def test_message_to_missing_conversation_returns_404(self):
        response = self.client().post(
            "/api/chat/conversations/9999/messages", json={"text": "嗨"}
        )
        self.assertEqual(response.status_code, 404)


# --- SSE ---


def parse_sse(body: str) -> list[tuple[str, dict]]:
    """把 SSE 回應拆成 [(event, data), ...]，順便驗證框架格式。"""
    events = []
    for frame in body.split("\n\n"):
        if not frame.strip():
            continue
        lines = frame.split("\n")
        assert len(lines) == 2, f"一則事件應恰為 event: 與 data: 兩行，收到：{lines!r}"
        assert lines[0].startswith("event: "), lines[0]
        assert lines[1].startswith("data: "), lines[1]
        events.append((lines[0][len("event: ") :], json.loads(lines[1][len("data: ") :])))
    return events


class TestMessageStreaming(ChatRouteTestCase):
    def test_sse_framing_and_done_event(self):
        self.backend.rounds = [text_round("今天照課表練。")]
        conversation_id = self.new_conversation()

        response = self.client().post(
            f"/api/chat/conversations/{conversation_id}/messages", json={"text": "今天該練嗎？"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))

        events = parse_sse(response.text)
        self.assertEqual([name for name, _ in events], ["text", "done"])
        self.assertEqual(events[0][1], {"delta": "今天照課表練。"})
        self.assertEqual(events[1][1]["stop_reason"], "end_turn")
        self.assertIsInstance(events[1][1]["message_id"], int)

    def test_tool_events_are_streamed_then_persisted(self):
        self.backend.rounds = [
            tool_round([("toolu_1", "get_athlete_meta", {})]),
            text_round("查完了。"),
        ]
        conversation_id = self.new_conversation()

        response = self.client().post(
            f"/api/chat/conversations/{conversation_id}/messages", json={"text": "我是誰？"}
        )
        names = [name for name, _ in parse_sse(response.text)]
        self.assertEqual(names, ["tool_use", "tool_result", "text", "done"])

        stored = self.client().get(f"/api/chat/conversations/{conversation_id}").json()
        self.assertEqual(
            [m["role"] for m in stored["messages"]], ["user", "assistant", "user", "assistant"]
        )
        self.assertEqual(stored["title"], "我是誰？")

    def test_error_event_is_streamed_and_nothing_is_persisted(self):
        self.backend.rounds = [text_round("被截斷", stop_reason="max_tokens")]
        conversation_id = self.new_conversation()

        response = self.client().post(
            f"/api/chat/conversations/{conversation_id}/messages", json={"text": "今天該練嗎？"}
        )
        self.assertEqual(response.status_code, 200)
        events = parse_sse(response.text)
        self.assertEqual(events[-1][0], "error")

        stored = self.client().get(f"/api/chat/conversations/{conversation_id}").json()
        self.assertEqual(stored["messages"], [])

    def test_newlines_in_a_reply_stay_inside_one_data_line(self):
        """換行必須被 json.dumps 逸出，否則一則事件會被切成兩半。"""
        self.backend.rounds = [text_round("第一行\n第二行")]
        conversation_id = self.new_conversation()

        response = self.client().post(
            f"/api/chat/conversations/{conversation_id}/messages", json={"text": "換行測試"}
        )
        events = parse_sse(response.text)
        self.assertEqual(events[0][1]["delta"], "第一行\n第二行")


# --- app 啟動時建表 ---


class TestSchemaIsEnsuredOnStartup(unittest.TestCase):
    def test_create_app_creates_the_chat_tables(self):
        """FastAPI 進程原本從不執行 schema.sql，聊天的兩張表要靠 create_app 補上。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            conn = get_connection(db_path)
            conn.execute("DROP TABLE coach_messages")
            conn.execute("DROP TABLE coach_conversations")
            conn.commit()
            conn.close()

            assets_dir = Path(tmp) / "assets"
            assets_dir.mkdir()
            create_app(str(db_path), assets_dir=assets_dir, chat_config=_disabled_config())

            conn = get_connection(db_path)
            try:
                tables = {
                    row["name"]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
            finally:
                # ⚠️ 必須在 TemporaryDirectory 收掉之前關閉：Windows 不允許刪除
                # 仍被開啟的檔案（WinError 32），用 addCleanup 會晚一步而爆掉。
                conn.close()
        self.assertIn("coach_conversations", tables)
        self.assertIn("coach_messages", tables)


def _disabled_config() -> coach_chat.ChatConfig:
    return coach_chat.ChatConfig(api_key_present=False)


class TestStoreIsReachableThroughTheApi(ChatRouteTestCase):
    def test_conversation_created_directly_shows_up_in_the_api(self):
        conn = get_connection(self.db_path)
        self.addCleanup(conn.close)
        athlete_id = conn.execute(
            "SELECT id FROM athlete_profile ORDER BY id LIMIT 1"
        ).fetchone()["id"]
        conversation = coach_chat_store.create_conversation(conn, athlete_id)

        listed = self.client().get("/api/chat/conversations").json()["conversations"]
        self.assertEqual([c["id"] for c in listed], [conversation["id"]])


if __name__ == "__main__":
    unittest.main()
