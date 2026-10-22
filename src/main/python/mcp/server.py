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

寫入型 tool 有四個：generate_training_plan（產生課表）、
sync_garmin_data（從 Garmin Connect 增量同步資料）、
save_external_workouts（落地跑團／實體教練的課表，N-2）與
add_context_note（記錄學員的軟性輸入，§7.5）。其餘皆為唯讀查詢，
description 已明確標示副作用。

⚠️ 刻意**不**提供直接查 Garmin API 的查詢型 tool（如 get_hrv_status）：
資料一律先進資料庫，再由既有的唯讀 tools 查詢。理由見 PLAN.md §1
「Garmin 即時同步定位為匯入器」——直查會造成雙重真相、繞過事實層的
信賴度標記，且 Phase 2 規則引擎讀不到那些資料。

除 tools 外另提供一個 prompt：coach（教練的行為約束層／人設，
見本檔末 Prompt 區塊與 PLAN.md §7.6）。
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
from src.main.python.services import context_notes_store
from src.main.python.services import dashboard_queries
from src.main.python.services import garmin_sync_runner
from src.main.python.services import session_quality
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
    # MCP SDK 在跟 lifespan 不同的 thread 執行各個 tool call，需關閉
    # check_same_thread（lifespan 保證同一時間只有一個 tool 呼叫在跑，
    # 符合官方文件對關閉此檢查的前提）。
    conn = get_connection(_db_path, check_same_thread=False)
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


# --- 工具分類（唯讀／寫入）：單一真實來源 ---
#
# dashboard 的 AI 教練面板（DASHBOARD_TASKS.md D.3）只把唯讀工具餵給模型：
# 「呈現不等於決策、頁面無寫入路徑」的硬約束延伸到聊天——同步 Garmin、
# 產課表、記軟性輸入、落地外部課表一律仍走 Claude Code／Desktop 的 MCP 路徑。
#
# 分類寫在這裡而不是聊天層，是為了讓「新增一個 tool 卻忘記分類」變成可測的
# 錯誤：test_mcp_tool_classification.py 斷言 list_tools() 的名稱集合恰好等於
# 兩者的聯集、且兩者不相交。任何新 tool 沒被列進來，測試就會紅。
READ_ONLY_TOOL_NAMES = frozenset(
    {
        "get_athlete_meta",
        "list_running_sessions",
        "get_session_detail",
        "get_wellness_trend",
        "assess_session_quality",
        "get_recovery_impact",
        "get_readiness_status",
        "get_active_training_plan",
        "get_today",
        "get_context_notes",
    }
)

WRITE_TOOL_NAMES = frozenset(
    {
        "sync_garmin_data",
        "generate_training_plan",
        "add_context_note",
        "save_external_workouts",
    }
)


# --- Tools（讀取型） ---


@mcp.tool()
def get_athlete_meta(ctx: Context[AppContext], athlete_id: int | None = None) -> dict:
    """取得學員基本資料、各項身體指標的可用日期範圍、預設可選時間區間清單。

    athlete_id 省略時預設取資料庫中第一位學員（單人自用情境）。

    `athlete.max_hr` 採事實層三欄位契約 `{value, source, confidence}`。
    ⚠️ 若 `confidence.level` 為 `low`，代表 HRmax 非實測（手錶顯示值或年齡
    公式），**所有以 %HRR 推算的強度、訓練負荷、等效配速都帶著同一個系統性
    偏移**。據此下結論時必須一併說明此不確定性，不可當作精確值使用。
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

    ⚠️ **務必先看 `assessability.blocked`**：其中列出的判讀維度缺的是
    「前提」而非資料品質微瑕，**不得對這些維度下任何結論**。最常見的情形是
    `workout_type` 為 unknown/NULL（全體 266 場中佔 25.6%）——這不是
    「未知的課種」而是分類器失敗，該場可能是任何課種，拿 easy 的標準去評
    一場其實是間歇的課會產出完全錯誤的「執行度不佳」。此時應明說「這場無法
    評配速執行度，因為課種未知」，而非自行猜測課種後照評。
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

    ⚠️ 欄位語意警告：`acwr` 欄位存的是 Garmin `acwrFactorPercent`
    （0–100 量表，代表 ACWR 對 readiness 分數的貢獻度），**不是**古典的
    0.8–1.3 急慢性負荷比。**不可**用「>1.3 即過載」這類比值門檻解讀它。
    此為 Garmin 黑盒衍生值，僅供對照參考，不應作為判讀依據。
    """
    return _serialize(dashboard_queries.get_wellness_trend(_conn(ctx), athlete_id, range_key))


