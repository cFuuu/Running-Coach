"""Dashboard 的純查詢層：吃一個 sqlite3.Connection，回傳可直接序列化成 JSON 的 dict。

刻意**不 import fastapi**——這一層只負責「資料庫 → contract 定義的資料結構」，
路由/HTTP 的事情全在 api/routes_dashboard.py。這樣做的好處是：
1. 單元測試可以直接建臨時 SQLite 呼叫這些函式，不需要起 HTTP server
2. 之後若換成別的 web 框架、或改成 CLI 匯出報表，這一層原封不動可重用

回傳結構以 `docs/dev/DASHBOARD_TASKS.md` 的 API Contract 為準，欄位名稱與
`available`／`reason`／`clipped` 等旗標都必須與前端（Task B）的預期一致。

設計原則（呼應使用者要求）：
- **只呈現數據，不做解讀**：例如 hr_drift 只回傳前後半平均心率與百分比，不寫「這樣算好或不好」
- **缺值就是缺值**：沒有資料的日期直接不出現在 points 裡，絕不補 0（補 0 會在折線圖上畫出誤導的低點）
- **不 hardcode 個人識別資訊**：athlete_id 一律經由 resolve_athlete_id() 這個單一入口取得

SQL 只用標準語法（不用窗口函數等 SQLite 專有寫法），方便日後換成 PostgreSQL。
"""

from __future__ import annotations

import datetime
import json
import sqlite3
from typing import Any

from src.main.python.services import fit_parser
from src.main.python.services import garmin_export_parser as parser

# range 參數合法值 → 往回推的天數。"all" 代表不設起日。
RANGE_DAYS: dict[str, int | None] = {
    "7d": 7,
    "30d": 30,
    "90d": 90,
    "1y": 365,
    "all": None,
}
DEFAULT_RANGE = "30d"

# 自訂區間用這個前綴表示：custom:YYYY-MM-DD:YYYY-MM-DD。
# 選這個表示法（而非替每個端點加 start_date/end_date 參數）是因為
# resolve_range() 是所有 range-based 端點的單一入口，回傳值本來就是
# {range, start_date, end_date} 起訖日形狀——用前綴字串讓 6 個端點
# 零改動就能支援自訂區間，不必動路由層簽章與前端每個呼叫點。
CUSTOM_RANGE_PREFIX = "custom:"

# 視為「跑步類」的 activity_type。跟 fit_import_runner 的比較集合一樣，
# 這是可調整的參數而非寫死的假設——之後要把其他運動也納入單場分析時擴充這裡即可。
RUNNING_ACTIVITY_TYPES: tuple[str, ...] = ("running", "treadmill_running", "track_running", "trail_running")

# `/api/wellness-trend` 要回傳的指標定義，唯一真實來源——同時用來組 SQL
# 欄位清單（見 get_wellness_trend 的 columns）與回給前端的顯示中繼資料
# （label/unit/decimals/format）。前端不再自己維護一份指標定義，改由
# /api/meta 的 wellness_metric_defs 透出這份清單驅動渲染與排序/顯隱設定。
#
# 刻意**不含 skin_temp_c／body_battery_max／body_battery_min**：實測 Garmin
# 匯出包裡這三欄 100% 為 NULL，畫出來只會是一條空線。
# stress_avg（睡眠期間平均）與 all_day_stress_avg（全天平均）是兩個不同範疇的指標，
# 分成兩筆而非合併成一條線。
#
# format 為 'duration' 的指標用秒數格式化成 h:mm:ss（見前端 charts.formatDuration），
# 其餘用 decimals 位數的一般數字格式化。
WellnessMetricDef = tuple[str, str, str, int, str]  # (column, label, unit, decimals, format)

WELLNESS_METRIC_DEFS: tuple[WellnessMetricDef, ...] = (
    ("hrv_ms", "HRV", "ms", 1, "number"),
    ("resting_hr_bpm", "靜止心率", "bpm", 0, "number"),
    ("spo2_pct", "血氧", "%", 0, "number"),
    ("sleep_score", "睡眠分數", "", 0, "number"),
    ("training_readiness_score", "訓練準備度", "", 0, "number"),
    ("stress_avg", "壓力（睡眠期間）", "", 0, "number"),
    ("all_day_stress_avg", "壓力（全天）", "", 0, "number"),
    ("steps", "步數", "步", 0, "number"),
    # 以下 5 個是 Phase 5 新開放的指標（原本 daily_wellness 有資料但前端
    # 未顯示）。依實測涵蓋率決定：前 4 個涵蓋率 64.8%~89.1%，預設顯示；
    # respiration_rate 僅 17.6%（與既有的 hrv_ms／spo2_pct 同量級），
    # 可選但預設隱藏，避免初次開啟就充滿大片無資料的圖表。
    ("sleep_duration_sec", "睡眠時長", "", 0, "duration"),
    ("hrv_weekly_avg_ms", "HRV 週均", "ms", 1, "number"),
    ("recovery_time_hours", "恢復時間", "hr", 1, "number"),
    ("acwr", "急慢性負荷比（ACWR）", "", 2, "number"),
    ("respiration_rate", "呼吸率", "/min", 1, "number"),
)

