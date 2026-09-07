"""AI 教練聊天的 HTTP 路由層（`/api/chat/*`）——DASHBOARD_TASKS.md D.3。

這一層同樣刻意很薄：來源限制、參數驗證、把事件包成 SSE 框架。對話迴圈本身在
`services/coach_chat.py`，落地在 `services/coach_chat_store.py`，兩者都不 import
fastapi，可以用 unittest 直接測。

⚠️ **來源限制（D.0 決策 4）**：dashboard 其餘端點的「區網無驗證」紅線不變，
但聊天會花錢、且會把個人健康資料送出本機，所以**預設只接受本機**，要放行
私有網段必須明確設 `RC_CHAT_ALLOW_PRIVATE_NETWORK=1`。瀏覽器端零密鑰。
未來要在外面用：把驗證放在 app **前面**（Tailscale 為首選、Cloudflare Tunnel
＋Access 次之），**不在 app 內做帳號系統**。
"""

from __future__ import annotations

import ipaddress
import json
import sqlite3
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from src.main.python.api.routes_dashboard import get_conn
from src.main.python.services import coach_chat, coach_chat_store
from src.main.python.services import dashboard_queries as queries
from src.main.python.services.coach_chat import ChatConfig

router = APIRouter(prefix="/api/chat", tags=["chat"])

AthleteParam = Query(default=None, description="要查詢的 athlete_id，未指定時用資料庫第一位")

# Tailscale 的 CGNAT 網段。Python 3.12 的 ip_address.is_private 對這一段回 False
# （它不在 RFC1918 私有位址裡），所以必須明列，否則從 Tailscale 連進來會被擋。
# Tailscale 的 IPv6 位址落在 fd7a:115c:a1e0::/48，屬 unique-local，is_private
# 本來就為 True，不必另外列。
_TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")


# 由 app.py 在建立 app 時以 dependency_overrides 覆寫成真正的設定。
# 這個預設實作只是佔位，直接呼叫會報錯而不是回一份猜的設定。
def get_chat_config() -> ChatConfig:  # pragma: no cover - 一定會被覆寫
    raise RuntimeError("聊天設定未載入：請透過 app.create_app() 建立應用程式")


def get_backend(config: ChatConfig = Depends(get_chat_config)) -> coach_chat.LLMBackend:
    """預設用真的 Anthropic 後端；測試以 dependency_overrides 換成 FakeBackend。"""
    return coach_chat.AnthropicBackend(model=config.model, max_tokens=config.max_tokens)


def is_client_allowed(request: Request, config: ChatConfig) -> bool:
    """判斷這個請求的來源位址可不可以用聊天端點。

    **Fail closed**：位址解析不出來（沒有 client、或不是合法 IP）一律拒絕。
    Starlette 的 `TestClient` 預設 host 是字串 `"testclient"`，因此測試必須傳
    `client=("127.0.0.1", 1)` 才會被放行——這正是這條規則該有的行為。

    uvicorn 只信任來自 127.0.0.1 的 `X-Forwarded-For`，區網裝置無法靠偽造
    標頭把自己講成本機。
    """
    client = request.client
    if client is None or not client.host:
        return False
    try:
        ip = ipaddress.ip_address(client.host)
    except ValueError:
        return False

    if ip.is_loopback:  # 涵蓋 127.0.0.0/8 與 ::1
        return True
    if not config.allow_private_network:
        return False
    return ip.is_private or ip in _TAILSCALE_CGNAT


def require_chat_client(
    request: Request, config: ChatConfig = Depends(get_chat_config)
) -> None:
    if not is_client_allowed(request, config):
        raise HTTPException(
            status_code=403,
            detail=(
                "AI 教練僅限本機使用。要讓區網裝置使用，"
                "請在啟動 server 的環境設定 RC_CHAT_ALLOW_PRIVATE_NETWORK=1。"
            ),
        )


def require_chat_enabled(config: ChatConfig = Depends(get_chat_config)) -> ChatConfig:
    if not config.enabled:
        raise HTTPException(status_code=503, detail=config.disabled_reason)
    return config


