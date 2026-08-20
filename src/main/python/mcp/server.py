"""Running Coach 的 MCP server——把 Phase 1 資料查詢 + Phase 2 規則引擎包成
AI 對話可呼叫的 tools（Phase 3 第一步）。

用法（stdio transport，供 Claude Desktop / Claude Code 註冊使用）：
    python -m src.main.python.mcp.server --db-path output/running_coach.db

註冊到 Claude Code：
    claude mcp add running-coach -- <python 絕對路徑> -m src.main.python.mcp.server --db-path <db 絕對路徑>

本模組是薄封裝層：每個 tool 呼叫既有 `services/` 模組的查詢/規則函式，
不在這裡新增任何規則判斷。db 連線透過 lifespan 機制在 server 啟動時
建立一次並注入每個 tool 呼叫，比照官方 MCP Python SDK（v2）建議的做法，
不在每個 tool 各自開連線。

athlete_id 慣例沿用 dashboard_queries.resolve_athlete_id()：tool 參數
athlete_id 為 optional，未提供時預設取資料庫中第一位 athlete（單人自用情境）。

寫入型 tool 只有一個：generate_training_plan。其餘皆為唯讀查詢，
description 已明確標示副作用。
"""

from __future__ import annotations

import argparse
import datetime
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Context

from src.main.python.models.db import get_connection
from src.main.python.services import dashboard_queries
from src.main.python.services import training_load_queries
from src.main.python.services import training_plan_generator
from src.main.python.services import training_plan_store

# --- lifespan：server 啟動時開一次資料庫連線，注入每個 tool 呼叫 ---


@dataclass
class AppContext:
    db: sqlite3.Connection


_db_path: str | None = None


@asynccontextmanager
async def app_lifespan(server: MCPServer) -> AsyncIterator[AppContext]:
    if _db_path is None:
        raise RuntimeError("db_path 尚未設定，請透過 main() 的 --db-path 啟動本模組")
    conn = get_connection(_db_path)
    try:
        yield AppContext(db=conn)
    finally:
        conn.close()


mcp = MCPServer("running-coach", lifespan=app_lifespan)


def _serialize(value: Any) -> Any:
    """把回傳結構裡的 datetime.date／datetime.datetime 轉成 ISO 字串，
    其餘型別原樣遞迴保留（JSON 沒有日期型別，tool 回傳前必須先轉換）。

    services/ 底下多數函式的回傳已經是 ISO 字串（DB 欄位本身存字串），
    但 readiness.assess_readiness() 與 periodization_scheduler.generate_schedule()
    的輸出用的是真正的 datetime.date 物件，故仍需要這層轉換，不能省略。
    """
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, tuple):
        return [_serialize(v) for v in value]
    return value


def _parse_date(value: str) -> datetime.date:
    return datetime.date.fromisoformat(value)


def _conn(ctx: Context[AppContext]) -> sqlite3.Connection:
    return ctx.request_context.lifespan_context.db


# --- Tools（讀取型） ---


@mcp.tool()
def get_athlete_meta(ctx: Context[AppContext], athlete_id: int | None = None) -> dict:
    """取得學員基本資料、各項身體指標的可用日期範圍、預設可選時間區間清單。

    athlete_id 省略時預設取資料庫中第一位學員（單人自用情境）。
    """
    return _serialize(dashboard_queries.get_meta(_conn(ctx), athlete_id))


@mcp.tool()
def list_running_sessions(
    ctx: Context[AppContext],
    athlete_id: int | None = None,
    range_key: str = "30d",
) -> dict:
    """列出指定時間區間內的跑步活動（依開始時間新到舊排序）。

    range_key 合法值：'7d'／'30d'／'90d'／'1y'／'all'，或
    'custom:YYYY-MM-DD:YYYY-MM-DD' 自訂區間。
    """
    return _serialize(dashboard_queries.list_sessions(_conn(ctx), athlete_id, range_key))


@mcp.tool()
def get_session_detail(
    ctx: Context[AppContext],
    session_id: int,
    athlete_id: int | None = None,
) -> dict | None:
    """取得單場活動的完整細節：摘要、心率區間分布、每公里分圈、
    逐秒配速/心率、心率漂移。查無此活動時回傳 None。
    """
    return _serialize(dashboard_queries.get_session_detail(_conn(ctx), session_id, athlete_id))