WELLNESS_METRICS: tuple[str, ...] = tuple(d[0] for d in WELLNESS_METRIC_DEFS)

# 預設顯示的指標（respiration_rate 因涵蓋率低，可選但預設不顯示）。
# 前端 localStorage 沒有使用者自訂設定時，用這份清單決定初始顯示哪些。
WELLNESS_METRICS_DEFAULT_HIDDEN: tuple[str, ...] = ("respiration_rate",)

# `/api/recovery-impact` 計算 delta 的欄位：(daily_wellness 欄位名, 回傳 key 前綴, 是否附百分比)。
# 只有 HRV 附百分比：HRV 的絕對差值（例如 -2 ms）脫離基線就看不出幅度，
# 而 RHR 與 Training Readiness 本身就是好讀的絕對數字，加百分比反而多餘。
RECOVERY_METRICS: tuple[tuple[str, str, bool], ...] = (
    ("hrv_ms", "hrv", True),
    ("resting_hr_bpm", "resting_hr", False),
    ("training_readiness_score", "training_readiness", False),
)

# Garmin 匯出 JSON 手動分圈（activities.raw_data_json 的 splits）的 type 值。
# 實測只有 17 是使用者按錶分出的「圈」；3 是整場總計（會與整場數字重複），
# 18／22 則是暖身/其他雜訊段落，畫成分圈圖會誤導，因此一律濾掉。
MANUAL_LAP_SPLIT_TYPE = 17

# 服務性質提醒，隨 /api/meta 一起回給前端顯示（見 api/app.py 的安全性說明）
SERVICE_NOTICE = "此服務僅供區網存取且無身分驗證，切勿對外網開放。"

# 預設 range 選項的顯示標籤，隨 /api/meta 一起回給前端。後端是這份清單的
# 唯一真實來源——前端依此動態產生按鈕，不再各自硬編碼一份 range 清單
# （原本 index.html 的 5 個按鈕、app.js 的 VALID_RANGES、這裡的 RANGE_DAYS
# 三處各自維護，容易漂移）。
RANGE_LABELS: dict[str, str] = {
    "7d": "7 天",
    "30d": "30 天",
    "90d": "90 天",
    "1y": "1 年",
    "all": "全部",
}


# --------------------------------------------------------------------------
# 共用工具
# --------------------------------------------------------------------------


def resolve_athlete_id(conn: sqlite3.Connection, athlete_id: int | None = None) -> int | None:
    """取得要查詢的 athlete_id——這是**唯一**的入口。

    傳入 None 時取資料庫中的第一位（單人自用情境）。之後上雲改成從登入 session
    取得時，只需要改這一個函式，不必去每支查詢裡找散落的預設值。
    資料庫完全沒有 athlete 時回傳 None，呼叫端須自行處理（不可假設一定有人）。
    """
    if athlete_id is not None:
        return athlete_id
    row = conn.execute("SELECT id FROM athlete_profile ORDER BY id LIMIT 1").fetchone()
    return row["id"] if row else None


def is_valid_range(range_key: str) -> bool:
    """range 參數的合法性判斷單一入口。路由層（HTTP 400）與查詢層
    （ValueError）都呼叫這個函式，避免兩邊各自維護一份判斷邏輯而漂移。
    """
    if range_key in RANGE_DAYS:
        return True
    if range_key.startswith(CUSTOM_RANGE_PREFIX):
        try:
            parse_range(range_key)
            return True
        except ValueError:
            return False
    return False


