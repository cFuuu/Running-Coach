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
from src.main.python.services import plan_reconciliation
from src.main.python.services import training_load_queries
from src.main.python.services import training_plan_generator
from src.main.python.services import vdot_engine
from src.main.python.services import race_names as race_names_module
from src.main.python.services.activity_types import (
    PACE_COMPARABLE_RUNNING_TYPES,
    RUNNING_ACTIVITY_TYPES,
)
from src.main.python.services import garmin_export_parser as parser
from src.main.python.services.readiness import DEFAULT_HIGH_RISK_CONSECUTIVE_TRAINING_DAYS
from . import data_confidence

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
    ("hrv_ms", "HRV 夜間均值", "ms", 1, "number"),
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
    # ⚠️ 此欄位存的是 Garmin `acwrFactorPercent`（0–100 量表，代表 ACWR 對
    # readiness 分數的貢獻度），**不是**古典的 0.8–1.3 急慢性負荷比。
    # 見 garmin_export_parser.parse_training_readiness() 的說明。
    # 標籤刻意不用「ACWR」字樣，避免下游（含 AI 對話層）誤讀為比值。
    ("acwr", "Garmin 負荷比指數（0–100）", "", 0, "number"),
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
            """
            SELECT id, name, max_hr_bpm, max_hr_source, resting_hr_bpm
            FROM athlete_profile WHERE id = ?
            """,
            (resolved,),
        ).fetchone()
        if row:
            # HRmax 與其來源標記一併輸出（PLAN.md §7.3 三欄位契約）。
            # 過去只回傳 {id, name}，導致 max_hr_source='watch_display' 這個
            # 信賴度標記存在資料庫卻傳不到判斷層——AI 無從得知所有以 %HRR
            # 推算的強度/負荷/等效配速都帶著同一個系統性偏移。
            hrmax_biases = data_confidence.hrmax_bias_codes(row["max_hr_source"])
            athlete = {
                "id": row["id"],
                "name": row["name"],
                "resting_hr_bpm": row["resting_hr_bpm"],
                "max_hr": data_confidence.fact(
                    row["max_hr_bpm"],
                    source=f"athlete_profile.max_hr_source={row['max_hr_source']}",
                    bias_codes=hrmax_biases,
                ),
            }

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


def _fetch_warmups(conn: sqlite3.Connection, main_ids: list[int]) -> dict[int, dict]:
    """主課 id → 其暖身摘要（N-25）。暖身是另存的獨立活動，見 warmup_links.py。"""
    if not main_ids:
        return {}
    placeholders = ",".join("?" for _ in main_ids)
    return {
        row["warmup_for_activity_id"]: {
            "id": row["id"],
            "started_at": row["started_at"],
            "distance_km": row["distance_km"],
            "duration_sec": row["duration_sec"],
            "avg_pace_sec_per_km": row["avg_pace_sec_per_km"],
            "avg_hr_bpm": row["avg_hr_bpm"],
        }
        for row in conn.execute(
            f"""
            SELECT id, started_at, distance_km, duration_sec, avg_pace_sec_per_km,
                   avg_hr_bpm, warmup_for_activity_id
            FROM activities WHERE warmup_for_activity_id IN ({placeholders})
            """,
            main_ids,
        )
    }