def _resolve_athlete_id(conn: sqlite3.Connection, athlete_id: int | None) -> int:
    resolved = queries.resolve_athlete_id(conn, athlete_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="資料庫中沒有任何學員資料")
    return resolved


@router.get("/status")
def read_status(
    request: Request,
    config: ChatConfig = Depends(get_chat_config),
) -> dict[str, Any]:
    """面板啟動時先打這支，決定要不要開放輸入框、以及擋住時要顯示什麼理由。

    ⚠️ 這是**唯一**不套 403 的端點：來源不允許時仍回 200，否則面板連
    「為什麼不能用」都說不出口，只會看到一個沒有解釋的錯誤。
    """
    return {
        "enabled": config.enabled,
        "reason": config.disabled_reason,
        "model": config.model,
        "client_allowed": is_client_allowed(request, config),
    }


@router.get(
    "/conversations",
    dependencies=[Depends(require_chat_client), Depends(require_chat_enabled)],
)
def list_conversations(
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict[str, Any]:
    resolved = _resolve_athlete_id(conn, athlete_id)
    return {"conversations": coach_chat_store.list_conversations(conn, resolved)}


@router.post(
    "/conversations",
    status_code=201,
    dependencies=[Depends(require_chat_client), Depends(require_chat_enabled)],
)
def create_conversation(
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict[str, Any]:
    resolved = _resolve_athlete_id(conn, athlete_id)
    return coach_chat_store.create_conversation(conn, resolved)


@router.get(
    "/conversations/{conversation_id}",
    dependencies=[Depends(require_chat_client), Depends(require_chat_enabled)],
)
def read_conversation(
    conversation_id: int,
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict[str, Any]:
    resolved = _resolve_athlete_id(conn, athlete_id)
    conversation = coach_chat_store.get_conversation(conn, resolved, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail=f"找不到 id={conversation_id} 的對話")
    return conversation


@router.delete(
    "/conversations/{conversation_id}",
    status_code=204,
    dependencies=[Depends(require_chat_client), Depends(require_chat_enabled)],
)
def delete_conversation(
    conversation_id: int,
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    resolved = _resolve_athlete_id(conn, athlete_id)
    if not coach_chat_store.delete_conversation(conn, resolved, conversation_id):
        raise HTTPException(status_code=404, detail=f"找不到 id={conversation_id} 的對話")
    return Response(status_code=204)


def _sse_frame(event: str, data: dict[str, Any]) -> str:
    """一則 SSE 事件：`event:` ＋單行 `data:` JSON ＋空行。

    `data` 必須是**單行**——JSON 裡的換行由 json.dumps 轉成 `\\n` 逸出序列，
    因此永遠不會出現真正的換行把一則事件切成兩半。
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post(
    "/conversations/{conversation_id}/messages",
    dependencies=[Depends(require_chat_client)],
)
async def post_message(
    conversation_id: int,
    payload: dict[str, Any] = Body(default_factory=dict),
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
    config: ChatConfig = Depends(require_chat_enabled),
    backend: coach_chat.LLMBackend = Depends(get_backend),
) -> StreamingResponse:
    """送出一則提問，以 SSE 串流回覆。

    參數與對話存在與否在開始串流**之前**驗完（一旦開始串流就只剩 200＋
    `event: error` 可用）。回合中途的任何錯誤都走 `error` 事件，且該回合不落地。
    """
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=422, detail="text 不可為空")

    resolved = _resolve_athlete_id(conn, athlete_id)
    if not coach_chat_store.conversation_exists(conn, resolved, conversation_id):
        raise HTTPException(status_code=404, detail=f"找不到 id={conversation_id} 的對話")

    async def event_stream() -> AsyncIterator[str]:
        async for event in coach_chat.stream_turn(
            conn=conn,
            config=config,
            backend=backend,
            conversation_id=conversation_id,
            text=text,
        ):
            yield _sse_frame(event.event, event.data)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # 前面若擺了 nginx／Cloudflare Tunnel，沒有這個標頭串流會被緩衝成
            # 一次送完，逐字輸出就沒了。
            "X-Accel-Buffering": "no",
        },
    )