def parse_range(range_key: str) -> tuple[datetime.date, datetime.date] | None:
    """解析 custom:YYYY-MM-DD:YYYY-MM-DD，回傳 (start, end)。

    非 custom 前綴回傳 None（呼叫端走既有的 RANGE_DAYS 邏輯）。
    格式錯誤、日期不合法、或 start > end 一律 raise ValueError，
    訊息面向使用者（會被路由層轉成 HTTP 400 的 detail）。
    """
    if not range_key.startswith(CUSTOM_RANGE_PREFIX):
        return None
    body = range_key[len(CUSTOM_RANGE_PREFIX):]
    parts = body.split(":")
    if len(parts) != 2:
        raise ValueError(f"自訂區間格式錯誤，應為 custom:YYYY-MM-DD:YYYY-MM-DD：{range_key}")
    try:
        start = datetime.date.fromisoformat(parts[0])
        end = datetime.date.fromisoformat(parts[1])
    except ValueError as exc:
        raise ValueError(f"自訂區間日期格式錯誤（需 YYYY-MM-DD）：{range_key}") from exc
    if start > end:
        raise ValueError(f"自訂區間的起日不可晚於訖日：{range_key}")
    return start, end


def resolve_range(
    conn: sqlite3.Connection,
    athlete_id: int | None,
    range_key: str = DEFAULT_RANGE,
    today: datetime.date | None = None,
) -> dict:
    """把 range 參數換算成起訖日字串，回傳 {"range", "start_date", "end_date"}。

    兩種路徑：
    - 預設列舉（7d/30d/90d/1y/all）：end_date 用「資料庫裡該 athlete 最新的
      一天」而非系統今天——匯出包的資料可能落後現實好幾天，用系統今天會讓
      7d 視窗大半是空的。查不到任何資料時才退回系統今天。
    - 自訂區間（custom:...）：直接使用使用者指定的起訖日，**不套用「錨定
      最新資料日」規則**——使用者已經明確指定日期，錨定反而會違背其意圖
      （例如使用者想看去年某段訓練，錨定會讓 end_date 跑到今天附近）。

    start_date 為 None 代表 range="all"（不設下界）。
    """
    custom = parse_range(range_key)
    if custom is not None:
        start, end = custom
        return {"range": range_key, "start_date": start.isoformat(), "end_date": end.isoformat()}

    if range_key not in RANGE_DAYS:
        raise ValueError(f"不支援的 range：{range_key}（合法值：{', '.join(RANGE_DAYS)} 或 {CUSTOM_RANGE_PREFIX}YYYY-MM-DD:YYYY-MM-DD）")

    end_date = _latest_data_date(conn, athlete_id) or (today or datetime.date.today())
    days = RANGE_DAYS[range_key]
    # 含當日在內往回推，因此 7d = 今天與前 6 天，共 7 天
    start_date = end_date - datetime.timedelta(days=days - 1) if days else None

    return {
        "range": range_key,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat(),
    }


def _latest_data_date(conn: sqlite3.Connection, athlete_id: int | None) -> datetime.date | None:
    """該 athlete 在 activities 或 daily_wellness 裡最新的一天。"""
    if athlete_id is None:
        return None
    row = conn.execute(
        """
        SELECT MAX(d) AS latest FROM (
            SELECT MAX(date(started_at)) AS d FROM activities WHERE athlete_id = ?
            UNION ALL
            SELECT MAX(date) AS d FROM daily_wellness WHERE athlete_id = ?
        )
        """,
        (athlete_id, athlete_id),
    ).fetchone()
    if not row or not row["latest"]:
        return None
    return datetime.date.fromisoformat(row["latest"])


def _round(value: Any, digits: int = 1) -> Any:
    """只對數字四捨五入，None 原樣保留（絕不轉成 0）。"""
    return None if value is None else round(value, digits)


# --------------------------------------------------------------------------
# /api/meta
# --------------------------------------------------------------------------


