"""總覽「AI 分析」的 HTTP 路由層（`/api/analysis*`）。

按下按鈕 → 在背景啟動 Claude Code 無互動 session（services/coach_analysis.py），
以 SSE 串流回傳「查了哪些工具」與最終報告，報告存進 analysis_reports。

⚠️ 來源限制比照聊天端點：分析會把個人健康資料送出本機、消耗 Claude 額度，
**預設只接受本機**，放行私有網段同樣看 `RC_CHAT_ALLOW_PRIVATE_NETWORK=1`。
同一時間只跑一個分析（Claude Code 子程序吃資源，重複按按鈕不該疊出好幾個）。
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from src.main.python.api import client_access
from src.main.python.api.routes_chat import _sse_frame
from src.main.python.api.routes_dashboard import get_conn
from src.main.python.services import analysis_report_store as store
from src.main.python.services import coach_analysis
from src.main.python.services import dashboard_queries as queries
from src.main.python.services.coach_analysis import AnalysisConfig

router = APIRouter(prefix="/api/analysis", tags=["analysis"])

AthleteParam = Query(default=None, description="要查詢的 athlete_id，未指定時用資料庫第一位")

# 按鈕送出的分析問題（唯一來源；前端不帶 prompt）。刻意寫明「先查哪些」，
# 讓模型呼叫工具取得含信賴度的事實，而不是憑印象作答。
ANALYSIS_PROMPT = (
    "請分析我最近一週的訓練狀況與身體狀況：先查近期跑步場次與單場品質、訓練負荷與恢復狀態、"
    "HRV 等身體數據趨勢、本週課表與執行情形、近期軟性輸入（context notes），"
    "再整理出：目前狀態、需要注意的地方、接下來幾天的建議。"
)

# 同一時間只允許一個分析在跑
_running_lock = threading.Lock()


# 由 app.py 以 dependency_overrides 覆寫成真正的設定
def get_analysis_config() -> AnalysisConfig:  # pragma: no cover - 一定會被覆寫
    raise RuntimeError("分析設定未載入：請透過 app.create_app() 建立應用程式")


def get_runner() -> coach_analysis.Runner:
    """預設真的啟動 claude；測試以 dependency_overrides 換成假的逐行輸出。"""
    return coach_analysis.subprocess_runner


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
        "running": _running_lock.locked(),
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


@router.post("", dependencies=[Depends(require_analysis_client)])
def start_analysis(
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
    config: AnalysisConfig = Depends(get_analysis_config),
    runner: coach_analysis.Runner = Depends(get_runner),
) -> StreamingResponse:
    """啟動一次分析並以 SSE 串流事件：start → tool_use／tool_result／text／denied … → done 或 error。

    啟用與否、是否已有分析在跑，都在開始串流**之前**檢查（開始後只剩 error 事件可用）。
    """
    if not config.enabled:
        raise HTTPException(status_code=503, detail=config.disabled_reason)
    resolved = _resolve_athlete_id(conn, athlete_id)
    if not _running_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有一個分析正在進行，請等它完成")

    try:
        report_id = store.create_report(conn, resolved, ANALYSIS_PROMPT)
    except Exception:
        _running_lock.release()
        raise

    def event_stream() -> Iterator[str]:
        tools: list[dict[str, Any]] = []
        by_id: dict[str, dict[str, Any]] = {}
        texts: list[str] = []
        finished = False
        try:
            yield _sse_frame("start", {"id": report_id})
            for ev in coach_analysis.run_analysis(config, ANALYSIS_PROMPT, runner):
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
                    store.finish_report(
                        conn, report_id, report_text=report, tools=tools,
                        error="Claude Code 回報錯誤" if ev.data["is_error"] else None,
                        num_turns=ev.data.get("num_turns"), duration_ms=ev.data.get("duration_ms"),
                    )
                    finished = True
                    ev.data["id"] = report_id
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
            _running_lock.release()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
