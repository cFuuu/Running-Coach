"""Dashboard 的 HTTP 路由層。

這一層刻意很薄：解析 query 參數、開/關資料庫連線、把結果交給
`dashboard_queries` 的純函式，然後回傳。**任何 SQL 或計算邏輯都不該出現在這裡**——
放在查詢層才能用 unittest 直接測，不必起 HTTP server。

回傳格式完全依照 `docs/dev/DASHBOARD_TASKS.md` 的 API Contract。
"""

from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path
from typing import Callable, Iterator

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from src.main.python.services import daily_feel_store
from src.main.python.services import dashboard_queries as queries
from src.main.python.services import garmin_cards
from src.main.python.services import race_names
from src.main.python.services import training_plan_store

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


# 學員檔路徑（賽事名稱的來源，見 race_names.py），同樣由 app.py 覆寫。
# 預設 None＝沒有學員檔，賽事名稱一律退回活動名稱。
def get_profile_path() -> Path | None:
    return None


RangeParam = Query(
    default=queries.DEFAULT_RANGE,
    description=(
        f"時間範圍，合法值：{', '.join(_RANGE_VALUES)}，"
        f"或自訂區間 {queries.CUSTOM_RANGE_PREFIX}YYYY-MM-DD:YYYY-MM-DD"
    ),
)
AthleteParam = Query(default=None, description="要查詢的 athlete_id，未指定時用資料庫第一位")