@mcp.tool()
def assess_session_quality(
    ctx: Context[AppContext],
    session_id: int,
    athlete_id: int | None = None,
) -> dict | None:
    """評估單場訓練的執行品質，回傳四個生理維度的判讀：強度符合度
    （實際心率強度是否落在該課種應有區間）、配速執行度（整場平均配速對該
    課種的 VDOT 配速區間）、配速穩定度（前後半配速差異）、心率漂移。
    查無此活動時回傳 None。

    ⚠️ **本工具刻意不輸出總分或等第**。各維度分開呈現，權衡與結論屬於你
    （判斷層）的職責——把它們合成單一分數等於把判斷偷渡進事實層。

    ⚠️ **每個維度都帶 `confidence` 標記，回答時必須一併考量**：
    - `level: low` 代表該數字有已知偏誤，`biases` 裡寫明錯在哪、可能錯多少。
      據此下結論時要說明不確定性，不可當精確值使用。
    - `hr_drift` 刻意沒有 `verdict`：`drift_pct` 是純前後半心率差（分母無配速），
      `decoupling_pct` 才是 Friel 的 Pa:HR。Friel 的 5% 是教練經驗法則，
      **不可當判定門檻**；`tier: reference` 的場次更要保守。只描述數值與趨勢，不要判定好壞。

    ⚠️ **`not_assessed` 列出的維度不得下結論**，其中的 reason 說明為何不可評
    （最常見是課種為 unknown——那是分類器失敗，該場可能是任何課種）。
    此時應明說「這項無法評估」，而非自行猜測後照評。

    ⚠️ **配速執行度只評 easy/lsd**。tempo/interval 的整場平均配速含暖身、
    緩和與間歇之間的休息段，會系統性地看起來「偏慢」——那是課的結構，
    不是沒執行；本專案尚未建模「主段」，故這兩種課直接列為不可評。
    另外此維度**只有偏快才算需要提示**：低強度日跑太快會讓這天既無恢復效果、
    也構不成刺激；跑得比區間慢不視為未執行，回報數值即可。

    本工具只分析實際練的內容，**不與計畫課表對照、不計算遵從度**——
    配速執行度比的是 VDOT 推算的該課種配速區間，不是 `training_plan` 的課表，
    問的是「這場 easy 跑得像不像 easy」，不是「有沒有照課表跑」。
    """
    conn = _conn(ctx)
    resolved = dashboard_queries.resolve_athlete_id(conn, athlete_id)
    if resolved is None:
        return None
    detail = dashboard_queries.get_session_detail(conn, session_id, resolved)
    if detail is None:
        return None

    activity = conn.execute(
        "SELECT workout_type, avg_hr_bpm, avg_pace_sec_per_km, date(started_at) AS started_on "
        "FROM activities WHERE id = ?",
        (session_id,),
    ).fetchone()
    records = [
        dict(row)
        for row in conn.execute(
            "SELECT elapsed_sec, pace_sec_per_km, hr_bpm FROM activity_records "
            "WHERE activity_id = ? ORDER BY elapsed_sec",
            (session_id,),
        )
    ]
    max_hr_bpm, resting_hr_bpm = training_load_queries.get_hr_params(conn, resolved)
    source_row = conn.execute(
        "SELECT max_hr_source FROM athlete_profile WHERE id = ?", (resolved,)
    ).fetchone()

    # 配速執行度的比較基準取「這場活動當天」的 VDOT，而非今天的——
    # 評估一場兩年前的訓練不該拿現在的體能當標準。
    pace_zones = training_plan_generator.get_pace_zones(
        conn,
        resolved,
        reference_date=datetime.date.fromisoformat(activity["started_on"]),
    )

    result = session_quality.assess_session(
        dict(activity),
        records,
        detail.get("hr_drift"),
        max_hr_bpm,
        resting_hr_bpm,
        source_row["max_hr_source"] if source_row else None,
        pace_zones=pace_zones,
    )
    result["session"] = {
        "id": detail["id"],
        "date": detail["date"],
        "title": detail["title"],
        "workout_type": detail["workout_type"],
        "distance_km": detail["summary"]["distance_km"],
    }
    return _serialize(result)


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
    """取得指定日期區間內每日的恢復狀態判讀（readiness: low/normal/None），
    綜合連續訓練天數、訓練負荷趨勢（TSB）、HRV 相對 7 日均值變化三個維度，
    附觸發原因。只輸出判讀結果，不會修改任何課表。

    readiness 為 None 代表三個維度皆無法評估（例如該日尚未同步任何
    activities／daily_wellness 紀錄），**不等於** "normal"——None 是
    「沒查」，"normal" 是「查過了、確實沒有異常訊號」。回傳的每筆另附
    dimensions_evaluated（實際評估了哪些維度）與 not_assessed（未評的
    維度與原因），行為約束第 2 條「不可評的維度就不評」同樣適用於此。

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


@mcp.tool()
def get_today(ctx: Context[AppContext], athlete_id: int | None = None) -> dict:
    """一次取得回答「今天該練什麼」所需的全部事實：今日／明日課表、
    今日準備度判讀（含最近一次真正可評的日子）、訓練負荷 ATL/CTL/TSB、
    今日 HRV 與相對 7 日均值的變化、連續訓練天數、最近一場活動，
    以及資料新鮮度（最後同步到哪一天、落後幾天）。

    這是既有唯讀 tools 的**聚合**，不新增任何規則判斷——同樣的數字也能
    用 get_readiness_status／get_active_training_plan／get_wellness_trend
    ／list_running_sessions 分別問到，本工具只是省下多次往返並確保它們
    錨定同一個「今天」。

    ⚠️ 每個數值都依 §7.3 三欄位契約包成 {value, source, confidence}，
    回答時必須一併讀 confidence——`level: low` 代表該數字有已知偏誤，
    `biases` 裡寫明錯在哪。**值為 None 代表「無法評估」而非 0 或正常**，
    此時應明說該項不可評與原因，不可自行填補。

    ⚠️ `readiness.today` 為 None 代表今天尚未同步到任何資料（不是「狀態
    正常」）；此時 `readiness.latest_evaluable` 提供最近一次真正評過的
    那天供參考，但那是**過去某一天**的判讀，不可當成今天的狀態直接引用，
    引用時須說明是哪一天。

    ⚠️ `freshness.days_behind` 大於 0 時，代表 Garmin 尚未同步到今天，
    以下所有判讀都建立在較舊的資料上，須主動告知使用者。

    athlete_id 省略時預設取資料庫中第一位學員。
    """
    return _serialize(dashboard_queries.get_today(_conn(ctx), athlete_id))


# --- Tools（寫入型，會修改資料庫） ---


@mcp.tool()
def sync_garmin_data(
    ctx: Context[AppContext],
    days: int | None = None,
    athlete_id: int | None = None,
    dry_run: bool = False,
) -> dict:
    """⚠️ 會寫入資料庫：從 Garmin Connect 抓取活動與每日身體數據，正規化後
    寫入資料庫。**冪等**——重複同步同一區間不會產生重複資料（由資料庫
    UNIQUE 約束保證），已存在的資料會就地更新。

    **days 省略時會自動回推到上次同步的斷點**（含 3 天重疊以補 Garmin
    延遲寫入的睡眠／HRV／Training Readiness），上限 90 天。這是預設且
    建議的用法——固定天數會在長期未同步時漏掉中間的缺口。

    ⚠️ **同步前應先告知使用者預計耗時**：wellness 是逐日各打 7 個 API
    端點並自我節流，90 天約需 10.5 分鐘。建議流程是先以 `dry_run=True`
    取得 `estimate.summary`（例：「45 天／316 次 API 請求／約 5 分 16 秒」），
    在對話中告知使用者後，再以相同參數實際執行。若使用者已明確要求
    立即同步，可直接執行並在回覆中轉述 `estimate`。

    回傳值一律含 `days`（實際採用天數）、`days_auto_resolved`（是否為
    自動回推）、`gap_truncated`、`estimate`。若 `gap_truncated=True`，
    代表距上次同步已超過 90 天、更早的缺口本次補不到，須告知使用者
    改下載官方匯出包並在終端機執行 garmin_import_runner。

    需先由使用者在終端機完成一次性認證：
        python -m src.main.python.services.garmin_auth login

    未認證或 token 過期時回傳 `available=False` 與說明，**不會**自動用帳密
    重登（Garmin 對登入端點有 rate limit，自動重試會把帳號推向被鎖）。
    此時應請使用者自行重跑上述指令，不要重複呼叫本工具。

    ⚠️ 若回傳含 `rate_limited=True`，代表已觸發 Garmin 的 IP 層限制：
    **不可重試**，請告知使用者稍後再試。

    本工具會為每場跑步另抓逐圈明細寫入 `activity_laps`，並套用既有分類器
    補上 `workout_type`（與歷史資料同一套標準）。抓不到分圈的場次
    `workout_type` 維持 NULL，在 `assess_session_quality` 中會被列為不可評
    ——這是刻意不猜（見行為約束第 2 條），屬正確行為。

    ⚠️ 逐秒資料仍需 FIT，本工具不提供，故同步進來的活動**心率漂移與
    配速穩定度兩個維度仍不可評**。

    athlete_id 省略時預設取資料庫中第一位學員。
    """
    resolved_athlete_id = dashboard_queries.resolve_athlete_id(_conn(ctx), athlete_id)
    if resolved_athlete_id is None:
        return {"available": False, "reason": "資料庫中沒有任何學員資料"}
    return _serialize(
        garmin_sync_runner.sync(
            _conn(ctx), resolved_athlete_id, days=days, dry_run=dry_run
        )
    )


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


@mcp.tool()
def get_context_notes(
    ctx: Context[AppContext],
    start_date: str,
    end_date: str,
    athlete_id: int | None = None,
) -> list[dict]:
    """取得指定日期區間內學員記錄過的**軟性輸入**（出差、睡眠不足、工作壓力、
    「腳有點緊」等規則算不出、只有學員本人知道的情境）。

    用途是解釋訓練偏差的**原因**——規則層看得到「這週漏了三課」，看不到
    「因為出差」。回答近況、解釋落差、或給建議前，值得先查這段期間有沒有
    相關脈絡。

    ⚠️ **這些不是事實層資料，永遠不進任何計算**。它們無法驗證（學員自述
    睡不好，系統無從查證），只能作為你判讀時的補充脈絡。不可拿它推算任何
    數值，也不可把它當成降低或提高某個判讀門檻的依據。

    ⚠️ 回述事實清單（行為約束第 4 條）時，軟性輸入應與事實層數據**分開列出**，
    標明它來自學員自述而非量測。

    與 [start_date, end_date] 有任何**重疊**的紀錄都會回傳（例如一筆
    09-01~09-10 的出差，查 09-05 時會出現）。日期格式為 YYYY-MM-DD。
    """
    resolved_athlete_id = dashboard_queries.resolve_athlete_id(_conn(ctx), athlete_id)
    if resolved_athlete_id is None:
        return []
    return _serialize(
        context_notes_store.get_notes_in_range(
            _conn(ctx), resolved_athlete_id, start_date, end_date
        )
    )


@mcp.tool()
def add_context_note(
    ctx: Context[AppContext],
    note: str,
    start_date: str,
    end_date: str | None = None,
    athlete_id: int | None = None,
) -> dict:
    """⚠️ 會寫入資料庫：記錄一筆學員的**軟性輸入**，供日後回看訓練偏差的原因。

    適用情境：學員在對話中提到規則算不出的狀況——「這週出差」「最近睡很差」
    「工作很忙」「左腳有點緊」「心情不好不想跑」。

    ⚠️ **請記錄學員的原話，不要代為摘要、分類或判斷嚴重程度。**
    這份文字的價值正在於它未經處理——三個月後回看時，「腳有點緊」與
    「腳痛」是不同的資訊，摘要會抹掉這個差別。本表刻意沒有類型與嚴重度
    欄位，就是為了避免過早結構化。

    start_date：影響起始日（YYYY-MM-DD）。
    end_date：影響結束日，省略代表單日、或影響仍在持續且結束日未知。

    ⚠️ 寫入前先向學員確認你要記什麼、涵蓋哪幾天。不要把學員的每一句閒聊
    都記下來——只記**會影響訓練解讀**的情境。

    ⚠️ 記錄下來的內容永遠不進事實層、不參與任何計算（見 get_context_notes）。
    """
    resolved_athlete_id = dashboard_queries.resolve_athlete_id(_conn(ctx), athlete_id)
    if resolved_athlete_id is None:
        return {"saved": False, "reason": "資料庫中沒有任何學員資料"}
    try:
        note_id = context_notes_store.add_note(
            _conn(ctx), resolved_athlete_id, note, start_date, end_date
        )
    except ValueError as exc:
        return {"saved": False, "reason": str(exc)}
    return {"saved": True, "note_id": note_id, "start_date": start_date, "end_date": end_date}


@mcp.tool()
def save_external_workouts(
    ctx: Context[AppContext],
    workouts: list[dict],
    clear_generated_between: bool = False,
    athlete_id: int | None = None,
) -> dict:
    """⚠️ 會寫入資料庫：把**外部來源**（跑團／實體教練）的課表逐日寫入
    training_plan，標記 plan_source='external'。

    用途是讓學員把教練給的課表告訴你，由你整理成逐日結構後寫入。適用情境
    例如學員說「教練說這週三 tempo 8K、週日長跑 18K」。

    workouts：每筆為 dict，必要欄位
        - "planned_date"：YYYY-MM-DD
        - "workout_type"：easy／tempo／interval／lsd／race／rest／strength／
          cross_training 其中之一
      選填 "planned_distance_km"／"planned_duration_sec"／
      "planned_pace_sec_per_km"／"notes"。

    ⚠️ **課種對不上時不要自行發明**：跑團的說法（如「配速跑」「山路」
    「400 間歇 ×8」）不保證落在上述 8 種內。請挑最接近的一種，**並把學員
    的原話完整放進 notes**，不要為了讓它「看起來整齊」而丟棄原始描述。
    不確定該歸哪一類時，直接問學員，不要猜。

    ⚠️ **這是寫入型操作，須經學員明確指示才呼叫**。同一天已有生效課表時，
    舊列會被標記為已取代（保留版本歷史、不刪除），**包含系統自產的課表**
    ——這是刻意的：外部課表介入後即為唯一基準，系統退居分析層。
    寫入前應先向學員複述你整理出的逐日內容並取得確認。

    clear_generated_between：跑團課表通常只排有練的日子，中間空檔不會出現在
    workouts 裡。設為 True 會把本批日期範圍內其餘系統自產的排程一併標記失效
    （保留歷史、不刪除），避免課表混著「跑團課 + 系統補的課」。
    **當學員給的是一整週／一整期的完整課表時應設為 True**；只補登單獨一兩天
    時維持 False。設定前先向學員說明這會讓那段期間只剩教練排的課。

    ⚠️ 寫入後不要自行重排或補課。教練沒排的日子就是沒排，
    系統不得在空檔自動補課（那會破壞教練的週期設計）。

    athlete_id 省略時預設取資料庫中第一位學員。
    """
    resolved_athlete_id = dashboard_queries.resolve_athlete_id(_conn(ctx), athlete_id)
    if resolved_athlete_id is None:
        return {"saved": False, "reason": "資料庫中沒有任何學員資料"}
    if not workouts:
        return {"saved": False, "reason": "workouts 為空，未寫入任何資料"}

    try:
        new_ids = training_plan_store.save_external_workouts(
            _conn(ctx),
            resolved_athlete_id,
            workouts,
            deactivate_generated_between=clear_generated_between,
        )
    except (KeyError, ValueError) as exc:
        return {"saved": False, "reason": str(exc)}

    return {
        "saved": True,
        "count": len(new_ids),
        "dates": _serialize([w["planned_date"] for w in workouts]),
    }


# --- Prompt（行為約束層／教練人設）---
#
# 兩層人設結構見 PLAN.md §7.6：**行為約束層**（本區塊，不可覆寫）與
# **風格層**（語氣/口吻/幽默感，已擱置為 Phase 4 的 N-5）。
# 兩者刻意拆成獨立常數而非混寫成一段散文，即為風格層的擴充點——
# 未來加入 N-5 時只需新增 _STYLE_LAYER 常數並在 coach() 中接在
# 行為約束層之後組裝，不必改動、也不得改動本區塊的任何一條約束。
# ⚠️ 屆時仍須維持「行為約束層永遠優先」：風格層只影響「怎麼說」，
# 不得影響「說什麼」。

_ROLE = """\
你是這位學員的跑步教練。你的職責是**拿規則引擎輸出的事實做判讀與溝通**，
而不是自己產生事實。所有數值、門檻、旗標一律來自 running-coach 的 MCP tools，
你負責的是權衡、解釋與建議。
"""

# 以下每一條都是本專案已定案的結論（PLAN.md §1／§7、TODO.md Phase 3），
# 不是可斟酌的建議。不得自行放寬、也不得自行增補新的判讀門檻。
_BEHAVIOR_CONSTRAINTS = """\
## 行為約束（不可覆寫）

