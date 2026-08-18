"""Dashboard 的 HTTP 路由層。

這一層刻意很薄：解析 query 參數、開/關資料庫連線、把結果交給
`dashboard_queries` 的純函式，然後回傳。**任何 SQL 或計算邏輯都不該出現在這裡**——
放在查詢層才能用 unittest 直接測，不必起 HTTP server。

回傳格式完全依照 `docs/dev/DASHBOARD_TASKS.md` 的 API Contract。
"""

from __future__ import annotations

import sqlite3
from typing import Callable, Iterator

from fastapi import APIRouter, Depends, HTTPException, Query

from src.main.python.services import dashboard_queries as queries

router = APIRouter(prefix="/api", tags=["dashboard"])

# range 的合法值直接取自查詢層，避免兩邊各維護一份清單而漂移
_RANGE_VALUES = tuple(queries.RANGE_DAYS)


def make_connection_dependency(db_path: str) -> Callable[[], Iterator[sqlite3.Connection]]:
    """產生一個「每個請求開一條連線」的 FastAPI dependency。

    db_path 由 app.py 從 CLI 參數傳進來（不 hardcode，見 AGENTS.md）。
    每個請求各開各的連線，用完即關，確保不同請求之間不共用連線；
    本服務是單人區網自用，連線成本相對於正確性完全不是問題。

    ⚠️ `check_same_thread=False` 是必要的，不是圖方便省略檢查：FastAPI 用
    `anyio.to_thread.run_sync` 執行這種同步的 generator dependency 時，
    `yield` 之前（開連線）與 `finally`（關連線）**不保證落在同一條 worker
    thread**——執行緒池會重複調度 thread，同一個 request 的兩段可能被排到
    不同 thread 執行。實測已重現：`conn.close()` 在跟 `sqlite3.connect()`
    不同的 thread 執行，導致 `sqlite3.ProgrammingError`。因為這個連線的
    生命週期就只在單一 request 內（開→查→關，不跨 request 共用、不並行
    操作同一個 conn），關掉同執行緒檢查是安全的。
    """

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    return get_conn


# 由 app.py 在建立 app 時以 dependency_overrides 覆寫成真正的連線來源。
# 這個預設實作只是佔位，直接呼叫會報錯而不是連到某個寫死的路徑。
def get_conn() -> Iterator[sqlite3.Connection]:  # pragma: no cover - 一定會被覆寫
    raise RuntimeError("資料庫連線未設定：請透過 app.create_app(db_path=...) 建立應用程式")


RangeParam = Query(default=queries.DEFAULT_RANGE, description=f"時間範圍，合法值：{', '.join(_RANGE_VALUES)}")
AthleteParam = Query(default=None, description="要查詢的 athlete_id，未指定時用資料庫第一位")


def _validate_range(range_key: str) -> str:
    if range_key not in queries.RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"不支援的 range：{range_key}（合法值：{', '.join(_RANGE_VALUES)}）",
        )
    return range_key


@router.get("/meta")
def read_meta(
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    return queries.get_meta(conn, athlete_id)


@router.get("/sessions")
def read_sessions(
    range: str = RangeParam,
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    return queries.list_sessions(conn, athlete_id, _validate_range(range))


@router.get("/sessions/{session_id}")
def read_session_detail(
    session_id: int,
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    detail = queries.get_session_detail(conn, session_id, athlete_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"找不到 id={session_id} 的活動")
    return detail


@router.get("/wellness-trend")
def read_wellness_trend(
    range: str = RangeParam,
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    return queries.get_wellness_trend(conn, athlete_id, _validate_range(range))


@router.get("/training-days")
def read_training_days(
    range: str = RangeParam,
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    return queries.get_training_days(conn, athlete_id, _validate_range(range))


@router.get("/recovery-impact")
def read_recovery_impact(
    range: str = RangeParam,
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    return queries.get_recovery_impact(conn, athlete_id, _validate_range(range))