def get_meta(conn: sqlite3.Connection, athlete_id: int | None = None) -> dict:
    """回傳 athlete 基本資料、各指標的可用日期範圍、預設 range 選項清單、
    以及整體資料起訖日（供前端自訂日期選擇器的 min/max 使用）。
    """
    resolved = resolve_athlete_id(conn, athlete_id)
    athlete = None
    if resolved is not None:
        row = conn.execute(
            "SELECT id, name FROM athlete_profile WHERE id = ?", (resolved,)
        ).fetchone()
        if row:
            athlete = {"id": row["id"], "name": row["name"]}

    coverage: dict[str, dict] = {}
    if resolved is not None:
        # 同一指標可能有多個來源（garmin_export / fit_manual…），取聯集後的最早與最晚，
        # 前端要的是「這個指標到底有沒有資料」，不需要分來源。
        for row in conn.execute(
            """
            SELECT metric_name,
                   MIN(earliest_date) AS earliest_date,
                   MAX(latest_date) AS latest_date
            FROM metric_coverage
            WHERE athlete_id = ?
            GROUP BY metric_name
            ORDER BY metric_name
            """,
            (resolved,),
        ):
            coverage[row["metric_name"]] = {
                "earliest_date": row["earliest_date"],
                "latest_date": row["latest_date"],
            }

    data_bounds = None
    if coverage:
        earliest = min(c["earliest_date"] for c in coverage.values() if c["earliest_date"])
        latest = max(c["latest_date"] for c in coverage.values() if c["latest_date"])
        data_bounds = {"earliest_date": earliest, "latest_date": latest}

    ranges = [{"key": key, "label": RANGE_LABELS[key]} for key in RANGE_DAYS]

    wellness_metric_defs = [
        {
            "key": column,
            "label": label,
            "unit": unit,
            "decimals": decimals,
            "format": fmt,
            "default_hidden": column in WELLNESS_METRICS_DEFAULT_HIDDEN,
        }
        for column, label, unit, decimals, fmt in WELLNESS_METRIC_DEFS
    ]

    return {
        "athlete": athlete,
        "metric_coverage": coverage,
        "ranges": ranges,
        "data_bounds": data_bounds,
        "wellness_metric_defs": wellness_metric_defs,
        "notice": SERVICE_NOTICE,
    }


# --------------------------------------------------------------------------
# /api/sessions
# --------------------------------------------------------------------------


def list_sessions(
    conn: sqlite3.Connection,
    athlete_id: int | None = None,
    range_key: str = DEFAULT_RANGE,
) -> dict:
    """列出 range 內的跑步類活動，依開始時間由新到舊。"""
    resolved = resolve_athlete_id(conn, athlete_id)
    window = resolve_range(conn, resolved, range_key)
    if resolved is None:
        return {**window, "sessions": []}

    type_placeholders = ",".join("?" for _ in RUNNING_ACTIVITY_TYPES)
    sql = f"""
        SELECT id, started_at, title, activity_type, workout_type, workout_type_source,
               distance_km, duration_sec, avg_pace_sec_per_km, avg_hr_bpm, max_hr_bpm,
               avg_cadence_spm, aerobic_te
        FROM activities
        WHERE athlete_id = ?
          AND activity_type IN ({type_placeholders})
          AND date(started_at) <= ?
    """
    params: list[Any] = [resolved, *RUNNING_ACTIVITY_TYPES, window["end_date"]]
    if window["start_date"]:
        sql += " AND date(started_at) >= ?"
        params.append(window["start_date"])
    sql += " ORDER BY started_at DESC"

    sessions = []
    for row in conn.execute(sql, params):
        sessions.append(
            {
                "id": row["id"],
                "started_at": row["started_at"],
                "date": row["started_at"][:10] if row["started_at"] else None,
                "title": row["title"],
                "activity_type": row["activity_type"],
                "workout_type": row["workout_type"],
                "workout_type_source": row["workout_type_source"],
                "distance_km": row["distance_km"],
                "duration_sec": row["duration_sec"],
                "avg_pace_sec_per_km": row["avg_pace_sec_per_km"],
                "avg_hr_bpm": row["avg_hr_bpm"],
                "max_hr_bpm": row["max_hr_bpm"],
                "avg_cadence_spm": row["avg_cadence_spm"],
                "aerobic_te": row["aerobic_te"],
            }
        )
    return {**window, "sessions": sessions}


# --------------------------------------------------------------------------
# /api/sessions/{id}
# --------------------------------------------------------------------------


def get_session_detail(
    conn: sqlite3.Connection, session_id: int, athlete_id: int | None = None
) -> dict | None:
    """單場詳細資料：摘要、心率區間、分圈、逐秒、心率漂移。查無此場回傳 None。

    每個子區塊都一定會出現在回傳裡，沒有資料時是 {"available": false, "reason": ...}
    而不是省略——前端才能顯示「為什麼沒有圖」而不是畫一張空圖。
    """
    resolved = resolve_athlete_id(conn, athlete_id)
    sql = """
        SELECT id, athlete_id, started_at, title, activity_type, workout_type, workout_type_source,
               distance_km, duration_sec, avg_pace_sec_per_km, avg_hr_bpm, max_hr_bpm,
               avg_cadence_spm, aerobic_te, elevation_gain_m, calories, raw_data_json
        FROM activities
        WHERE id = ?
    """
    params: list[Any] = [session_id]
    if resolved is not None:
        sql += " AND athlete_id = ?"
        params.append(resolved)
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None

    records = _fetch_records(conn, session_id)

    return {
        "id": row["id"],
        "started_at": row["started_at"],
        "date": row["started_at"][:10] if row["started_at"] else None,
        "title": row["title"],
        "activity_type": row["activity_type"],
        "workout_type": row["workout_type"],
        "workout_type_source": row["workout_type_source"],
        "planned": _fetch_planned(conn, row["athlete_id"], row["started_at"], session_id),
        "summary": {
            "distance_km": row["distance_km"],
            "duration_sec": row["duration_sec"],
            "avg_pace_sec_per_km": row["avg_pace_sec_per_km"],
            "avg_hr_bpm": row["avg_hr_bpm"],
            "max_hr_bpm": row["max_hr_bpm"],
            "avg_cadence_spm": row["avg_cadence_spm"],
            "aerobic_te": row["aerobic_te"],
            "elevation_gain_m": row["elevation_gain_m"],
            "calories": row["calories"],
        },
        "hr_zones": _build_hr_zones(row["raw_data_json"]),
        "laps": _build_laps(conn, session_id, row["raw_data_json"]),
        "records": _build_records_block(records),
        "hr_drift": fit_parser.compute_hr_drift(records)
        if records
        else {"available": False, "reason": "需要逐秒資料才能計算心率漂移"},
    }