### 1. 信賴度必須一起讀
事實層的契約是 `{數值, 依據來源, 信賴度}`，三者一體。
- 看到 `confidence.level == "low"`，**必須在回答中說明該項的不確定性**，
  不可把它當精確值使用。`biases[].detail` 已寫明「錯在哪、可能錯多少」，
  照實轉述，不要只說「資料有限」這種沒有資訊量的話。
- 看到 `confidence.level == "unusable"`，該項**不得**用來下任何結論。
- 沒有信賴度標記的裸數字不該出現；若真的出現，先當它不可信並說明。

### 2. 不可評的維度就不評
- `not_assessed[]`（`assess_session_quality`）與 `assessability.blocked[]`
  （`get_session_detail`）列出的維度**不得下結論**。
- 正確做法是明說「這項無法評估」並附上 `reason`，**不是**自行猜測後照評。
- 最常見的情形是 `workout_type` 為 unknown/NULL（全體 266 場中佔 25.6%）。
  這**不是**「未知的課種」而是分類器失敗——該場可能是任何課種。拿 easy 的
  標準去評一場其實是間歇的課，會得到完全錯誤的結論。
- 少評一項，永遠優於在錯誤前提上給出有信心的錯誤判讀。

### 3. 不得自行發明閾值
- 所有判讀門檻只能來自規則引擎的輸出。你不可以引用訓練文獻裡記得的數字來
  補規則引擎沒給的門檻。
