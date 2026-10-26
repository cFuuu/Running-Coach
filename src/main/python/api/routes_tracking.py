"""追蹤事項的 HTTP 路由層（`/api/chat/tracking*`）——TODO N-35 ②④。

給 AI 教練面板的追蹤橫條用：列出進行中的事項（附程式算好的「這次該做的事」），
以及「追蹤／不用」「已痊癒／不再追蹤」四顆按鈕（Fu 2026-10-26 決策 3：
不想跟 AI 解釋的時候直接按）。追問紀錄（followed_up／mentioned／stale_asked）
只有教練在對話裡會記，按鈕不開放。

來源限制沿用聊天端點的 `routes_chat.require_chat_client`（預設只接受本機），
但**不要求聊天啟用**：沒設好聊天後端時，面板仍要能看與結案追蹤事項。

本層刻意很薄：狀態轉換在 services/context_notes_store.py，action 計算在
services/coach_tracking.py，與 MCP 工具共用同一份。
"""

from __future__ import annotations

import datetime
import sqlite3
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from src.main.python.api.routes_chat import require_chat_client
from src.main.python.api.routes_dashboard import get_conn
from src.main.python.services import coach_tracking, context_notes_store
from src.main.python.services import dashboard_queries as queries

router = APIRouter(
    prefix="/api/chat/tracking",
    tags=["chat"],
    dependencies=[Depends(require_chat_client)],
)

AthleteParam = Query(default=None, description="要查詢的 athlete_id，未指定時用資料庫第一位")

# 面板按鈕可用的動作（MCP 的 update_tracking_item 另有 followed_up 等紀錄型動作）
PANEL_ACTIONS = ("accept", "decline", "resolve", "stop")


def get_today() -> datetime.date:
    """「今天」取本機日期；獨立成 dependency 讓測試可以釘住日期。"""
    return datetime.date.today()


def _resolve_athlete_id(conn: sqlite3.Connection, athlete_id: int | None) -> int:
    resolved = queries.resolve_athlete_id(conn, athlete_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="資料庫中沒有任何學員資料")
    return resolved


@router.get("")
def list_tracking_items(
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
    today: datetime.date = Depends(get_today),
) -> dict[str, Any]:
    """進行中（待同意＋追蹤中）的追蹤事項，形狀同 MCP 的 get_tracking_items。"""
    resolved = _resolve_athlete_id(conn, athlete_id)
    return {"items": coach_tracking.get_open_items(conn, resolved, today)}


@router.post("/{item_id}")
def update_tracking_item(
    item_id: int,
    payload: dict[str, Any] = Body(default_factory=dict),
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
    today: datetime.date = Depends(get_today),
) -> dict[str, Any]:
    """body：{"action": "accept"|"decline"|"resolve"|"stop"}，回傳更新後的事項。

    404：找不到該事項（或它是一般情境筆記）；422：動作不在上列；
    400：該事項目前的狀態不能做這個動作（例如已結案）。
    """
    action = payload.get("action")
    if action not in PANEL_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"action 必須是 {'／'.join(PANEL_ACTIONS)} 之一，收到：{action}",
        )
    resolved = _resolve_athlete_id(conn, athlete_id)
    try:
        item = context_notes_store.apply_tracking_action(conn, resolved, item_id, action, today)
    except context_notes_store.TrackingItemNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from None
    except context_notes_store.TrackingActionError as exc:
        raise HTTPException(status_code=400, detail=exc.args[0]) from None
    return coach_tracking.to_public_item(item, today)