def _fetch_planned(
    conn: sqlite3.Connection, athlete_id: int, started_at: str | None, session_id: int
) -> dict | None:
    """找出這場活動對應的計畫課表：先看直接連結，再退回同一天的計畫。"""
    row = conn.execute(
        "SELECT workout_type, planned_distance_km, planned_pace_sec_per_km, notes "
        "FROM training_plan WHERE linked_activity_id = ? LIMIT 1",
        (session_id,),
    ).fetchone()
    if row is None and started_at:
        row = conn.execute(
            "SELECT workout_type, planned_distance_km, planned_pace_sec_per_km, notes "
            "FROM training_plan WHERE athlete_id = ? AND planned_date = ? "
            "ORDER BY id LIMIT 1",
            (athlete_id, started_at[:10]),
        ).fetchone()
    if row is None:
        return None
    return {
        "workout_type": row["workout_type"],
        "planned_distance_km": row["planned_distance_km"],
        "planned_pace_sec_per_km": row["planned_pace_sec_per_km"],
        "notes": row["notes"],
    }


def _fetch_records(conn: sqlite3.Connection, session_id: int) -> list[dict]:
    return [
        {
            "elapsed_sec": r["elapsed_sec"],
            "distance_km": r["distance_km"],
            "hr_bpm": r["hr_bpm"],
            "pace_sec_per_km": r["pace_sec_per_km"],
            "cadence_spm": r["cadence_spm"],
            "altitude_m": r["altitude_m"],
        }
        for r in conn.execute(
            "SELECT elapsed_sec, distance_km, hr_bpm, pace_sec_per_km, cadence_spm, altitude_m "
            "FROM activity_records WHERE activity_id = ? ORDER BY elapsed_sec",
            (session_id,),
        )
    ]


def _build_records_block(records: list[dict]) -> dict:
    if not records:
        return {"available": False, "reason": "此活動尚未解析 FIT 逐秒資料"}
    # 匯入時已降頻，實際間隔從資料本身推導，不寫死成常數
    sample_every = None
    if len(records) >= 2:
        sample_every = records[1]["elapsed_sec"] - records[0]["elapsed_sec"]
    return {"available": True, "sample_every_sec": sample_every, "points": records}


def _build_laps(conn: sqlite3.Connection, session_id: int, raw_data_json: str | None) -> dict:
    """分圈資料，來源有優先序：FIT 每公里分圈 > Garmin 手動按錶分段。

    FIT 的 lap 是整齊的每公里分圈（91% 跑步活動有），品質遠優於手動分段，
    所以只有在完全沒有 FIT 分圈時才退回 raw_data_json 的 splits。
    """
    fit_laps = [
        {
            "lap": r["lap_index"],
            "distance_km": r["distance_km"],
            "duration_sec": r["duration_sec"],
            "pace_sec_per_km": r["pace_sec_per_km"],
            "avg_hr_bpm": r["avg_hr_bpm"],
            "max_hr_bpm": r["max_hr_bpm"],
        }
        for r in conn.execute(
            "SELECT lap_index, distance_km, duration_sec, pace_sec_per_km, avg_hr_bpm, max_hr_bpm "
            "FROM activity_laps WHERE activity_id = ? ORDER BY lap_index",
            (session_id,),
        )
    ]
    if fit_laps:
        return {"available": True, "source": "fit", "laps": fit_laps}

    manual_laps = _parse_manual_laps(raw_data_json)
    # 只有一圈等於「整場一段」，畫成分圈圖沒有任何資訊量，視同沒有分圈資料。
    if len(manual_laps) > 1:
        return {"available": True, "source": "garmin_manual_lap", "laps": manual_laps}

    return {"available": False, "reason": "此活動無分圈資料"}


