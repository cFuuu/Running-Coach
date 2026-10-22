"""Garmin 資料同步的 HTTP 路由層（`/api/sync`）——N-19 §7。

這一層刻意很薄：來源限制、參數驗證，然後交給既有的
`services/garmin_sync_runner.sync()`。**同步邏輯一行都不在這裡**——
它與 CLI、MCP tool 共用同一個實作，三條路徑因此不可能對「同步」有不同定義。

## 為什麼 dashboard 出現了寫入端點

DASHBOARD_TASKS.md D.1 原本寫「頁面無寫入路徑……無『同步 Garmin』按鈕」。
2026-09-10 由 Fu 決定放行**這一個**動作，理由與界線：

  - 該約束真正要防的是「頁面上出現沒有信賴度的判讀與決策」（§7.3）。
    同步不產生任何判斷——它只把 Garmin 既有的資料搬進來，冪等、可重複執行。
  - **仍然不放行**決策型寫入：`generate_training_plan`（產課表）、
    `add_context_note`（寫記事）、`save_external_workouts`。那些才是
    「呈現層擅自做決定」，仍走 Claude Code／MCP。
  - 聊天面板**仍然只開放唯讀工具**（D.3 不變）。同步是使用者按的按鈕，
    不是模型能呼叫的工具——模型不該有能力自己去打 Garmin 的 API。

## 三條安全界線（皆為 CLAUDE.md 憑證規範的直接要求）

1. **來源限制**：與 `/api/chat/*` 共用 `client_access`，預設只接受本機。
   否則區網上任何人都能觸發你的 Garmin 請求，把帳號推向 rate limit。
   放行私有網段需明確設 `RC_SYNC_ALLOW_PRIVATE_NETWORK=1`。
2. **絕不在此用帳密登入**。token 失效時回明確的「請重跑一次性認證指令」
   （runner 已經是這個行為，本層只是把它如實透出），不自動重登——
   Garmin 對 `/sso/signin` 有 rate limit，自動重試會把帳號推向被鎖。
3. **回傳值不得含憑證**。runner 的結果只有筆數與日期；`reason` 來自
   例外訊息，而 `garmin_auth`／`garmin_api_client` 的例外訊息本來就
   只講「需要重新認證」不夾帶 token。見 `test_routes_sync.py` 的斷言。

## 為什麼保留 dry_run 這個參數

同步是**長時間操作**（wellness 逐日各打數個端點並自我節流，90 天約 12 分鐘）。
前端的流程是：先 `dry_run=True` 拿 `estimate.summary` 顯示給使用者確認，
按下去才真的執行。這是 2026-09-01 與 Fu 議定的行為，CLI 與 MCP 都照做，
dashboard 沒有理由自己發明一套。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from src.main.python.api import client_access
from src.main.python.api.routes_dashboard import get_conn
from src.main.python.services import dashboard_queries as queries
from src.main.python.services import garmin_sync_runner

router = APIRouter(prefix="/api/sync", tags=["sync"])

_FORBIDDEN_DETAIL = (
    "資料同步僅限本機使用。要讓區網裝置使用，"
    "請在啟動 server 的環境設定 RC_SYNC_ALLOW_PRIVATE_NETWORK=1。"
)


def allow_private_network() -> bool:
    """是否放行私有網段。做成 dependency 而非模組層常數，測試才能覆寫，
    也才不會在 import 當下就把環境變數的值凍住。"""
    return client_access.env_flag("RC_SYNC_ALLOW_PRIVATE_NETWORK")


def require_sync_client(
    request: Request, allowed: bool = Depends(allow_private_network)
) -> None:
    client_access.require_client(request, allowed, _FORBIDDEN_DETAIL)


def get_sync_runner():
    """實際執行同步的函式。測試以 dependency_overrides 換成假的，
    避免單元測試真的去打 Garmin。"""
    return garmin_sync_runner.sync


@router.get("/status")
def read_status(
    request: Request,
    allowed: bool = Depends(allow_private_network),
) -> dict[str, Any]:
    """按鈕啟用與否、以及擋住時要顯示什麼理由。

    ⚠️ 與 `/api/chat/status` 同樣的理由：這是**唯一**不套 403 的端點。
    來源不允許時仍回 200，否則前端連「為什麼不能同步」都說不出口。
    """
    client_allowed = client_access.is_client_allowed(request, allowed)
    return {
        "client_allowed": client_allowed,
        "reason": None if client_allowed else _FORBIDDEN_DETAIL,
    }


@router.post("", dependencies=[Depends(require_sync_client)])
def run_sync(
    payload: dict[str, Any] = Body(default_factory=dict),
    conn: sqlite3.Connection = Depends(get_conn),
    runner=Depends(get_sync_runner),
) -> dict[str, Any]:
    """執行同步。`dry_run=True` 只回計畫與耗時估算，不打 API、不寫入。

    `days` 省略時由 runner 自動回推到上次同步的斷點（建議用法）——
    固定天數會在長期未同步時漏掉中間的缺口。

    runner 對認證失敗／rate limit 一律回 `available=False` 而非拋例外，
    故這裡直接把結果透出，HTTP 狀態仍是 200：這些不是「請求錯了」，
    而是「同步這次做不成」，前端要據 `reason` 顯示可操作的指引。
    """
    days = payload.get("days")
    if days is not None:
        if not isinstance(days, int) or isinstance(days, bool):
            raise HTTPException(status_code=422, detail="days 必須是整數")
        if days < 1:
            raise HTTPException(status_code=422, detail=f"days 必須 >= 1（收到 {days}）")

    dry_run = payload.get("dry_run", False)
    if not isinstance(dry_run, bool):
        raise HTTPException(status_code=422, detail="dry_run 必須是布林值")

    athlete_id = payload.get("athlete_id")
    if athlete_id is not None and (
        not isinstance(athlete_id, int) or isinstance(athlete_id, bool)
    ):
        raise HTTPException(status_code=422, detail="athlete_id 必須是整數")

    resolved = queries.resolve_athlete_id(conn, athlete_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="資料庫中沒有任何學員資料")

    return runner(conn, resolved, days=days, dry_run=dry_run)