def list_sessions(
    conn: sqlite3.Connection,
    athlete_id: int | None = None,
    range_key: str = DEFAULT_RANGE,
) -> dict:
    """列出 range 內的跑步類活動，依開始時間由新到舊。

    已關聯為暖身的活動不單獨列出，改以 `warmup` 摘要掛在主課上（N-25）——
    否則每堂品質課都多一筆來路不明的短跑，場次數偏多。
    """
    resolved = resolve_athlete_id(conn, athlete_id)
    window = resolve_range(conn, resolved, range_key)
    if resolved is None:
        return {**window, "sessions": []}

    # sorted() 讓 SQL 參數順序穩定（來源是 frozenset，迭代順序不保證）
    running_types = sorted(RUNNING_ACTIVITY_TYPES)
    type_placeholders = ",".join("?" for _ in running_types)
    sql = f"""
        SELECT id, started_at, title, activity_type, workout_type, workout_type_source,
               workout_type_basis, detected_workout_type,
               distance_km, duration_sec, avg_pace_sec_per_km, avg_hr_bpm, max_hr_bpm,
               avg_cadence_spm, aerobic_te
        FROM activities
        WHERE athlete_id = ?
          AND activity_type IN ({type_placeholders})
          AND date(started_at) <= ?
          AND warmup_for_activity_id IS NULL
    """
    params: list[Any] = [resolved, *running_types, window["end_date"]]
    if window["start_date"]:
        sql += " AND date(started_at) >= ?"
        params.append(window["start_date"])
    sql += " ORDER BY started_at DESC"

    rows = conn.execute(sql, params).fetchall()
    warmups = _fetch_warmups(conn, [row["id"] for row in rows])
    sessions = []
    for row in rows:
        sessions.append(
            {
                "id": row["id"],
                "started_at": row["started_at"],
                "date": row["started_at"][:10] if row["started_at"] else None,
                "title": row["title"],
                "activity_type": row["activity_type"],
                "workout_type": row["workout_type"],
                "workout_type_source": row["workout_type_source"],
                # N-28：生效課種的依據（plan／laps）與純分圈偵測結果，兩者不同代表
                # 「課表寫 X、分圈看起來像 Y」
                "workout_type_basis": row["workout_type_basis"],
                "detected_workout_type": row["detected_workout_type"],
                "distance_km": row["distance_km"],
                "duration_sec": row["duration_sec"],
                "avg_pace_sec_per_km": row["avg_pace_sec_per_km"],
                "avg_hr_bpm": row["avg_hr_bpm"],
                "max_hr_bpm": row["max_hr_bpm"],
                "avg_cadence_spm": row["avg_cadence_spm"],
                "aerobic_te": row["aerobic_te"],
                "warmup": warmups.get(row["id"]),
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
               workout_type_basis, detected_workout_type,
               distance_km, duration_sec, avg_pace_sec_per_km, avg_hr_bpm, max_hr_bpm,
               avg_cadence_spm, aerobic_te, elevation_gain_m, calories, raw_data_json,
               source_version, warmup_for_activity_id
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
        "workout_type_basis": row["workout_type_basis"],
        "detected_workout_type": row["detected_workout_type"],
        **_planned_fields(
            _fetch_planned(
                conn,
                row["athlete_id"],
                row["started_at"],
                session_id,
                row["activity_type"],
            )
        ),
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
        # N-25：本場的暖身（另存的獨立活動），以及本場若本身是暖身則指向哪場主課
        "warmup": _fetch_warmups(conn, [session_id]).get(session_id),
        "warmup_for_activity_id": row["warmup_for_activity_id"],
        "hr_zones": _build_hr_zones(
            row["raw_data_json"],
            is_api=row["source_version"] == API_SYNC_SOURCE_VERSION,
        ),
        "laps": _build_laps(conn, session_id, row["raw_data_json"]),
        "records": _build_records_block(records),
        "hr_drift": fit_parser.compute_hr_drift(
            records,
            workout_type=row["workout_type"],
            elevation_gain_m=row["elevation_gain_m"],
            distance_km=row["distance_km"],
        )
        if records
        else {"available": False, "reason": "需要逐秒資料才能計算心率漂移"},
        "assessability": _build_assessability(row),
    }


def _build_assessability(row: sqlite3.Row) -> dict:
    """標示這場活動有哪些判讀維度不可評、為什麼（PLAN.md §7.4 定案）。

    不改既有欄位形狀（dashboard 前端契約依賴它們），而是另闢一個區塊
    告訴判斷層「這裡不要下結論」。`workout_type` 為 unknown/NULL 時
    （全體 266 場中佔 25.6%）不評需要課種前提的維度——unknown 不是
    「未知的課種」而是分類器失敗，拿 easy 的標準評一場其實是間歇的課
    會得到完全錯誤的「執行度不佳」。
    """
    blocked: list[dict] = []
    codes = data_confidence.workout_type_bias_codes(row["workout_type"])
    if codes:
        bias = data_confidence.describe_bias(codes[0])
        blocked.append(
            {
                "dimensions": ["pace_execution", "workout_type_conformance"],
                "reason": bias["detail"],
                "bias_code": bias["code"],
            }
        )
    return {
        "blocked": blocked,
        "note": (
            "blocked 內列出的維度**不得**做判讀，缺的是前提而非資料品質微瑕；"
            "其餘維度仍可評，但須併同各自的 confidence 標記一起考量"
        ),
    }


def _planned_fields(fetched: tuple[dict | None, dict]) -> dict:
    """把 `_fetch_planned` 的回傳攤平成 session detail 的兩個欄位。"""
    planned, match = fetched
    return {"planned": planned, "planned_match": match}


def _fetch_planned(
    conn: sqlite3.Connection,
    athlete_id: int,
    started_at: str | None,
    session_id: int,
    activity_type: str | None,
) -> tuple[dict | None, dict]:
    """找出這場活動對應的計畫課表。

    回傳 `(planned, planned_match)`：

    - `planned`：配對成功時的課表內容；**歧義或無對應時為 None**。
      依 §7.3 硬規則，寧可不輸出，也不給一個可能配錯的計畫。
      成功時附 `confidence`（推論而來的對應標記為 low，見
      `data_confidence.KNOWN_BIASES["plan_match_inferred"]`）。
    - `planned_match`：**永遠**回傳，說明配對結果與原因。歧義時
      `state == "ambiguous"`，讓判斷層知道「當天有課表但無法確定是這場執行的」，
      而不是誤以為當天根本沒排課。

    配對規則見 `plan_reconciliation` 模組；本函式只負責取資料與包裝。
    """
    empty_match = {"state": "none", "method": None, "reason": "no_activity_date"}
    if not started_at:
        return None, empty_match

    plan_date = started_at[:10]
    plan_rows = [
        dict(row)
        for row in conn.execute(
            "SELECT id, workout_type, planned_distance_km, planned_pace_sec_per_km, "
            "notes, linked_activity_id FROM training_plan "
            "WHERE athlete_id = ? AND planned_date = ? AND is_active = 1 ORDER BY id",
            (athlete_id, plan_date),
        )
    ]
    same_day = [
        dict(row)
        for row in conn.execute(
            "SELECT id, activity_type FROM activities "
            "WHERE athlete_id = ? AND date(started_at) = ?",
            (athlete_id, plan_date),
        )
    ]
    result = plan_reconciliation.match_activity_to_plan(
        {"id": session_id, "activity_type": activity_type}, plan_rows, same_day
    )
    match_meta = {
        "state": result["state"],
        "method": result["method"],
        "reason": result["reason"],
    }
    plan = result["plan"]
    if plan is None:
        return None, match_meta

    bias_codes = ["plan_match_inferred"] if result["state"] == "inferred" else []
    payload = {
        "workout_type": plan["workout_type"],
        "planned_distance_km": plan["planned_distance_km"],
        "planned_pace_sec_per_km": plan["planned_pace_sec_per_km"],
        "notes": plan["notes"],
    }
    payload["confidence"] = {
        "level": data_confidence.combine_level(bias_codes),
        "biases": [data_confidence.describe_bias(c) for c in bias_codes],
    }
    return payload, match_meta


def get_activity_plan_match(
    conn: sqlite3.Connection, athlete_id: int, activity_id: int
) -> dict | None:
    """單場活動目前的 `plan_match`（`{state, method, reason}`），與行事曆、單場明細
    同一套配對（走 `_fetch_planned`）。供 `PUT /api/plan/{id}/link` 回報寫入後的結果；
    活動不存在或不屬於這位學員時回 None。
    """
    row = conn.execute(
        "SELECT started_at, activity_type FROM activities WHERE id = ? AND athlete_id = ?",
        (activity_id, athlete_id),
    ).fetchone()
    if row is None:
        return None
    _, match = _fetch_planned(
        conn, athlete_id, row["started_at"], activity_id, row["activity_type"]
    )
    return match


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

# `activities.source_version` 標記為此值者來自 API 同步，其 raw payload 的
# 單位與官方匯出包不同（見 _build_hr_zones 的 docstring）。
# ⚠️ 這裡刻意重寫字面值而不 import garmin_sync_runner.SYNC_SOURCE_VERSION：
# 那個模組在 module level 匯入 garmin_api_client／garmin_auth，會把網路相依
# 拖進純查詢層（FastAPI 與 MCP server 都會載入本模組）。兩邊一致性改由
# test_dashboard_queries.test_api_sync_source_version_matches_sync_runner 強制。
API_SYNC_SOURCE_VERSION = "api_sync"


def _build_hr_zones(raw_data_json: str | None, *, is_api: bool = False) -> dict:
    """從 raw_data_json 取心率區間停留秒數（Garmin 的 hrTimeInZone_N 欄位）。

    ⚠️ **同一個欄位名在兩個來源是不同單位，必須由呼叫端告知來源**：

    - **官方匯出包（`is_api=False`）是毫秒**：例如某場 `hrTimeInZone_2 = 3629313`，
      換算 60.5 分鐘與該場實際時長吻合，須用 `ms_to_sec()` 換算。
    - **API 同步（`is_api=True`）是秒**：與同一份 payload 的 `duration` 一致，
      **不可**再除以 1000。

    來源以 `activities.source_version == "api_sync"` 判定（見 `API_SYNC_SOURCE_VERSION`）。
    2026-09-02 對真實資料庫全掃驗證此規則無例外：290 場有心率區間者中，7 場
    api_sync 全為秒、283 場匯出包全為毫秒。

    ⚠️ **不要改成「直接讀 `activities.hr_time_in_zone_json` 欄位」**——那個欄位
    刻意只存 Z1~Z5、不存 Z0，而本函式的百分比分母**必須含 Z0**（見下方說明），
    改讀該欄位會讓各區間佔比虛增，正是 2026-08-18 改版 Phase 2 修掉的那個 bug；
    且該欄位僅涵蓋 203/529 場，退回路徑無論如何都省不掉。

    **此錯誤已實際發生過**（2026-09-02 Phase 3 驗收時發現）：1B-9 修正了寫入端
    的單位假設卻沒動讀取端，導致 API 同步的活動心率區間全被縮小 1000 倍——
    5648 秒的長跑顯示為 5 秒。故此處的 `is_api` 不是可省略的最佳化，是正確性前提。

    ⚠️ **跨來源比較百分比時的已知限制**：API payload **不含 `hrTimeInZone_0`**
    （2026-09-02 實測 12 筆 api_sync 全數只有 Z1~Z5），故 API 來源的活動
    `below_zone_1` 恆為 0、分母只有 Z1~Z5；匯出包來源則含 Z0 暖身時間。
    兩者的 `pct` 分母語意因此不同，跨來源並排比較時分母差一段暖身時間。
    這是資料可得性的限制，不是計算錯誤——**不要為了「一致」而把匯出包的 Z0
    也排除**，那會回頭破壞既有場次的佔比正確性。

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
        seconds = round(value) if is_api else parser.ms_to_sec(value)
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


# --------------------------------------------------------------------------
# /api/today
# --------------------------------------------------------------------------


def _fetch_plan_row(
    conn: sqlite3.Connection, athlete_id: int, plan_date: str
) -> dict | None:
    """取某天生效中的課表（若當天有多筆，只取第一筆——多筆同日課表是
    N-12 記載的既有資料異常，今日卡只呈現不解讀，故不在此處理歧義）。
    """
    row = conn.execute(
        """
        SELECT workout_type, planned_distance_km, planned_pace_sec_per_km,
               notes, plan_source, created_at
        FROM training_plan
        WHERE athlete_id = ? AND planned_date = ? AND is_active = 1
        ORDER BY id LIMIT 1
        """,
        (athlete_id, plan_date),
    ).fetchone()
    if row is None:
        return None
    return {
        "workout_type": row["workout_type"],
        "planned_distance_km": row["planned_distance_km"],
        "planned_pace_sec_per_km": row["planned_pace_sec_per_km"],
        "notes": row["notes"],
        "plan_source": row["plan_source"],
        "created_at": row["created_at"],
    }


def get_today(
    conn: sqlite3.Connection,
    athlete_id: int | None = None,
    today: datetime.date | None = None,
    race_schedule: dict | None = None,
) -> dict:
    """今日卡：今日／明日課表、規則引擎的準備度判讀、訓練負荷、最近一場活動。

    today 可覆寫（預設系統今天）——供測試與需要重現特定日期畫面時使用。

    race_schedule：學員檔的目標賽事與賽程（race_names.load_race_schedule()），
    用來輸出 races（下場賽事／目標賽事剩餘天數）；省略時 races 為 None。

    純呈現層：本函式不解讀「今天該不該練」，只把規則引擎
    （readiness.py／training_load.py）已經算出的事實依 §7.3 三欄位契約
    包裝後透出。信賴度為 unusable 時 data_confidence.fact() 回傳 None，
    前端須顯示「未評」而非省略欄位。
    """
    resolved = resolve_athlete_id(conn, athlete_id)
    today = today or datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)
    races = race_names_module.race_countdown(race_schedule, today) if race_schedule else None

    if resolved is None:
        return {
            "date": today.isoformat(),
            "freshness": None,
            "races": races,
            "planned": {"today": None, "tomorrow": None},
            "readiness": None,
            "load": None,
            "hrv": None,
            "garmin_reference": None,
            "consecutive_training_days": None,
            "last_activity": None,
        }

    latest_wellness_row = conn.execute(
        "SELECT MAX(date) AS d FROM daily_wellness WHERE athlete_id = ?", (resolved,)
    ).fetchone()
    latest_activity_row = conn.execute(
        "SELECT MAX(date(started_at)) AS d FROM activities WHERE athlete_id = ?",
        (resolved,),
    ).fetchone()
    wellness_latest = latest_wellness_row["d"] if latest_wellness_row else None
    activity_latest = latest_activity_row["d"] if latest_activity_row else None
    most_recent = max((d for d in (wellness_latest, activity_latest) if d), default=None)
    days_behind = (
        (today - datetime.date.fromisoformat(most_recent)).days
        if most_recent
        else None
    )

    planned_today = _fetch_plan_row(conn, resolved, today.isoformat())
    planned_tomorrow = _fetch_plan_row(conn, resolved, tomorrow.isoformat())

    # 準備度：查一段涵蓋今天在內、往回抓幾天的窗，找出「今天」與
    # 「往回第一個真正可評的日期」（N-13：今天很可能還沒同步，None
    # 不代表異常，但今日卡想同時呈現「最近一次真正評過的狀態」）。
    lookback_start = today - datetime.timedelta(days=_READINESS_LOOKBACK_DAYS)
    readiness_series = training_load_queries.compute_readiness_for_athlete(
        conn, resolved, lookback_start, today
    )
    readiness_by_date = {r["date"]: r for r in readiness_series}
    today_readiness = readiness_by_date.get(today)
    latest_evaluable = next(
        (r for r in reversed(readiness_series) if r["readiness"] is not None),
        None,
    )

    # 今天常常還沒同步（手錶隔天早上才上傳）。準備度與負荷都退回
    # _TODAY_FALLBACK_DAYS 天內最近一個有資料的日子，並標明是哪一天
    # （與下方 HRV 的退回同一個窗口；超出窗口就誠實顯示無資料）。
    fallback_start = today - datetime.timedelta(days=_TODAY_FALLBACK_DAYS)
    today_fallback = None
    if (today_readiness is None or today_readiness["readiness"] is None) and latest_evaluable \
            and latest_evaluable["date"] >= fallback_start:
        today_fallback = latest_evaluable

    # N-13 同一原則套用到負荷面板：compute_daily_loads() 對「今天」一律填
    # load=0.0 才能遞推 ATL/CTL/TSB，今天完全無資料時這個 0 是假設而非
    # 事實，直接顯示出來的 TSB 會跟 readiness.today 為 None 互相矛盾
    # （一邊說「未評」，另一邊卻煞有介事顯示數字）。故今天若在
    # get_dates_without_any_data() 裡，改用窗口內最近一個有資料日子的負荷，
    # 並以 as_of 標明日期；窗口內都沒資料才不輸出。
    no_data_dates = training_load_queries.get_dates_without_any_data(
        conn, resolved, fallback_start, today
    )
    load_date = next(
        (
            today - datetime.timedelta(days=offset)
            for offset in range(_TODAY_FALLBACK_DAYS + 1)
            if today - datetime.timedelta(days=offset) not in no_data_dates
        ),
        None,
    )

    # 起點刻意與上方準備度序列相同（lookback_start）：ATL/CTL 是指數加權遞推，
    # 暖機起點不同會讓同一天算出略有差異的 TSB，同一頁上「訓練負荷」與
    # 「訓練完備程度」就會各說各話（實測 -14.1 vs -14.4）。
    load_series = (
        training_load_queries.compute_training_load_for_athlete(
            conn, resolved, lookback_start, load_date
        )
        if load_date is not None
        else []
    )
    load_today = load_series[-1] if load_series else None

    hrmax_row = conn.execute(
        "SELECT max_hr_source FROM athlete_profile WHERE id = ?", (resolved,)
    ).fetchone()
    max_hr_source = hrmax_row["max_hr_source"] if hrmax_row else None
    hrmax_biases = data_confidence.hrmax_bias_codes(max_hr_source)

    load_fact = None
    if load_today is not None:
        tsb_bias_codes = [*hrmax_biases, "tsb_percentile_engineering"]
        load_fact = {
            "atl": data_confidence.fact(
                _round(load_today["atl"]), source="Banister EWMA（7 日時間常數）",
                bias_codes=hrmax_biases,
            ),
            "ctl": data_confidence.fact(
                _round(load_today["ctl"]), source="Banister EWMA（42 日時間常數）",
                bias_codes=hrmax_biases,
            ),
            "tsb": data_confidence.fact(
                _round(load_today["tsb"]), source="TSB = CTL − ATL",
                bias_codes=tsb_bias_codes,
            ),
            # 非 None 代表今天尚未同步，數值是這一天的；前端必須標注
            "as_of": None if load_date == today else load_date.isoformat(),
        }

    wellness_today_row = conn.execute(
        """
        SELECT hrv_ms, hrv_weekly_avg_ms, hrv_status,
               hrv_baseline_balanced_low, hrv_baseline_balanced_upper,
               hrv_last_night_5min_high
        FROM daily_wellness WHERE athlete_id = ? AND date = ?
        """,
        (resolved, today.isoformat()),
    ).fetchone()

    # 今天的身體數據常常還沒同步（手錶隔天早上才上傳），此時整張 HRV 卡
    # 會是空的，但昨天的資料其實在庫裡。退回最近一筆並標明是哪一天。
    #
    # 【窗口為何只有 _TODAY_FALLBACK_DAYS 天，不沿用 readiness 的 14 天】
    # N-13 的硬規則是「沒資料就不可假裝評估過」。HRV 是逐日變動的生理
    # 指標，兩週前的數值拿來當「今日 HRV」已不是延遲、而是錯的資訊。
    # 只涵蓋「手錶還沒同步今天」這一種情況，超出窗口一律回 None，
    # 讓卡片誠實地顯示無資料。日期一定隨值回傳，前端必須標注。
    hrv_as_of: str | None = None
    if wellness_today_row is None or wellness_today_row["hrv_ms"] is None:
        fallback_row = conn.execute(
            """
            SELECT date, hrv_ms, hrv_weekly_avg_ms, hrv_status,
                   hrv_baseline_balanced_low, hrv_baseline_balanced_upper,
                   hrv_last_night_5min_high
            FROM daily_wellness
            WHERE athlete_id = ? AND date <= ? AND date >= ? AND hrv_ms IS NOT NULL
            ORDER BY date DESC LIMIT 1
            """,
            (resolved, today.isoformat(), fallback_start.isoformat()),
        ).fetchone()
        if fallback_row is not None:
            wellness_today_row = fallback_row
            hrv_as_of = fallback_row["date"]

    hrv_fact = None
    if wellness_today_row is not None:
        hrv_ms = wellness_today_row["hrv_ms"]
        hrv_weekly_avg_ms = wellness_today_row["hrv_weekly_avg_ms"]
        delta_pct = None
        if hrv_ms is not None and hrv_weekly_avg_ms:
            delta_pct = _round((hrv_ms - hrv_weekly_avg_ms) / hrv_weekly_avg_ms * 100, 1)
        hrv_fact = {
            "today_ms": data_confidence.fact(hrv_ms, source="daily_wellness.hrv_ms")
            if hrv_ms is not None
            else None,
            "weekly_avg_ms": data_confidence.fact(
                hrv_weekly_avg_ms, source="daily_wellness.hrv_weekly_avg_ms（7 日滾動均值）"
            )
            if hrv_weekly_avg_ms is not None
            else None,
            "last_night_5min_high_ms": data_confidence.fact(
                wellness_today_row["hrv_last_night_5min_high"],
                source="daily_wellness.hrv_last_night_5min_high（昨夜最高 5 分鐘平均）",
            )
            if wellness_today_row["hrv_last_night_5min_high"] is not None
            else None,
            "delta_pct": data_confidence.fact(
                delta_pct,
                source="(今日 - 7日均值) / 7日均值",
                bias_codes=["hrv_pct_form_unvalidated"],
            )
            if delta_pct is not None
            else None,
            # ---- Garmin（Firstbeat）自己的 HRV 狀態分類與平衡區間 ----
            #
            # 【為什麼這幾個欄位不走 data_confidence.fact()】
            # fact() 對 unusable 級偏誤一律回 None，而 garmin_blackbox 正是
            # unusable——那條規則保護的是「判斷層不得使用無法評估可信度的數字」，
            # 目的正確，這裡也完全遵守：規則引擎不讀這個欄位。2026-10-23 起 AI 教練可
            # 在標明「Garmin 參考」的前提下引用（見行為約束第 8 條），但不得當判讀依據。
            #
            # 但「不給判斷層用」與「不讓使用者看見手錶顯示了什麼」是兩件事。
            # 使用者手錶上明明寫著「不平衡」，儀表板卻完全不提，反而會讓人
            # 以為資料沒同步。因此改走 garmin_reference 這個獨立形狀：
            # 明確標 for_display_only，與 fact() 的三欄位契約長得不一樣，
            # 下游若誤把它當 fact 用會立刻壞掉（而不是靜默混入判斷）。
            #
            # 狀態是拿 7 日均值（hrv_weekly_avg_ms）對平衡區間判的，不是昨夜
            # 均值（hrv_ms）：實測 39/39 天吻合，昨夜均值只有 29/39。見 TODO N-20。
            "garmin_reference": {
                "for_display_only": True,
                "note": "Garmin（Firstbeat）黑盒演算法輸出，公式未公開，僅供參考；引用須標明來源，不得單憑它下結論",
                "status": wellness_today_row["hrv_status"],
                "baseline_balanced_low": wellness_today_row["hrv_baseline_balanced_low"],
                "baseline_balanced_upper": wellness_today_row["hrv_baseline_balanced_upper"],
            }
            if wellness_today_row["hrv_status"] is not None
            else None,
            # 非 None 代表這張卡顯示的不是今天，而是這個日期的資料。
            # 前端必須據此標注，否則使用者會把昨天的數字當成今天的。
            "as_of": hrv_as_of,
        }

    consecutive_training_days = None
    if today_readiness is not None:
        consecutive_training_days = {
            "value": today_readiness["consecutive_training_days"],
            "threshold": (
                DEFAULT_HIGH_RISK_CONSECUTIVE_TRAINING_DAYS
                if today_readiness["threshold_source"] == "default"
                else _fetch_personalized_threshold(conn, resolved)
            ),
            "threshold_source": today_readiness["threshold_source"],
        }

    last_activity_row = conn.execute(
        """
        SELECT id, date(started_at) AS d, activity_type, workout_type,
               distance_km, avg_pace_sec_per_km, avg_hr_bpm
        FROM activities
        WHERE athlete_id = ? AND date(started_at) <= ?
        ORDER BY started_at DESC LIMIT 1
        """,
        (resolved, today.isoformat()),
    ).fetchone()
    last_activity = None
    if last_activity_row is not None:
        last_activity = {
            "id": last_activity_row["id"],
            "date": last_activity_row["d"],
            "activity_type": last_activity_row["activity_type"],
            "workout_type": last_activity_row["workout_type"],
            "distance_km": last_activity_row["distance_km"],
            "avg_pace_sec_per_km": last_activity_row["avg_pace_sec_per_km"],
            "avg_hr_bpm": last_activity_row["avg_hr_bpm"],
        }

    return {
        "date": today.isoformat(),
        "freshness": {
            "wellness_latest": wellness_latest,
            "activity_latest": activity_latest,
            "days_behind": days_behind,
            # 側邊列底部「最後更新時間」用：同步時刻（UTC，帶 Z）與最新活動的開始時間
            **{k: v for k, v in _data_freshness(conn, resolved).items() if k != "wellness_latest"},
        },
        "races": races,
        "planned": {"today": planned_today, "tomorrow": planned_tomorrow},
        "readiness": {
            "today": _serialize_readiness_entry(today_readiness),
            "latest_evaluable": _serialize_readiness_entry(latest_evaluable),
            # 今天不可評、但退回窗口內有可評日時，給前端代替今日顯示（須標注日期）
            "today_fallback": _serialize_readiness_entry(today_fallback),
        },
        "load": load_fact,
        "hrv": hrv_fact,
        "garmin_reference": _garmin_health_reference(conn, resolved, today),
        "consecutive_training_days": consecutive_training_days,
        "last_activity": last_activity,
    }


def _garmin_health_reference(
    conn: sqlite3.Connection, athlete_id: int, today: datetime.date
) -> dict | None:
    """Garmin 健康狀態卡（HR／HRV／呼吸／血氧）的參考摘要，供首頁「注意」旗標與 AI 教練引用。

    範圍與判定（IN_RANGE／BELOW／ABOVE）都是 Garmin 依個人歷史推算的輸出，本專案
    不重算。2026-10-23 起：**規則引擎（readiness.py）仍不讀**，但首頁與 AI 教練可在
    標明「Garmin 參考」的前提下引用；不走 data_confidence.fact()，刻意與三欄位契約
    長得不同（reference_only），下游若誤當 fact 用會立刻壞掉。
    今天還沒同步時退回 _TODAY_FALLBACK_DAYS 內最近一天，並以 as_of 標明。
    """
    window_start = today - datetime.timedelta(days=_TODAY_FALLBACK_DAYS)
    latest = conn.execute(
        "SELECT MAX(date) AS d FROM health_status WHERE athlete_id = ? AND date <= ? AND date >= ?",
        (athlete_id, today.isoformat(), window_start.isoformat()),
    ).fetchone()["d"]
    if latest is None:
        return None
    rows = conn.execute(
        "SELECT metric, value, baseline_lower, baseline_upper, status "
        "FROM health_status WHERE athlete_id = ? AND date = ?",
        (athlete_id, latest),
    ).fetchall()
    # 狀態為 NULL 視為 Garmin 沒給判定，不算異常（與只看 IN_RANGE 以外的 BELOW／ABOVE 一致）
    outliers = [
        {
            "metric": r["metric"],
            "status": r["status"],
            "value": r["value"],
            "lower": r["baseline_lower"],
            "upper": r["baseline_upper"],
        }
        for r in rows
        if r["status"] is not None and r["status"] != "IN_RANGE"
    ]
    return {
        "reference_only": True,
        "note": (
            "Garmin 手錶（Firstbeat）依個人歷史算出的健康狀態，演算法未公開，只供參考；"
            "不是本專案的規則。引用時須標明來源，不可單憑它建議休息或調整課表"
        ),
        "health_status": {
            "date": latest,
            # 非 None 代表這不是今天的資料（今天尚未同步），前端與回述都必須標注
            "as_of": None if latest == today.isoformat() else latest,
            "metrics_total": len(rows),
            "outliers": outliers,
        },
        # 首頁「綜合整備狀態」：本專案規則都沒觸發（readiness 為 normal）而此值為 True 時，改顯示「注意」
        "caution": len(outliers) > 0,
    }


# 準備度往回查幾天找「最近一次可評日」——超過這個範圍代表資料同步斷了
# 太久，今日卡不再往回找，交由 freshness.days_behind 呈現這個事實即可。
_READINESS_LOOKBACK_DAYS = 14

# 今日卡的退回窗口（天）：HRV、準備度、訓練負荷共用。只為了涵蓋「手錶今天
# 還沒同步」，不是資料補洞機制——超過這個天數就誠實顯示無資料（見 get_today）。
_TODAY_FALLBACK_DAYS = 2



def _fetch_personalized_threshold(conn: sqlite3.Connection, athlete_id: int) -> int | None:
    row = conn.execute(
        "SELECT high_risk_consecutive_training_days FROM athlete_profile WHERE id = ?",
        (athlete_id,),
    ).fetchone()
    return row["high_risk_consecutive_training_days"] if row else None


def _serialize_readiness_entry(entry: dict | None) -> dict | None:
    """把 readiness.assess_readiness() 一筆輸出轉成今日卡要的形狀
    （date 轉字串，其餘挑今日卡要的欄位透傳）。entry 為 None 時（該區間內
    完全沒有可評的日子）回傳 None。measurements 是各維度的「數值 / 門檻」。
    """
    if entry is None:
        return None
    return {
        "date": entry["date"].isoformat(),
        "readiness": entry["readiness"],
        "triggers": entry["triggers"],
        "dimensions_evaluated": entry["dimensions_evaluated"],
        "not_assessed": entry["not_assessed"],
        "measurements": entry["measurements"],
        "hrv_trend": entry["hrv_trend"],
    }


# --------------------------------------------------------------------------
# /api/calendar
# --------------------------------------------------------------------------


def get_calendar(
    conn: sqlite3.Connection,
    athlete_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    today: datetime.date | None = None,
    easy_pace_range: tuple[int, int] | None = None,
) -> dict:
    """[start, end] 內每一天的課表與實際活動（全部運動類型），供週條與
    行事曆共用。**刻意不走 resolve_range()**——那個函式錨定「資料庫最新
    一天」，會讓行事曆被截斷；行事曆要用真實今天當作參照點（呼應既有
    Phase 6 規格：行事曆用真實今天，其他 range 圖表維持錨定資料庫最新日）。

    easy_pace_range：學員檔的輕鬆配速 (較快, 較慢) 秒/公里。課表只寫時間、沒寫距離的
    課，用「時間 ÷ 區間中點」推算 estimated_distance_km；沒給或課表已有距離則為 None。
    推算值與 planned_distance_km 分開放，呈現端才分得出哪個是教練寫的、哪個是估的。

    課表與活動的配對重用 plan_reconciliation.match_activity_to_plan()——
    與單場明細（_fetch_planned）走同一套規則，避免行事曆另立一份配對邏輯。

    day_state 是狀態分類，不是評價：
        - "rest_ok"：當天無跑步課表（無課表，或課表為 rest），且無跑步活動
        - "planned_done"：當天有跑步課表，且配對成功（exact/inferred），
          或同日多場跑步搶同一份課表（單場為 ambiguous，但當天確有執行）
        - "planned_missed"：**今天以前**有跑步課表，但沒有任何活動可配對
        - "today_pending"：**今天**有跑步課表、尚無配對成功的活動——今天
          還沒過完，不可判定為「錯過」，故與 planned_missed 分開
        - "unplanned_activity"：當天有跑步活動，但無對應課表可配對成功
          （含歧義／無課表兩種情況；不細分是因為兩者對「有練但沒排」
          這件事的呈現相同）
        - "future"：today 之後，尚未發生
    """
    resolved = resolve_athlete_id(conn, athlete_id)
    today = today or datetime.date.today()
    if resolved is None or not start or not end:
        return {"start": start, "end": end, "today": today.isoformat(), "days": []}

    plan_rows = conn.execute(
        """
        SELECT id, planned_date, workout_type, planned_distance_km, planned_duration_sec,
               planned_pace_sec_per_km, plan_source, linked_activity_id, notes
        FROM training_plan
        WHERE athlete_id = ? AND is_active = 1
          AND planned_date >= ? AND planned_date <= ?
        ORDER BY planned_date, id
        """,
        (resolved, start, end),
    ).fetchall()
    plans_by_date: dict[str, list[dict]] = {}
    easy_mid_pace = sum(easy_pace_range) / 2 if easy_pace_range else None
    for row in plan_rows:
        plan = dict(row)
        plan["estimated_distance_km"] = (
            plan["planned_duration_sec"] / easy_mid_pace
            if easy_mid_pace
            and not plan["planned_distance_km"]
            and plan["planned_duration_sec"]
            and plan["workout_type"] in plan_reconciliation.RUNNING_WORKOUT_TYPES
            else None
        )
        plans_by_date.setdefault(row["planned_date"], []).append(plan)

    activity_rows = conn.execute(
        """
        SELECT id, date(started_at) AS d, activity_type, workout_type,
               title, distance_km, duration_sec, avg_hr_bpm, warmup_for_activity_id
        FROM activities
        WHERE athlete_id = ? AND date(started_at) >= ? AND date(started_at) <= ?
        ORDER BY started_at
        """,
        (resolved, start, end),
    ).fetchall()
    activities_by_date: dict[str, list[sqlite3.Row]] = {}
    for row in activity_rows:
        activities_by_date.setdefault(row["d"], []).append(row)

    start_date = datetime.date.fromisoformat(start)
    end_date = datetime.date.fromisoformat(end)
    days = []
    current = start_date
    while current <= end_date:
        date_str = current.isoformat()
        day_plans = plans_by_date.get(date_str, [])
        day_activities = activities_by_date.get(date_str, [])
        same_day_for_match = [
            {"id": a["id"], "activity_type": a["activity_type"]} for a in day_activities
        ]

        activities_out = []
        any_matched = False
        any_executed = False
        for activity_row in day_activities:
            match = plan_reconciliation.match_activity_to_plan(
                {"id": activity_row["id"], "activity_type": activity_row["activity_type"]},
                day_plans,
                same_day_for_match,
            )
            if match["state"] in ("exact", "inferred"):
                any_matched = True
            # 整天層級只問「有沒有執行」，不問「是哪一場」：熱身另存一場時兩場
            # 都是 ambiguous，但當天確實有相容活動，不可判成未執行
            if (
                match["state"] != "none"
                and activity_row["activity_type"] in RUNNING_ACTIVITY_TYPES
            ):
                any_executed = True
            activities_out.append(
                {
                    "id": activity_row["id"],
                    "activity_type": activity_row["activity_type"],
                    "is_running": activity_row["activity_type"] in RUNNING_ACTIVITY_TYPES,
                    "workout_type": activity_row["workout_type"],
                    "distance_km": activity_row["distance_km"],
                    "duration_sec": activity_row["duration_sec"],
                    "avg_hr_bpm": activity_row["avg_hr_bpm"],
                    "title": activity_row["title"],
                    # N-25：非 NULL 代表這筆是暖身，值為主課的 activity id
                    "warmup_for_activity_id": activity_row["warmup_for_activity_id"],
                    "plan_match": {
                        "state": match["state"],
                        "method": match["method"],
                        "reason": match["reason"],
                    },
                }
            )

        has_running_plan = any(
            p["workout_type"] in plan_reconciliation.RUNNING_WORKOUT_TYPES for p in day_plans
        )
        has_running_activity = any(a["is_running"] for a in activities_out)

        if current > today:
            day_state = "future"
        elif has_running_plan and (any_matched or any_executed):
            day_state = "planned_done"
        elif has_running_plan and current == today:
            day_state = "today_pending"
        elif has_running_plan:
            day_state = "planned_missed"
        elif has_running_activity and not any_matched:
            day_state = "unplanned_activity"
        else:
            day_state = "rest_ok"

        days.append(
            {
                "date": date_str,
                "planned": [
                    {
                        "id": p["id"],
                        "workout_type": p["workout_type"],
                        "planned_distance_km": p["planned_distance_km"],
                        "planned_pace_sec_per_km": p["planned_pace_sec_per_km"],
                        "planned_duration_sec": p["planned_duration_sec"],
                        "estimated_distance_km": p["estimated_distance_km"],
                        "plan_source": p["plan_source"],
                        # 外部課表的教練原文（跑班寫法如 P"6:05-5:55、r400m3'30）放在這裡，
                        # 前端顯示原文而非只顯示課種，避免資訊被壓扁成「節奏跑 13 km」
                        "notes": p["notes"],
                    }
                    for p in day_plans
                ],
                "activities": activities_out,
                "day_state": day_state,
            }
        )
        current += datetime.timedelta(days=1)

    return {"start": start, "end": end, "today": today.isoformat(), "days": days}


# --------------------------------------------------------------------------
# /api/training-load
# --------------------------------------------------------------------------


def get_training_load(
    conn: sqlite3.Connection,
    athlete_id: int | None = None,
    range_key: str = DEFAULT_RANGE,
) -> dict:
    """訓練負荷面板：range 內每日的 ATL/CTL/TSB 時間序列，各自帶信賴度。

    走既有 resolve_range()——與其他 range 端點（sessions／wellness-trend
    等）共用同一套 range 切換，錨定「資料庫最新日」而非真實今天
    （與 /api/today／/api/calendar 的錨點刻意不同，理由見各自的說明）。
    """
    resolved = resolve_athlete_id(conn, athlete_id)
    window = resolve_range(conn, resolved, range_key)
    if resolved is None or not window["start_date"]:
        return {**window, "series": [], "garmin_reference": None}

    start_date = datetime.date.fromisoformat(window["start_date"])
    end_date = datetime.date.fromisoformat(window["end_date"])

    hrmax_row = conn.execute(
        "SELECT max_hr_source FROM athlete_profile WHERE id = ?", (resolved,)
    ).fetchone()
    hrmax_biases = data_confidence.hrmax_bias_codes(
        hrmax_row["max_hr_source"] if hrmax_row else None
    )
    tsb_bias_codes = [*hrmax_biases, "tsb_percentile_engineering"]

    load_series = training_load_queries.compute_training_load_for_athlete(
        conn, resolved, start_date, end_date
    )

    series = []
    for day in load_series:
        series.append(
            {
                "date": day["date"].isoformat(),
                "atl": data_confidence.fact(
                    _round(day["atl"]), source="Banister EWMA（7 日時間常數）",
                    bias_codes=hrmax_biases,
                ),
                "ctl": data_confidence.fact(
                    _round(day["ctl"]), source="Banister EWMA（42 日時間常數）",
                    bias_codes=hrmax_biases,
                ),
                "tsb": data_confidence.fact(
                    _round(day["tsb"]), source="TSB = CTL − ATL",
                    bias_codes=tsb_bias_codes,
                ),
                "uncertain": day["uncertain"],
            }
        )

    return {
        **window,
        "series": series,
        "garmin_reference": _garmin_load_reference(conn, resolved, start_date, end_date),
    }


def _garmin_load_reference(
    conn: sqlite3.Connection,
    athlete_id: int,
    start_date: datetime.date,
    end_date: datetime.date,
) -> dict | None:
    """Garmin 自己的急性／慢性負荷與訓練狀態，僅供對照（2026-10-21 Fu 拍板）。

    主指標是本專案的 TSB；Garmin 的單場負荷是 EPOC 黑盒、尺度與 TRIMP 不同，
    只能對照方向，不可比絕對值或相加。與 _garmin_vdot_reference() 同一個
    形狀：for_display_only，判斷層不讀。

    ⚠️ training_status.chronic_load_optimal_min/max 欄位名稱有誤導：真實資料
    每列都恰為 chronic_load × 0.8／× 1.5，是**急性負荷**的建議範圍（即 ACWR
    0.8–1.5），這裡輸出時改用符合語意的鍵名。
    """
    rows = conn.execute(
        """
        SELECT date, training_status_phrase, acute_load, chronic_load, acwr_ratio,
               acwr_status, chronic_load_optimal_min, chronic_load_optimal_max
        FROM training_status
        WHERE athlete_id = ? AND date >= ? AND date <= ?
        ORDER BY date
        """,
        (athlete_id, start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    if not rows:
        return None
    return {
        "for_display_only": True,
        "note": "Garmin（Firstbeat）以 EPOC 推估的負荷，演算法未公開、尺度與本專案 TRIMP 不同，"
                "僅供對照方向，不得作為判讀依據",
        "series": [
            {
                "date": row["date"],
                "status": row["training_status_phrase"],
                "acute_load": row["acute_load"],
                "chronic_load": row["chronic_load"],
                "acwr": row["acwr_ratio"],
                "acwr_status": row["acwr_status"],
                "acute_load_optimal_min": _round(row["chronic_load_optimal_min"]),
                "acute_load_optimal_max": _round(row["chronic_load_optimal_max"]),
            }
            for row in rows
        ],
    }


# --------------------------------------------------------------------------
# /api/overview
# --------------------------------------------------------------------------

# 「有效訓練」的篩選條件（2026-10-14 Fu 拍板）。意思是「資料品質足以拿來分析
# 的場次」，**不是**「練得好不好」——後者要對照課表，屬 session_quality 的範疇。
# 排除的是手錶誤觸、起跑就停、沒戴心率的場次。
EFFECTIVE_SESSION_MIN_DURATION_SEC = 600
EFFECTIVE_SESSION_MIN_DISTANCE_KM = 1.0

# 強度分層：心率區間 → 低／中／高。區間沿用 Garmin 手錶上設定的 Z1~Z5
# （%HRR 五區），**不是** Daniels 的配速區，不可套上「馬拉松區」「閾值區」
# 之類的名稱。Z0（低於 Z1 的暖身／停等時間）歸入低強度。
INTENSITY_TIER_ZONES: dict[str, tuple[int, ...]] = {
    "low": (HR_ZONE_BELOW_FIRST, 1, 2),
    "mid": (3,),
    "high": (4, 5),
}

# VDOT 對照用的 Garmin 完賽預測欄位 → (vdot_engine 距離類別, 顯示名稱)
_GARMIN_RACE_PREDICTIONS: tuple[tuple[str, str, str], ...] = (
    ("race_predict_5k_sec", "5k", "5K"),
    ("race_predict_10k_sec", "10k", "10K"),
    ("race_predict_half_sec", "half_marathon", "半馬"),
    ("race_predict_marathon_sec", "marathon", "全馬"),
)
_DISTANCE_CATEGORY_LABEL = {
    "3k": "3K",
    **{code: label for _, code, label in _GARMIN_RACE_PREDICTIONS},
}


def _iso_monday(day: datetime.date) -> datetime.date:
    """該日所在 ISO 週的週一。週跑量統計一律用 ISO 週（與前端 mondayOf() 同義）。"""
    return day - datetime.timedelta(days=day.weekday())


def _running_type_filter() -> tuple[str, list[str]]:
    """(SQL placeholder 字串, 參數) —— sorted() 讓參數順序穩定（來源是 frozenset）。"""
    running_types = sorted(RUNNING_ACTIVITY_TYPES)
    return ",".join("?" for _ in running_types), running_types


def _fetch_overview_runs(
    conn: sqlite3.Connection, athlete_id: int, start_date: str | None, end_date: str
) -> list[sqlite3.Row]:
    placeholders, running_types = _running_type_filter()
    sql = f"""
        SELECT id, date(started_at) AS activity_date, distance_km, duration_sec,
               avg_hr_bpm, raw_data_json, source_version
        FROM activities
        WHERE athlete_id = ?
          AND activity_type IN ({placeholders})
          AND date(started_at) <= ?
    """
    params: list[Any] = [athlete_id, *running_types, end_date]
    if start_date:
        sql += " AND date(started_at) >= ?"
        params.append(start_date)
    sql += " ORDER BY started_at"
    return conn.execute(sql, params).fetchall()


def _is_effective_session(row: sqlite3.Row) -> bool:
    return (
        (row["duration_sec"] or 0) >= EFFECTIVE_SESSION_MIN_DURATION_SEC
        and (row["distance_km"] or 0) >= EFFECTIVE_SESSION_MIN_DISTANCE_KM
        and row["avg_hr_bpm"] is not None
    )


def _zone_seconds(row: sqlite3.Row) -> dict[int, int] | None:
    """單場的 {zone: 秒數}（含 Z0）。單位換算一律交給 _build_hr_zones()，
    不在這裡重寫一次——匯出包是毫秒、API 同步是秒，見該函式說明。"""
    zones = _build_hr_zones(
        row["raw_data_json"], is_api=row["source_version"] == API_SYNC_SOURCE_VERSION
    )
    if not zones["available"]:
        return None
    seconds = {z["zone"]: z["seconds"] for z in zones["zones"]}
    seconds[HR_ZONE_BELOW_FIRST] = zones["below_zone_1"]["seconds"]
    return seconds


def _weekly_intensity(
    runs: list[tuple[sqlite3.Row, dict[int, int] | None]],
    start_date: datetime.date,
    end_date: datetime.date,
) -> list[dict]:
    """每個 ISO 週的跑量，依該場心率區間停留時間的比例拆成低／中／高強度。

    沒有心率區間的場次整場計入 unknown_km，不猜它的強度。range 內每一週
    都會列出（沒跑就是 0 km——那是真的沒跑，不是缺資料）；被 range 起訖日
    切到一半的週標 partial，前端據此提示該週數字不完整。
    """
    weeks: dict[datetime.date, dict] = {}
    week = _iso_monday(start_date)
    while week <= end_date:
        week_end = week + datetime.timedelta(days=6)
        weeks[week] = {
            "week_start": week.isoformat(),
            "partial": week < start_date or week_end > end_date,
            "low_km": 0.0, "mid_km": 0.0, "high_km": 0.0, "unknown_km": 0.0,
        }
        week += datetime.timedelta(days=7)

    for row, zone_secs in runs:
        distance = row["distance_km"] or 0.0
        bucket = weeks[_iso_monday(datetime.date.fromisoformat(row["activity_date"]))]
        total = sum(zone_secs.values()) if zone_secs else 0
        if total <= 0:
            bucket["unknown_km"] += distance
            continue
        for tier, zones in INTENSITY_TIER_ZONES.items():
            bucket[f"{tier}_km"] += distance * sum(zone_secs.get(z, 0) for z in zones) / total

    result = []
    for bucket in weeks.values():
        for key in ("low_km", "mid_km", "high_km", "unknown_km"):
            bucket[key] = round(bucket[key], 1)
        bucket["total_km"] = round(
            bucket["low_km"] + bucket["mid_km"] + bucket["high_km"] + bucket["unknown_km"], 1
        )
        result.append(bucket)
    return result


def _hr_zone_distribution(zone_rows: list[dict[int, int]]) -> dict:
    """range 內所有跑步的 Z1~Z5 停留時間加總與佔比。

    佔比分母**只有 Z1~Z5**、不含 Z0：API 同步來源沒有 Z0（見 _build_hr_zones
    的跨來源限制），分母含 Z0 會讓兩種來源混算時佔比語意不一致。
    """
    unavailable = {"available": False, "reason": "此區間內沒有含心率區間資料的跑步"}
    totals = {z: sum(r.get(z, 0) for r in zone_rows) for z in range(1, HR_ZONE_MAX + 1)}
    total = sum(totals.values())
    if total <= 0:
        return unavailable
    return {
        "available": True,
        "zones": [
            {"zone": z, "seconds": s, "pct": round(s / total * 100, 1)}
            for z, s in totals.items()
        ],
        "total_seconds": total,
        "sessions_counted": len(zone_rows),
    }


def _this_week_volume(
    conn: sqlite3.Connection, athlete_id: int, today: datetime.date
) -> dict:
    """本 ISO 週（週一～週日，錨定真實今天）已跑距離、課表距離、各心率區間時間。

    課表距離只加總有填 planned_distance_km 的課；只給時間（如「FR 60 分鐘」）
    的課無法換算距離，另以 planned_without_distance 計數讓前端註明，
    不拿配速去猜一個距離出來。

    zone_seconds 為本週跑步在 Z1~Z5 的停留秒數加總（手錶算好的 hrTimeInZone，
    單位換算同 _build_hr_zones）；沒有心率區間資料的場次不計入，
    zone_sessions_counted／sessions 讓前端看得出有沒有漏算。
    """
    week_start = _iso_monday(today)
    week_end = week_start + datetime.timedelta(days=6)
    rows = _fetch_overview_runs(conn, athlete_id, week_start.isoformat(), week_end.isoformat())
    zone_rows = [z for z in (_zone_seconds(row) for row in rows) if z]
    plan = conn.execute(
        """
        SELECT COALESCE(SUM(planned_distance_km), 0) AS km,
               SUM(CASE WHEN planned_distance_km IS NULL THEN 1 ELSE 0 END) AS no_distance,
               COUNT(*) AS sessions
        FROM training_plan
        WHERE athlete_id = ? AND is_active = 1 AND workout_type <> 'rest'
          AND planned_date BETWEEN ? AND ?
        """,
        (athlete_id, week_start.isoformat(), week_end.isoformat()),
    ).fetchone()
    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "done_km": round(sum(row["distance_km"] or 0 for row in rows), 1),
        # 本週沒有任何（非休息的）課表時為 None，與「課表 0 km」區分
        "planned_km": round(plan["km"], 1) if plan["sessions"] else None,
        "planned_without_distance": plan["no_distance"] or 0,
        "sessions": len(rows),
        "zone_sessions_counted": len(zone_rows),
        "zone_seconds": [
            {"zone": z, "seconds": sum(r.get(z, 0) for r in zone_rows)}
            for z in range(1, HR_ZONE_MAX + 1)
        ],
    }


# 「近期跑量」的滾動視窗天數（2026-10-14 Fu 指定看近 1 週）
RECENT_VOLUME_DAYS = 7


def _recent_volume(conn: sqlite3.Connection, athlete_id: int, today: datetime.date) -> dict:
    """近 RECENT_VOLUME_DAYS 天（含今天）跑量，與再往前同長度的一段對照。

    用滾動天數而非 ISO 週：「近 1 週」問的是最近 7 天，不該因為今天是
    週三就只算到上週日。兩段都只回傳數字，增減幅度的好壞不在這裡判斷。
    """
    placeholders, running_types = _running_type_filter()

    def km_between(start: datetime.date, end: datetime.date) -> float:
        return conn.execute(
            f"""
            SELECT COALESCE(SUM(distance_km), 0) AS km FROM activities
            WHERE athlete_id = ? AND activity_type IN ({placeholders})
              AND date(started_at) BETWEEN ? AND ?
            """,
            (athlete_id, *running_types, start.isoformat(), end.isoformat()),
        ).fetchone()["km"]

    start = today - datetime.timedelta(days=RECENT_VOLUME_DAYS - 1)
    prev_end = start - datetime.timedelta(days=1)
    prev_start = prev_end - datetime.timedelta(days=RECENT_VOLUME_DAYS - 1)
    return {
        "days": RECENT_VOLUME_DAYS,
        "start": start.isoformat(),
        "end": today.isoformat(),
        "km": round(km_between(start, today), 1),
        "previous_start": prev_start.isoformat(),
        "previous_end": prev_end.isoformat(),
        "previous_km": round(km_between(prev_start, prev_end), 1),
    }


# 有氧效率 EF（速度 ÷ 心率）的比較視窗：近 6 週對近 6 個月（含近 6 週），
# 取自 TrailRunCoach 總覽「有氧效率」的視窗設計。🔴 推估，未經本學員資料校正。
EF_RECENT_DAYS = 42
EF_BASELINE_DAYS = 182
# 視窗內場次少於此數就不比較——一兩場的天候、補水就能左右平均。🔴 推估。
EF_MIN_RECENT_RUNS = 3
EF_MIN_BASELINE_RUNS = 6
# 只取輕鬆跑：EF 比的是「同樣的有氧強度換到多少速度」，強度課混進來沒有意義。
_EF_WORKOUT_TYPES = ("easy", "lsd")
# 只取戶外與田徑場：跑步機的配速來自手腕加速規，誤差比 GPS 大得多。
_EF_ACTIVITY_TYPES = tuple(sorted(PACE_COMPARABLE_RUNNING_TYPES - {"treadmill_running"}))
# 太短的跑步整場平均被暖身段主導。🔴 推估。
EF_MIN_DURATION_SEC = 20 * 60


def _aerobic_efficiency(conn: sqlite3.Connection, athlete_id: int, today: datetime.date) -> dict:
    """輕鬆跑的有氧效率 EF = 平均速度（公尺/分，優先用坡度校正配速）÷ 平均心率。

    只用單場摘要，不需要逐秒資料，因此 API 同步的活動也能算。回傳每場的點
    （供走勢圖）與近 6 週對近 6 個月的平均變化；變化為正代表同樣心率跑得更快。
    場次不足時 change 為 None 並附原因。只呈現數字，不判斷好壞。
    """
    baseline_start = today - datetime.timedelta(days=EF_BASELINE_DAYS - 1)
    recent_start = today - datetime.timedelta(days=EF_RECENT_DAYS - 1)
    type_ph = ",".join("?" for _ in _EF_ACTIVITY_TYPES)
    wt_ph = ",".join("?" for _ in _EF_WORKOUT_TYPES)
    rows = conn.execute(
        f"""
        SELECT date(started_at) AS activity_date, avg_hr_bpm, avg_pace_sec_per_km,
               avg_grade_adj_pace_sec_per_km
        FROM activities
        WHERE athlete_id = ? AND activity_type IN ({type_ph}) AND workout_type IN ({wt_ph})
          AND avg_hr_bpm > 0 AND COALESCE(avg_grade_adj_pace_sec_per_km, avg_pace_sec_per_km) > 0
          AND duration_sec >= ? AND date(started_at) BETWEEN ? AND ?
        ORDER BY started_at
        """,
        (
            athlete_id,
            *_EF_ACTIVITY_TYPES,
            *_EF_WORKOUT_TYPES,
            EF_MIN_DURATION_SEC,
            baseline_start.isoformat(),
            today.isoformat(),
        ),
    ).fetchall()

    points = []
    for row in rows:
        gap = row["avg_grade_adj_pace_sec_per_km"]
        pace = gap or row["avg_pace_sec_per_km"]
        points.append(
            {
                "date": row["activity_date"],
                "ef": round(60000 / pace / row["avg_hr_bpm"], 3),
                "grade_adjusted": gap is not None,
            }
        )

    recent = [p["ef"] for p in points if p["date"] >= recent_start.isoformat()]
    baseline = [p["ef"] for p in points]
    result: dict[str, Any] = {
        "recent_days": EF_RECENT_DAYS,
        "baseline_days": EF_BASELINE_DAYS,
        "recent_runs": len(recent),
        "baseline_runs": len(baseline),
        "points": points,
        "change": None,
        "reason": None,
    }
    if len(recent) < EF_MIN_RECENT_RUNS or len(baseline) < EF_MIN_BASELINE_RUNS:
        result["reason"] = (
            f"輕鬆跑場次不足：近 {EF_RECENT_DAYS} 天 {len(recent)} 場（需 ≥{EF_MIN_RECENT_RUNS}）、"
            f"近 {EF_BASELINE_DAYS} 天 {len(baseline)} 場（需 ≥{EF_MIN_BASELINE_RUNS}）"
        )
        return result

    recent_mean = sum(recent) / len(recent)
    baseline_mean = sum(baseline) / len(baseline)
    bias_codes = ["ef_season_unadjusted"]
    if not all(p["grade_adjusted"] for p in points):
        bias_codes.append("ef_pace_not_grade_adjusted")
    result["change"] = data_confidence.fact(
        {
            "recent_ef": round(recent_mean, 3),
            "baseline_ef": round(baseline_mean, 3),
            "change_pct": round((recent_mean / baseline_mean - 1) * 100, 1),
        },
        source=(
            f"輕鬆跑／LSD 的平均速度（m/min，坡度校正配速優先）÷ 平均心率，"
            f"近 {EF_RECENT_DAYS} 天平均對近 {EF_BASELINE_DAYS} 天平均"
        ),
        bias_codes=bias_codes,
    )
    return result


# 手錶五區的顯示名稱（英文沿用 Garmin Connect 用語，Z2 依 Fu 指定為 Easy run），
# 中文為 Fu 指定譯名
HR_ZONE_NAMES: dict[int, tuple[str, str]] = {
    1: ("熱身", "Warm Up"),
    2: ("輕鬆", "Easy run"),
    3: ("有氧", "Aerobic"),
    4: ("閾值", "Threshold"),
    5: ("最大", "Maximum"),
}


def _hr_zone_bounds(conn: sqlite3.Connection, athlete_id: int) -> dict:
    """手錶五區換算成 bpm：靜止心率 + %HRR × (HRmax − 靜止心率)。

    %HRR 邊界取自 athlete_profile.hr_zone_hrr_bounds_json（手錶設定），
    未設定或格式不對就回 available:false——不套任何預設區間，否則顯示的
    bpm 會和手錶上看到的對不上。HRmax 為手錶顯示值時帶 low 信賴度。
    """
    row = conn.execute(
        """
        SELECT max_hr_bpm, max_hr_source, resting_hr_bpm, hr_zone_hrr_bounds_json
        FROM athlete_profile WHERE id = ?
        """,
        (athlete_id,),
    ).fetchone()
    names = [
        {"zone": z, "name": HR_ZONE_NAMES[z][0], "name_en": HR_ZONE_NAMES[z][1]}
        for z in range(1, HR_ZONE_MAX + 1)
    ]
    try:
        bounds = json.loads(row["hr_zone_hrr_bounds_json"]) if row else None
    except (TypeError, ValueError):
        bounds = None
    valid = (
        isinstance(bounds, list)
        and len(bounds) == HR_ZONE_MAX + 1
        and all(isinstance(b, (int, float)) for b in bounds)
        and all(a < b for a, b in zip(bounds, bounds[1:]))
        and bool(row["max_hr_bpm"]) and bool(row["resting_hr_bpm"])
    )
    if not valid:
        return {"available": False, "reason": "尚未設定手錶心率區間或心率參數", "zones": names}

    rest, reserve = row["resting_hr_bpm"], row["max_hr_bpm"] - row["resting_hr_bpm"]
    return {
        "available": True,
        "max_hr": data_confidence.fact(
            row["max_hr_bpm"],
            source=f"athlete_profile.max_hr_source={row['max_hr_source']}",
            bias_codes=data_confidence.hrmax_bias_codes(row["max_hr_source"]),
        ),
        "resting_hr_bpm": rest,
        "zones": [
            {
                **names[i],
                "hrr_low": bounds[i],
                "hrr_high": bounds[i + 1],
                "bpm_low": round(rest + bounds[i] * reserve),
                "bpm_high": round(rest + bounds[i + 1] * reserve),
            }
            for i in range(HR_ZONE_MAX)
        ],
    }


def _race_vdot(conn: sqlite3.Connection, athlete_id: int, today: datetime.date) -> dict:
    """只用標記為比賽（workout_type='race'）的全力成績推 VDOT。

    刻意**不傳 max_hr_bpm** 給 vdot_engine：沒有 HRmax，非全力場次就無法經
    心率換算成候選（engine 的既有行為），等於只剩比賽成績。心率換算是次最大
    強度推估，即使 2026-10-14 修正公式後仍系統性偏高（實測一場 146bpm 的輕鬆
    10K 推出 44.6，同期 3K 全力成績 39.8；修正前是 48.5），總覽不採用。
    """
    candidates = [
        c for c in training_plan_generator.get_vdot_candidate_activities(conn, athlete_id)
        if c["is_max_effort"] and c["date"] <= today
    ]
    result = vdot_engine.estimate_vdot_and_paces(
        candidates,
        reference_date=today,
        run_dates=training_plan_generator.get_run_dates(conn, athlete_id),
    )
    if not result["available"]:
        reason = (
            "資料庫中沒有標記為比賽的成績"
            if not candidates
            else "比賽成績皆超過新鮮度門檻（5K／10K 90 天、半馬／全馬 180 天）"
        )
        return {"value": None, "reason": reason}

    basis = result["source_candidate"]
    label = _DISTANCE_CATEGORY_LABEL.get(basis["distance_category"], basis["distance_category"])
    detraining = result["detraining"]
    source = (
        f"{basis['activity']['date'].isoformat()} {label} 全力成績（標記為比賽）→ "
        "Daniels VDOT 公式（實際距離與完賽時間）"
    )
    bias_codes = ["vdot_race_stale"] if basis["confidence"] == "low" else []
    if detraining["factor"] < 1:
        source += (
            f"；其後最長停跑 {detraining['longest_gap_days']} 天，依 Daniels 表 9.1 "
            f"×{detraining['factor']}（原 {detraining['unadjusted_vdot']:.1f}）"
        )
        bias_codes.append("vdot_detraining_adjusted")
    return {
        "value": data_confidence.fact(
            round(result["vdot"], 1), source=source, bias_codes=bias_codes
        ),
        # 供前端顯示「依據哪一場」，不必去解析 source 字串
        "basis": {"date": basis["activity"]["date"].isoformat(), "distance": label},
        "reason": None,
    }


def _garmin_vdot_reference(
    conn: sqlite3.Connection, athlete_id: int, today: datetime.date
) -> dict | None:
    """Garmin 完賽預測換算成的 VDOT，僅供對照。

    與 /api/today 的 HRV garmin_reference 同一個形狀與理由：garmin_blackbox
    是 unusable，fact() 會回 None；但使用者手錶上看得到這些預測，儀表板
    並列出來對照是合理的，只是明確標 for_display_only，判斷層不讀。
    """
    row = conn.execute(
        """
        SELECT date, race_predict_5k_sec, race_predict_10k_sec,
               race_predict_half_sec, race_predict_marathon_sec
        FROM performance_metrics
        WHERE athlete_id = ? AND date <= ?
        ORDER BY date DESC LIMIT 1
        """,
        (athlete_id, today.isoformat()),
    ).fetchone()
    if row is None:
        return None
    predictions = []
    for column, category, label in _GARMIN_RACE_PREDICTIONS:
        time_sec = row[column]
        if not time_sec:
            continue
        # 與比賽成績同一條公式（Daniels 原版、用 engine 的標準距離）
        vdot = vdot_engine.vdot_from_race(vdot_engine._DISTANCE_KM[category], time_sec)
        predictions.append({"distance": label, "time_sec": time_sec, "vdot": round(vdot, 1)})
    if not predictions:
        return None
    return {
        "for_display_only": True,
        "note": "Garmin（Firstbeat）黑盒演算法輸出，公式未公開，僅供對照，不得作為判讀依據",
        "as_of": row["date"],
        "predictions": predictions,
    }


# 由短到長的顯示順序
_PERSONAL_BEST_ORDER = ("3k", "5k", "10k", "half_marathon", "marathon")


def _race_records(
    conn: sqlite3.Connection, athlete_id: int, race_names: dict[str, str] | None = None
) -> list[dict]:
    """所有標記為比賽的跑步，依日期由舊到新，每筆附 race_result() 的判讀與 VDOT。

    最佳成績與 VDOT 走勢共用這份清單（同一套距離歸類、同一個成績、同一條公式）。
    距離對不上任何標準類別的比賽不列入。

    race_names：{日期: 賽事名稱}，來自學員檔（見 race_names.py）。該日沒有
    名稱時退回活動名稱；名稱含「測驗／測試」的標 personal_test。
    """
    race_names = race_names or {}
    placeholders, running_types = _running_type_filter()
    rows = conn.execute(
        f"""
        SELECT id, date(started_at) AS activity_date, title, distance_km,
               duration_sec, race_official_time_sec, race_official_distance_km
        FROM activities
        WHERE athlete_id = ? AND activity_type IN ({placeholders})
          AND workout_type = 'race'
        ORDER BY started_at
        """,
        (athlete_id, *running_types),
    ).fetchall()
    records = []
    for row in rows:
        result = training_plan_generator.race_result(row)
        if result is None:
            continue
        category = result["distance_category"]
        records.append(
            {
                "id": row["id"],
                "date": row["activity_date"],
                "title": row["title"],
                "name": race_names.get(row["activity_date"]) or row["title"],
                "personal_test": race_names_module.is_personal_test(
                    race_names.get(row["activity_date"])
                ),
                "distance": category,
                "label": _DISTANCE_CATEGORY_LABEL[category],
                "distance_km": _round(row["distance_km"], 2),
                "time_sec": result["time_sec"],
                "pace_sec_per_km": round(result["pace_sec_per_km"]),
                "official": result["official"],
                "vdot": round(
                    vdot_engine.vdot_from_race(
                        vdot_engine._DISTANCE_KM[category], result["time_sec"]
                    ),
                    1,
                ),
            }
        )
    return records


def _personal_bests(races: list[dict]) -> list[dict]:
    """標記為比賽（workout_type='race'）的跑步，依標準距離分組取最快幾場。

    回傳每個距離的**全部**比賽、由快到慢；畫面上預設只展開前幾名由前端決定。
    固定列出 _PERSONAL_BEST_ORDER 的每個距離，沒有比賽的距離 records 為空陣列
    （畫面保留該格、顯示「尚無紀錄」）。
    不吃 range：最佳成績是生涯紀錄。每場的距離類別／時間／配速一律交給
    training_plan_generator.race_result()——與 VDOT 候選同一份規則，兩邊對
    「這場算不算半馬、成績幾分」的判讀必須一致。`official` 標明時間是否為
    官方成績。
    """
    grouped: dict[str, list[dict]] = {}
    for race in races:
        grouped.setdefault(race["distance"], []).append(race)
    return [
        {
            "distance": category,
            "label": _DISTANCE_CATEGORY_LABEL[category],
            "records": sorted(grouped.get(category, []), key=lambda r: r["time_sec"]),
        }
        for category in _PERSONAL_BEST_ORDER
    ]


def _data_freshness(conn: sqlite3.Connection, athlete_id: int) -> dict:
    """最後更新時間：資料最後一次寫入（同步）的時刻，與最新一筆活動／身體數據。
    由 /api/today 的 freshness 帶出，顯示在每一頁都看得到的側邊列底部。

    last_synced_at 取各同步資料表 fetched_at 的最大值。⚠️ fetched_at 存的是
    **不帶時區的 UTC**（garmin_sync_runner 以 datetime.now(timezone.utc) 去掉
    tzinfo 寫入），這裡補上 "Z" 標成 UTC，由前端換算成當地時間顯示——
    直接當當地時間會差 8 小時。activity_latest_at 是活動的 started_at，
    本來就是當地時間，原樣回傳。
    """
    synced = conn.execute(
        """
        SELECT MAX(t) AS t FROM (
            SELECT MAX(fetched_at) AS t FROM activities WHERE athlete_id = ?
            UNION ALL SELECT MAX(fetched_at) FROM daily_wellness WHERE athlete_id = ?
            UNION ALL SELECT MAX(fetched_at) FROM training_status WHERE athlete_id = ?
            UNION ALL SELECT MAX(fetched_at) FROM performance_metrics WHERE athlete_id = ?
        )
        """,
        (athlete_id,) * 4,
    ).fetchone()["t"]
    activity = conn.execute(
        "SELECT MAX(started_at) AS t FROM activities WHERE athlete_id = ?", (athlete_id,)
    ).fetchone()["t"]
    wellness = conn.execute(
        "SELECT MAX(date) AS d FROM daily_wellness WHERE athlete_id = ?", (athlete_id,)
    ).fetchone()["d"]
    return {
        "last_synced_at": synced[:19] + "Z" if synced else None,
        "activity_latest_at": activity[:16] if activity else None,
        "wellness_latest": wellness,
    }


def get_overview(
    conn: sqlite3.Connection,
    athlete_id: int | None = None,
    range_key: str = DEFAULT_RANGE,
    today: datetime.date | None = None,
    race_names: dict[str, str] | None = None,
) -> dict:
    """總覽頁：區間摘要、資料品質、本週跑量與區間時間、近 1 週跑量、
    強度堆疊週跑量、心率區間分佈與 bpm 邊界、VDOT 與歷年走勢、最佳成績。

    ATL/CTL/TSB 不在這裡——前端直接用既有的 /api/training-load，不重算一份。

    兩種錨點並存（與其他端點的既有語意一致）：
    - range 系列（區間摘要、有效訓練、週跑量、心率區間）走 resolve_range()，錨定資料庫最新日
    - 本週、近 2 週與 VDOT 錨定真實今天（today 可覆寫供測試）
    """
    today = today or datetime.date.today()
    resolved = resolve_athlete_id(conn, athlete_id)
    window = resolve_range(conn, resolved, range_key, today)
    if resolved is None:
        return {**window, "available": False, "reason": "資料庫中沒有學員資料"}

    rows = _fetch_overview_runs(conn, resolved, window["start_date"], window["end_date"])
    runs = [(row, _zone_seconds(row)) for row in rows]
    races = _race_records(conn, resolved, race_names)

    end_date = datetime.date.fromisoformat(window["end_date"])
    if window["start_date"]:
        start_date = datetime.date.fromisoformat(window["start_date"])
    else:
        # range=all：從第一場跑步那週開始，而非無限往前
        start_date = datetime.date.fromisoformat(rows[0]["activity_date"]) if rows else end_date

    return {
        **window,
        "available": True,
        "sessions": {
            "total": len(rows),
            "total_km": round(sum(row["distance_km"] or 0 for row in rows), 1),
            "total_sec": sum(row["duration_sec"] or 0 for row in rows),
            "effective": sum(1 for row in rows if _is_effective_session(row)),
            "criteria": {
                "min_duration_sec": EFFECTIVE_SESSION_MIN_DURATION_SEC,
                "min_distance_km": EFFECTIVE_SESSION_MIN_DISTANCE_KM,
                "requires_hr": True,
            },
        },
        "this_week": _this_week_volume(conn, resolved, today),
        "recent_volume": _recent_volume(conn, resolved, today),
        "aerobic_efficiency": _aerobic_efficiency(conn, resolved, today),
        "weekly_intensity": {
            "tiers": {tier: list(zones) for tier, zones in INTENSITY_TIER_ZONES.items()},
            "weeks": _weekly_intensity(runs, start_date, end_date),
        },
        "hr_zones": _hr_zone_distribution([z for _, z in runs if z]),
        "hr_zone_bounds": _hr_zone_bounds(conn, resolved),
        "vdot": {
            "race": _race_vdot(conn, resolved, today),
            "garmin_reference": _garmin_vdot_reference(conn, resolved, today),
            # 歷年比賽的 VDOT（由舊到新），供走勢線；不受新鮮度門檻限制
            "history": [
                {"date": r["date"], "distance": r["label"], "vdot": r["vdot"]} for r in races
            ],
        },
        "personal_bests": _personal_bests(races),
    }