def _measurement(split: dict, field_enum: str) -> float | None:
    """從一筆 split 的 measurements 陣列裡，依 fieldEnum 取出對應數值。

    實測 Garmin 匯出格式：split 本身不是扁平欄位，數值都包在
    `measurements: [{"fieldEnum": "...", "value": ..., "unitEnum": "..."}, ...]` 裡，
    需逐筆比對 fieldEnum 才能取到，不是 split["distance"] 這種直接欄位。
    """
    for m in split.get("measurements") or []:
        if isinstance(m, dict) and m.get("fieldEnum") == field_enum:
            return m.get("value")
    return None


def _parse_manual_laps(raw_data_json: str | None) -> list[dict]:
    """從 activities.raw_data_json 的 splits 取出使用者手動按錶的分段。

    單位換算一律用 garmin_export_parser 已經過真實資料驗證的公開函式
    （公分／毫秒 → 公里／秒），不在這裡另寫一份。
    """
    if not raw_data_json:
        return []
    try:
        data = json.loads(raw_data_json)
    except (ValueError, TypeError):
        return []
    splits = data.get("splits") if isinstance(data, dict) else None
    if not isinstance(splits, list):
        return []

    laps = []
    for split in splits:
        if not isinstance(split, dict) or split.get("type") != MANUAL_LAP_SPLIT_TYPE:
            continue
        distance_km = parser.cm_to_km(_measurement(split, "SUM_DISTANCE"))
        duration_sec = parser.ms_to_sec(_measurement(split, "SUM_DURATION"))
        pace = (
            round(duration_sec / distance_km)
            if duration_sec and distance_km and distance_km > 0
            else None
        )
        avg_hr = _measurement(split, "WEIGHTED_MEAN_HEARTRATE")
        max_hr = _measurement(split, "MAX_HEARTRATE")
        laps.append(
            {
                "lap": len(laps) + 1,
                "distance_km": _round(distance_km, 4),
                "duration_sec": duration_sec,
                "pace_sec_per_km": pace,
                "avg_hr_bpm": round(avg_hr) if avg_hr is not None else None,
                "max_hr_bpm": round(max_hr) if max_hr is not None else None,
            }
        )
    return laps


# hrTimeInZone_0 是 Garmin 對「低於 Zone 1」時間的固定編號（暖身/靜止），
# 不是使用者的訓練區間之一，故獨立回傳為 below_zone_1 而非併入 zones。
HR_ZONE_BELOW_FIRST = 0

# 使用者要求跨場次比較時圖表軸線一致：不管某場實際進過幾個區間，
# zones 永遠回傳 Z1~Z5 這 5 列（沒進過的區間 seconds=0），而非依實際
# 資料的最高 zone 動態增減列數。實測 266 場的 hrTimeInZone_6 恆為 0
# （Garmin 錶只設定 5 個訓練區間時，「超過最高區間」的桶位固定產生但
# 收不到資料），故上限訂為 5、不含 zone 6 這個裝置 padding。
HR_ZONE_MAX = 5


