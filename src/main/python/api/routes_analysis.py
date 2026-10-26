"""總覽「分析近況」的 HTTP 路由層（`/api/analysis*`）。

按下按鈕 → 在背景啟動 Claude Code 無互動 session（services/coach_analysis.py），
以 SSE 串流回傳「查了哪些工具」與最終報告。2026-10-27（N-36 決策 4）起報告存成
對話裡的一回合（kind='analysis'），analysis_reports 只記執行紀錄。

⚠️ 來源限制比照聊天端點：分析會把個人健康資料送出本機、消耗 Claude 額度，
**預設只接受本機**，放行私有網段同樣看 `RC_CHAT_ALLOW_PRIVATE_NETWORK=1`。
同一時間只跑一個 Claude Code 子程序：與 session 模式的聊天共用
`claude_session.SESSION_LOCK`，忙碌回 409。
"""

from __future__ import annotations

import datetime
import sqlite3
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from src.main.python.api import client_access
from src.main.python.api.routes_chat import SSE_HEADERS, _sse_frame, get_today
from src.main.python.api.routes_dashboard import get_conn
from src.main.python.services import analysis_report_store as store
from src.main.python.services import claude_session, coach_analysis, coach_chat_store
from src.main.python.services import dashboard_queries as queries
from src.main.python.services.coach_analysis import ANALYSIS_PROMPT, AnalysisConfig

router = APIRouter(prefix="/api/analysis", tags=["analysis"])

AthleteParam = Query(default=None, description="要查詢的 athlete_id，未指定時用資料庫第一位")


# 由 app.py 以 dependency_overrides 覆寫成真正的設定
def get_analysis_config() -> AnalysisConfig:  # pragma: no cover - 一定會被覆寫
    raise RuntimeError("分析設定未載入：請透過 app.create_app() 建立應用程式")


def get_runner() -> claude_session.Runner:
    """預設真的啟動 claude；測試以 dependency_overrides 換成假的逐行輸出。"""
    return claude_session.subprocess_runner


def require_analysis_client(
    request: Request, config: AnalysisConfig = Depends(get_analysis_config)
) -> None:
    client_access.require_client(
        request,
        config.allow_private_network,
        "AI 分析僅限本機使用。要讓區網裝置使用，"
        "請在啟動 server 的環境設定 RC_CHAT_ALLOW_PRIVATE_NETWORK=1。",
    )


def _resolve_athlete_id(conn: sqlite3.Connection, athlete_id: int | None) -> int:
    resolved = queries.resolve_athlete_id(conn, athlete_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="資料庫中沒有任何學員資料")
    return resolved


@router.get("/status")
def read_status(
    request: Request, config: AnalysisConfig = Depends(get_analysis_config)
) -> dict:
    return {
        "enabled": config.enabled,
        "reason": config.disabled_reason,
        "client_allowed": client_access.is_client_allowed(request, config.allow_private_network),
        "running": claude_session.SESSION_LOCK.locked(),
    }