def _validate_range(range_key: str) -> str:
    """合法性判斷委派給查詢層的 is_valid_range()（唯一入口，見該函式說明），
    這裡只負責把不合法的結果轉成 HTTP 400。
    """
    if not queries.is_valid_range(range_key):
        raise HTTPException(
            status_code=400,
            detail=(
                f"不支援的 range：{range_key}（合法值：{', '.join(_RANGE_VALUES)}，"
                f"或自訂區間 {queries.CUSTOM_RANGE_PREFIX}YYYY-MM-DD:YYYY-MM-DD）"
            ),
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
    return queries.list_sessions(
        conn, athlete_id, _validate_range(range), include_lap_strip=True
    )


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


@router.get("/garmin-cards")
def read_garmin_cards(
    range: str = RangeParam,
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """Garmin 網頁式卡片牆（趨勢頁「Garmin 數據」與首頁體能狀況共用），僅供顯示。"""
    return garmin_cards.get_garmin_cards(conn, athlete_id, _validate_range(range))


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


def _parse_date_param(value: str, param_name: str) -> datetime.date:
    """把 YYYY-MM-DD 字串轉成 date，格式錯誤轉成 HTTP 400（而非 500）。"""
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"{param_name} 格式錯誤，應為 YYYY-MM-DD：{value}"
        ) from exc


@router.get("/today")
def read_today(
    date: str | None = Query(
        default=None, description="覆寫『今天』，格式 YYYY-MM-DD；省略時用系統今天"
    ),
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
    profile_path: Path | None = Depends(get_profile_path),
) -> dict:
    """今日卡：今日／明日課表、規則引擎的準備度判讀、訓練負荷、最近一場活動、
    下場／目標賽事剩餘天數（來自學員檔）。

    純呈現層薄封裝——實際邏輯全在 dashboard_queries.get_today()。
    """
    today = _parse_date_param(date, "date") if date else None
    return queries.get_today(
        conn, athlete_id, today, race_schedule=race_names.load_race_schedule(profile_path)
    )


@router.get("/calendar")
def read_calendar(
    start: str = Query(..., description="起日，格式 YYYY-MM-DD"),
    end: str = Query(..., description="訖日，格式 YYYY-MM-DD"),
    today: str | None = Query(
        default=None, description="覆寫『今天』，格式 YYYY-MM-DD；省略時用系統今天"
    ),
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
    profile_path: Path | None = Depends(get_profile_path),
) -> dict:
    """[start, end] 內每日課表與實際活動，供週條與行事曆共用。

    **刻意不走 resolve_range()／不接受 range 參數**——行事曆錨定真實今天，
    range 系列端點錨定資料庫最新日，兩者語意不同，見
    dashboard_queries.get_calendar() 的說明。
    """
    start_date = _parse_date_param(start, "start")
    end_date = _parse_date_param(end, "end")
    if start_date > end_date:
        raise HTTPException(status_code=400, detail=f"start 不可晚於 end：{start} > {end}")
    today_date = _parse_date_param(today, "today") if today else None
    return queries.get_calendar(
        conn,
        athlete_id,
        start,
        end,
        today_date,
        easy_pace_range=race_names.load_easy_pace_range(profile_path),
    )


@router.get("/training-load")
def read_training_load(
    range: str = RangeParam,
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """訓練負荷面板：range 內每日 ATL/CTL/TSB 時間序列，各自帶信賴度。"""
    return queries.get_training_load(conn, athlete_id, _validate_range(range))


@router.get("/overview")
def read_overview(
    range: str = RangeParam,
    today: str | None = Query(
        default=None, description="覆寫『今天』（本週跑量與 VDOT 的錨點），格式 YYYY-MM-DD"
    ),
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
    profile_path: Path | None = Depends(get_profile_path),
) -> dict:
    """總覽頁：有效訓練數、本週跑量、強度堆疊週跑量、心率區間分佈、VDOT。

    ATL/CTL/TSB 由前端另取 /api/training-load，這裡不重複輸出。
    """
    today_date = _parse_date_param(today, "today") if today else None
    # 每次請求重讀學員檔：Fu 改完賽事名稱重新整理就生效，不必重啟 server
    return queries.get_overview(
        conn,
        athlete_id,
        _validate_range(range),
        today_date,
        race_names=race_names.load_race_names(profile_path),
    )


# --------------------------------------------------------------------------
# /api/daily-feel：每日體感（首頁卡片的按鈕）
# --------------------------------------------------------------------------
# ⚠️ dashboard 區網放行的寫入端點（另一個是 /api/plan/{id}/link），
# 刻意**不加**來源限制、與唯讀端點同樣區網放行：
# 主要情境就是早上在手機上點一下。代價只是「區網內有人可以改你的體感紀錄」，
# 不花錢、不打外部 API（與 /api/chat、/api/sync 的「被觸發」代價不同）。


def _resolve_athlete_id(conn: sqlite3.Connection, athlete_id: int | None) -> int:
    resolved = queries.resolve_athlete_id(conn, athlete_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="資料庫中沒有任何學員資料")
    return resolved


@router.get("/daily-feel")
def read_daily_feel(
    start: str = Query(description="起始日 YYYY-MM-DD"),
    end: str = Query(description="結束日 YYYY-MM-DD"),
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    start_date = _parse_date_param(start, "start")
    end_date = _parse_date_param(end, "end")
    resolved = _resolve_athlete_id(conn, athlete_id)
    return {"feels": daily_feel_store.get_feels_in_range(conn, resolved, start_date, end_date)}


@router.put("/daily-feel/{date}")
def write_daily_feel(
    date: str,
    payload: dict = Body(default_factory=dict),
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """寫入（覆寫）某天體感。body：{"feel": "tired|normal|energetic", "note"?: str}"""
    day = _parse_date_param(date, "date")
    resolved = _resolve_athlete_id(conn, athlete_id)
    try:
        return daily_feel_store.set_feel(
            conn, resolved, day, payload.get("feel"), "dashboard", payload.get("note")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/daily-feel/{date}")
def remove_daily_feel(
    date: str,
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    day = _parse_date_param(date, "date")
    resolved = _resolve_athlete_id(conn, athlete_id)
    return {"deleted": daily_feel_store.delete_feel(conn, resolved, day)}


# --------------------------------------------------------------------------
# PUT /api/plan/{plan_id}/link：學員確認「這場是課表」（N-29 B2）
# --------------------------------------------------------------------------
# PLAN.md §1 2026-10-24：配對歧義時由學員確認，結果寫入 training_plan.linked_activity_id。
# 驗證與寫入都在 training_plan_store.set_plan_link()，這裡只做來源限制與錯誤轉換。
#
# 來源限制比照 /api/daily-feel：與唯讀端點同樣區網放行（2026-10-24 Fu 決定）。
# 主要情境是在手機上點「這場是課表」；代價只是區網內有人能改配對，
# 不花錢、不打外部 API，且隨時可再取消。


@router.put("/plan/{plan_id}/link")
def write_plan_link(
    plan_id: int,
    payload: dict = Body(default_factory=dict),
    athlete_id: int | None = AthleteParam,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """body：{"activity_id": int | null}，null＝取消連結。`activity_id` 必須明確給，
    漏給不當成取消（避免前端少帶欄位就把確認過的連結清掉）。

    回傳 `{plan_id, linked_activity_id, plan_match}`：`plan_match` 是該活動寫入後
    在行事曆會得到的 `{state, method, reason}`（連結成功時為 exact）；取消時為 null。
    """
    if "activity_id" not in payload:
        raise HTTPException(status_code=422, detail="缺少 activity_id（取消連結請傳 null）")
    activity_id = payload["activity_id"]
    if activity_id is not None and (
        not isinstance(activity_id, int) or isinstance(activity_id, bool)
    ):
        raise HTTPException(status_code=422, detail="activity_id 必須是整數或 null")

    resolved = _resolve_athlete_id(conn, athlete_id)
    try:
        result = training_plan_store.set_plan_link(conn, resolved, plan_id, activity_id)
    except training_plan_store.PlanLinkNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except training_plan_store.PlanLinkRejectedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    result["plan_match"] = (
        None
        if activity_id is None
        else queries.get_activity_plan_match(conn, resolved, activity_id)
    )
    return result