def _build_hr_zones(raw_data_json: str | None) -> dict:
    """從 raw_data_json 取心率區間停留秒數（Garmin 匯出的 hrTimeInZone_N 欄位）。

    實測欄位值單位是**毫秒**（例如某場 `hrTimeInZone_2 = 3629313`，換算 60.5 分鐘
    與該場實際時長吻合），不是欄位名字面看起來的秒，須用 ms_to_sec() 換算，
    不可直接當秒數使用。

    全部區間都是 0 或不存在時視為沒有資料——這種活動多半根本沒戴心率帶，
    畫一張全 0 的長條圖只會誤導。

    實測發現 `hrTimeInZone_0` ~ `_N` 加總恰好等於該場 duration_sec（抽驗 12 場，
    9 場完全吻合、3 場差 29~34 秒屬正常誤差），代表 zone 0（低於 Zone 1 的
    暖身/靜止時間）是真實資料、且應計入百分比分母，否則各區間佔比會虛增。
    但它不是使用者的訓練強度區間，混進 Z1~Z5 的長條圖裡會誤導，故獨立
    回傳為 `below_zone_1`。

    `zones` 固定回傳 Z1~Z5 共 5 筆（見 HR_ZONE_MAX 註解），沒進過的區間
    `seconds=0` 照樣列出，方便前端跨場次比較時圖表軸線一致，不因某場沒
    進某個區間就少一列。`below_zone_1` 同理固定回傳，沒有暖身時間就是
    `seconds=0`。
    """
    unavailable = {"available": False, "reason": "此活動無心率區間資料"}
    if not raw_data_json:
        return unavailable
    try:
        data = json.loads(raw_data_json)
    except (ValueError, TypeError):
        return unavailable
    if not isinstance(data, dict):
        return unavailable

    seconds_by_zone: dict[int, int] = {}
    for key, value in data.items():
        if not key.startswith("hrTimeInZone_") or value is None:
            continue
        index = key[len("hrTimeInZone_") :]
        if not index.isdigit():
            continue
        seconds = parser.ms_to_sec(value)
        if seconds is None:
            continue
        seconds_by_zone[int(index)] = seconds

    if not seconds_by_zone or all(v == 0 for v in seconds_by_zone.values()):
        return unavailable

    total_seconds = sum(
        v for k, v in seconds_by_zone.items() if k <= HR_ZONE_MAX
    )
    if total_seconds <= 0:
        return unavailable

    def with_pct(zone: int) -> dict:
        seconds = seconds_by_zone.get(zone, 0)
        return {
            "zone": zone,
            "seconds": seconds,
            "pct": round(seconds / total_seconds * 100, 1),
        }

    return {
        "available": True,
        "zones": [with_pct(z) for z in range(1, HR_ZONE_MAX + 1)],
        "below_zone_1": with_pct(HR_ZONE_BELOW_FIRST),
        "total_seconds": total_seconds,
    }


# --------------------------------------------------------------------------
# /api/wellness-trend
# --------------------------------------------------------------------------


def get_wellness_trend(
    conn: sqlite3.Connection,
    athlete_id: int | None = None,
    range_key: str = DEFAULT_RANGE,
) -> dict:
    """每日身體狀況趨勢。每個指標各自帶 coverage 與 clipped 旗標。

    clipped=true 代表「使用者選的區間比這個指標實際有資料的起日還早」，
    前端必須顯示「此日期前無資料」，而不是把缺的那段畫成 0 或一條平線。
    """
    resolved = resolve_athlete_id(conn, athlete_id)
    window = resolve_range(conn, resolved, range_key)

    coverage_by_metric: dict[str, dict] = {}
    rows: list[sqlite3.Row] = []
    if resolved is not None:
        coverage_by_metric = get_meta(conn, resolved)["metric_coverage"]

        columns = ", ".join(WELLNESS_METRICS)
        sql = f"SELECT date, {columns} FROM daily_wellness WHERE athlete_id = ? AND date <= ?"
        params: list[Any] = [resolved, window["end_date"]]
        if window["start_date"]:
            sql += " AND date >= ?"
            params.append(window["start_date"])
        sql += " ORDER BY date"
        rows = list(conn.execute(sql, params))

    metrics: dict[str, dict] = {}
    for metric in WELLNESS_METRICS:
        # 缺值的日期直接不進 points——前端會斷線，不會補 0
        points = [
            {"date": row["date"], "value": row[metric]}
            for row in rows
            if row[metric] is not None
        ]
        coverage = coverage_by_metric.get(metric)
        entry: dict[str, Any] = {
            "available": bool(points),
            "clipped": _is_clipped(window["start_date"], coverage),
            "coverage": coverage,
            "points": points,
        }
        if not points:
            entry["reason"] = "所選區間內無此指標資料"
        metrics[metric] = entry

    return {**window, "metrics": metrics}


def _is_clipped(start_date: str | None, coverage: dict | None) -> bool:
    """所選區間是否早於該指標實際有資料的起日。

    range="all"（start_date 為 None）等於「從最早查到最新」，只要該指標的起日
    晚於其他資料的起點，圖左側就會有一段空白，因此有 coverage 時一律視為 clipped。
    """
    if not coverage or not coverage.get("earliest_date"):
        return False
    if start_date is None:
        return True
    return start_date < coverage["earliest_date"]


# --------------------------------------------------------------------------
# /api/training-days
# --------------------------------------------------------------------------