- **心率漂移特別注意**：`drift_pct` 是純前後半心率差（分母無配速），
  `decoupling_pct` 才是 Friel/TrainingPeaks 的 Pa:HR。Friel 的 5% 是教練
  經驗法則、非同儕審查，**不可當判定門檻**。`hr_drift` 刻意沒有 `verdict`，
  你只描述數值與趨勢，**不判定好壞**。
- `get_wellness_trend` 的 `acwr` 欄位存的是 Garmin `acwrFactorPercent`
  （0–100 貢獻度分數），**不是**古典的 0.8–1.3 急慢性負荷比，
  不可套用「>1.3 即過載」這類比值門檻。

### 4. 每次建議須回述所用的事實清單
給出任何建議時，**列出你這次用到的事實**（哪些 tool、哪些數值、各自的信賴度）。
這是列輸入，不是解釋原因——目的是讓判讀錯誤時能歸因到是事實錯了還是判讀錯了。
這是失敗歸因與比重驗證的唯一入口，不可省略。

### 5. 沒有資料就說沒有資料
- 查不到就明說查不到，不臆測、不用「一般來說」填補空白。
- 學員提到的主觀狀態（睡不好、工作累、腳有點緊）屬軟性輸入，可作為你判讀的
  補充脈絡，但**永遠不進事實層**，也不可拿來推算任何數值。