@router.get("", dependencies=[Depends(require_analysis_client)])
def list_reports(
    athlete_id: int | None = AthleteParam, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    return {"reports": store.list_reports(conn, _resolve_athlete_id(conn, athlete_id))}


@router.get("/{report_id}", dependencies=[Depends(require_analysis_client)])
def read_report(
    report_id: int,
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    report = store.get_report(conn, _resolve_athlete_id(conn, athlete_id), report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"找不到 id={report_id} 的分析")
    return report


def _target_conversation(
    conn: sqlite3.Connection, athlete_id: int, conversation_id: int | None, today: datetime.date
) -> int:
    """報告要放進的對話：有給就用（存在與否由呼叫端先驗），沒給就用最近更新的，
    都沒有就新開。對話還沒有標題（例如剛按「＋ 新對話」）時補上「分析近況 MM/DD」。
    """
    title = coach_analysis.conversation_title(today)
    if conversation_id is not None:
        target = conversation_id
    else:
        target = coach_chat_store.latest_conversation_id(conn, athlete_id)
        if target is None:
            return coach_chat_store.create_conversation(conn, athlete_id, title=title)["id"]
    coach_chat_store.set_title_if_empty(conn, target, title)
    return target


@router.post("", dependencies=[Depends(require_analysis_client)])
def start_analysis(
    athlete_id: int | None = AthleteParam,
    conversation_id: int | None = Query(
        default=None, description="報告要放進的對話；未指定時用最近更新的對話，沒有就新開"
    ),
    conn: sqlite3.Connection = Depends(get_conn),
    config: AnalysisConfig = Depends(get_analysis_config),
    runner: claude_session.Runner = Depends(get_runner),
    today: datetime.date = Depends(get_today),
) -> StreamingResponse:
    """啟動一次分析並以 SSE 串流事件：
    start{id, conversation_id} → tool_use／tool_result／text／denied … →
    done{…, id, conversation_id, message_id} 或 error{message, id}。

    啟用與否、對話是否存在、是否已有 Claude Code 在跑，都在開始串流**之前**檢查
    （開始後只剩 error 事件可用）。成功時報告存成該對話的一回合（kind='analysis'，
    一回合一交易）；失敗不進對話，只在 analysis_reports 記錯誤。
    """
    if not config.enabled:
        raise HTTPException(status_code=503, detail=config.disabled_reason)
    resolved = _resolve_athlete_id(conn, athlete_id)
    if conversation_id is not None and not coach_chat_store.conversation_exists(
        conn, resolved, conversation_id
    ):
        raise HTTPException(status_code=404, detail=f"找不到 id={conversation_id} 的對話")
    if not claude_session.SESSION_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=claude_session.BUSY_MESSAGE)

    try:
        target = _target_conversation(conn, resolved, conversation_id, today)
        report_id = store.create_report(conn, resolved, ANALYSIS_PROMPT, target)
        prompt = coach_analysis.build_prompt(conn, resolved, today)
    except BaseException:
        claude_session.SESSION_LOCK.release()
        raise

    def event_stream() -> Iterator[str]:
        tools: list[dict[str, Any]] = []
        by_id: dict[str, dict[str, Any]] = {}
        texts: list[str] = []
        recorder = claude_session.TurnRecorder()
        finished = False
        try:
            yield _sse_frame("start", {"id": report_id, "conversation_id": target})
            for ev in coach_analysis.run_analysis(config, prompt, runner, recorder):
                if ev.event == "tool_use":
                    entry = {"name": ev.data["name"], "input": ev.data["input"], "is_error": False}
                    tools.append(entry)
                    by_id[ev.data.get("id") or ""] = entry
                elif ev.event == "tool_result" and ev.data.get("id") in by_id:
                    by_id[ev.data["id"]]["is_error"] = ev.data["is_error"]
                elif ev.event == "text":
                    texts.append(ev.data["text"])
                elif ev.event == "done":
                    report = ev.data["report"] or "\n\n".join(texts)
                    message_id = None
                    if not ev.data["is_error"]:
                        message_ids = coach_chat_store.append_turn(
                            conn, target, coach_analysis.analysis_turn(recorder, report, today),
                            kind=coach_chat_store.KIND_ANALYSIS,
                        )
                        message_id = message_ids[-1]
                    store.finish_report(
                        conn, report_id, report_text=report, tools=tools,
                        error="Claude Code 回報錯誤" if ev.data["is_error"] else None,
                        num_turns=ev.data.get("num_turns"), duration_ms=ev.data.get("duration_ms"),
                    )
                    finished = True
                    ev.data.update(id=report_id, conversation_id=target, message_id=message_id)
                elif ev.event == "error":
                    store.finish_report(
                        conn, report_id, report_text="\n\n".join(texts) or None,
                        tools=tools, error=ev.data["message"],
                    )
                    finished = True
                    ev.data["id"] = report_id
                yield _sse_frame(ev.event, ev.data)
        finally:
            # 前端中途關掉頁面（產生器被關閉）時也要收尾，不留 running
            if not finished:
                store.finish_report(conn, report_id, report_text="\n\n".join(texts) or None,
                                    tools=tools, error="分析在完成前被中斷")
            claude_session.SESSION_LOCK.release()

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)