def get_training_days(
    conn: sqlite3.Connection,
    athlete_id: int | None = None,
    range_key: str = DEFAULT_RANGE,
) -> dict:
    """range 內有訓練的日期清單，供前端把訓練日標記疊到 wellness 圖上。

    這裡不限定跑步——重量訓練、騎車一樣會影響隔天的身體狀況，
    做「訓練×身體關聯」時全部都算訓練日。
    """
    resolved = resolve_athlete_id(conn, athlete_id)
    window = resolve_range(conn, resolved, range_key)
    if resolved is None:
        return {**window, "training_days": []}

    sql = """
        SELECT DISTINCT date(started_at) AS d
        FROM activities
        WHERE athlete_id = ? AND date(started_at) <= ?
    """
    params: list[Any] = [resolved, window["end_date"]]
    if window["start_date"]:
        sql += " AND date(started_at) >= ?"
        params.append(window["start_date"])
    sql += " ORDER BY d"

    return {**window, "training_days": [row["d"] for row in conn.execute(sql, params)]}


# --------------------------------------------------------------------------
# /api/recovery-impact
# --------------------------------------------------------------------------


def get_recovery_impact(
    conn: sqlite3.Connection,
    athlete_id: int | None = None,
    range_key: str = DEFAULT_RANGE,
) -> dict:
    """訓練後隔天的身體狀況變化。

    刻意**不新增資料表**：用 activities LEFT JOIN daily_wellness（訓練當天與隔天）
    即時計算即可，資料量小、且定義之後很可能會調整（現在只看隔天，未來可能改成
    兩天或週均）——存成表反而要做資料遷移。

    delta 一律是「隔天數值 − 訓練當天數值」，只回傳數字，不判斷好壞
    （解讀留給之後的 AI coach 層，這是使用者明確要求）。
    """
    resolved = resolve_athlete_id(conn, athlete_id)
    window = resolve_range(conn, resolved, range_key)
    if resolved is None:
        return {**window, "impacts": []}

    same_day_cols = ", ".join(f"same.{col} AS same_{col}" for col, _, _ in RECOVERY_METRICS)
    next_day_cols = ", ".join(f"nxt.{col} AS next_{col}" for col, _, _ in RECOVERY_METRICS)

    # LEFT JOIN 而非 INNER JOIN：隔天沒有 wellness 資料的活動也必須出現在結果裡，
    # 只是 next_day.available 為 false——省略它們會讓前端誤以為那天沒訓練。
    sql = f"""
        SELECT a.id AS activity_id,
               date(a.started_at) AS training_date,
               date(a.started_at, '+1 day') AS next_date,
               a.activity_type, a.workout_type, a.distance_km,
               nxt.date AS next_wellness_date,
               {same_day_cols},
               {next_day_cols}
        FROM activities a
        LEFT JOIN daily_wellness same
               ON same.athlete_id = a.athlete_id AND same.date = date(a.started_at)
        LEFT JOIN daily_wellness nxt
               ON nxt.athlete_id = a.athlete_id AND nxt.date = date(a.started_at, '+1 day')
        WHERE a.athlete_id = ? AND date(a.started_at) <= ?
    """
    params: list[Any] = [resolved, window["end_date"]]
    if window["start_date"]:
        sql += " AND date(a.started_at) >= ?"
        params.append(window["start_date"])
    sql += " ORDER BY a.started_at DESC"

    impacts = []
    for row in conn.execute(sql, params):
        impacts.append(
            {
                "activity_id": row["activity_id"],
                "training_date": row["training_date"],
                "activity_type": row["activity_type"],
                "workout_type": row["workout_type"],
                "distance_km": row["distance_km"],
                "next_day": _build_next_day(row),
            }
        )
    return {**window, "impacts": impacts}


def _build_next_day(row: sqlite3.Row) -> dict:
    """算出隔天各指標相對於訓練當天的差值。"""
    next_date = row["next_date"]
    if row["next_wellness_date"] is None:
        return {
            "date": next_date,
            "available": False,
            "reason": "隔天沒有每日身體狀況資料",
        }

    result: dict[str, Any] = {"date": next_date, "available": True}
    has_any = False
    for column, key, with_pct in RECOVERY_METRICS:
        same = row[f"same_{column}"]
        nxt = row[f"next_{column}"]
        # 任一天缺值就是缺值，不可當成 0 去相減（那會算出一個假的大幅變化）
        if same is None or nxt is None:
            result[f"{key}_delta"] = None
            if with_pct:
                result[f"{key}_delta_pct"] = None
            continue
        has_any = True
        result[f"{key}_delta"] = _round(nxt - same, 1)
        if with_pct:
            result[f"{key}_delta_pct"] = _round((nxt - same) / same * 100, 1) if same else None

    if not has_any:
        return {
            "date": next_date,
            "available": False,
            "reason": "訓練當天或隔天缺少可比較的指標數值",
        }
    return result