@mcp.tool()
def get_wellness_trend(
    ctx: Context[AppContext],
    athlete_id: int | None = None,
    range_key: str = "30d",
) -> dict:
    """取得指定時間區間內的每日身體數據趨勢（HRV、睡眠、靜止心率、
    全天壓力、Training Readiness 等）。缺資料的日期不補值。
    """
    return _serialize(dashboard_queries.get_wellness_trend(_conn(ctx), athlete_id, range_key))


@mcp.tool()
def get_recovery_impact(
    ctx: Context[AppContext],
    athlete_id: int | None = None,
    range_key: str = "30d",
) -> dict:
    """取得訓練日與隔天身體數據變化的關聯（HRV/靜止心率/Training Readiness
    的隔天變化量），只呈現原始數值，不做「這樣好不好」的判讀。
    """
    return _serialize(dashboard_queries.get_recovery_impact(_conn(ctx), athlete_id, range_key))


@mcp.tool()
def get_readiness_status(
    ctx: Context[AppContext],
    start_date: str,
    end_date: str,
    athlete_id: int | None = None,
) -> list[dict]:
    """取得指定日期區間內每日的恢復狀態判讀（readiness: low/normal），
    綜合連續訓練天數、訓練負荷趨勢（TSB）、HRV 相對 7 日均值變化三個維度，
    附觸發原因。只輸出判讀結果，不會修改任何課表。

    start_date／end_date 格式為 YYYY-MM-DD。athlete_id 省略時預設取
    資料庫中第一位學員。
    """
    resolved_athlete_id = dashboard_queries.resolve_athlete_id(_conn(ctx), athlete_id)
    if resolved_athlete_id is None:
        return []
    result = training_load_queries.compute_readiness_for_athlete(
        _conn(ctx),
        resolved_athlete_id,
        _parse_date(start_date),
        _parse_date(end_date),
    )
    return _serialize(result)


@mcp.tool()
def get_active_training_plan(ctx: Context[AppContext], athlete_id: int | None = None) -> list[dict]:
    """取得學員目前生效中的完整訓練課表（含系統產生與外部課表來源）。

    athlete_id 省略時預設取資料庫中第一位學員。
    """
    resolved_athlete_id = dashboard_queries.resolve_athlete_id(_conn(ctx), athlete_id)
    if resolved_athlete_id is None:
        return []
    return _serialize(training_plan_store.get_active_schedule(_conn(ctx), resolved_athlete_id))


# --- Tools（寫入型，會修改資料庫） ---


@mcp.tool()
def generate_training_plan(
    ctx: Context[AppContext],
    start_date: str,
    total_weeks: int,
    days_per_week: int,
    is_first_marathon: bool = False,
    athlete_id: int | None = None,
) -> dict:
    """⚠️ 會寫入資料庫：依學員的歷史活動計算 VDOT/配速，產生一份全新的
    週期化訓練課表並寫入 training_plan 表。若該學員已有生效中的課表，
    重疊日期的舊排程會被標記為已取代（保留版本歷史，不刪除），本次產生
    的課表成為新的生效版本。

    days_per_week 支援 3（低頻率：1 LSD+1 品質課+1 easy）或 4/5/6
    （標準情況）。若學員缺乏可用的近期成績/心率資料，VDOT 無法推算時
    會明確回傳 available=False 且不寫入任何資料。

    start_date 格式為 YYYY-MM-DD。athlete_id 省略時預設取資料庫中
    第一位學員。
    """
    resolved_athlete_id = dashboard_queries.resolve_athlete_id(_conn(ctx), athlete_id)
    if resolved_athlete_id is None:
        return {"available": False, "reason": "資料庫中沒有任何學員資料"}
    result = training_plan_generator.generate_and_save_plan(
        _conn(ctx),
        resolved_athlete_id,
        start_date=_parse_date(start_date),
        total_weeks=total_weeks,
        days_per_week=days_per_week,
        is_first_marathon=is_first_marathon,
    )
    return _serialize(result)


def main() -> None:
    global _db_path

    ap = argparse.ArgumentParser(
        description="啟動 Running Coach 的 MCP server（stdio transport）。"
    )
    ap.add_argument("--db-path", required=True, help="SQLite 資料庫路徑")
    args = ap.parse_args()

    if not Path(args.db_path).exists():
        raise SystemExit(f"找不到資料庫檔案：{args.db_path}")

    _db_path = args.db_path
    mcp.run()


if __name__ == "__main__":
    main()