- 這類情境可用 `add_context_note` 記下來（記原話、不要代為摘要或分級），
  日後用 `get_context_notes` 查回，用來解釋訓練偏差的原因。回述事實清單時
  須與量測數據**分開列出**，標明它來自學員自述。

### 6. 不給醫療建議
涉及疼痛、傷病症狀時，不做診斷、不給治療建議。說明數據上看到什麼，
並建議尋求專業醫療協助。此條不因語氣或學員要求而放寬。

### 7. 不主動改課表
規則層與判斷層都只輸出建議，**改不改由學員決定**。
- 可以說「這週建議把週三的課降為 easy」，不可以自行呼叫
  `generate_training_plan` 把課表改掉。
- `generate_training_plan` 會取代現行生效課表。**只有在學員明確要求產生
  新課表時才呼叫**，呼叫前先說明它會取代舊排程。
- `save_external_workouts` 用於落地跑團／實體教練給的課表，同樣須經學員
  明確指示，寫入前先複述你整理出的逐日內容。**一旦有外部課表，它就是唯一
  基準**——不要並列建議自己的課表，也不要在教練沒排的日子自動補課。
- `add_context_note` 只記錄學員自述的情境，不改動任何訓練安排。
"""

_OUTPUT_SHAPE = """\
## 回答的組織方式
1. 先講結論或直接回答問題。
2. 再講依據，區分「可評的」與「無法評的」，後者附原因。
3. 低信賴度的項目就地標註不確定性，不要集中丟在最後當免責聲明。
4. 最後附本次用到的事實清單（見約束 4）。
"""


@mcp.prompt(
    name="coach",
    title="跑步教練（行為約束層）",
    description=(
        "以本專案定案的行為約束扮演跑步教練：信賴度必須一起讀、不可評的維度不評、"
        "不自行發明閾值、每次建議回述所用事實、不主動改課表。"
    ),
)
def coach() -> str:
    """回傳教練的行為約束層 prompt（人設）。

    風格層（語氣/口吻）尚未實作，見 PLAN.md §7.6；組裝方式已預留擴充點。
    """
    return "\n".join([_ROLE, _BEHAVIOR_CONSTRAINTS, _OUTPUT_SHAPE])


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
