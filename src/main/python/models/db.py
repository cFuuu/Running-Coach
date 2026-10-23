"""SQLite connection helper for the Running Coach local data store."""

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# 已存在的資料庫要補上的欄位。schema.sql 用 CREATE TABLE IF NOT EXISTS，
# 對已建立的表不會生效，因此新欄位必須另外用 ALTER TABLE 補，
# 否則既有使用者得砍掉整個資料庫重新匯入。
_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    # (資料表, 欄位, 欄位定義)
    ("activities", "workout_type", "TEXT"),
    ("activities", "workout_type_source", "TEXT"),
    ("daily_wellness", "all_day_stress_avg", "INTEGER"),
    # 2026-08-20：training_plan 版本歷史保留（見 schema.sql 註解）。
    # 注意：SQLite 的 ALTER TABLE ADD COLUMN 不支援加 CHECK/DEFAULT 於既有列的
    # 回填邏輯，is_active 需靠下方 _backfill_training_plan_is_active() 補上預設值 1，
    # 否則既有資料庫的舊資料 is_active 會是 NULL 而非 1。
    ("training_plan", "is_active", "INTEGER"),
    ("training_plan", "superseded_by", "INTEGER"),
    # 2026-08-20：個人化恢復閾值（Issue #16），NULL 代表尚未人工設定。
    ("athlete_profile", "high_risk_consecutive_training_days", "INTEGER"),
    # 2026-10-14：手錶心率區間的 %HRR 邊界（見 schema.sql 註解）
    ("athlete_profile", "hr_zone_hrr_bounds_json", "TEXT"),
    # --- 2026-09-02：Garmin 指標擴充（見 docs/dev/GARMIN_METRICS_AUDIT.md）---
    # 審計發現大量已抓到卻未落庫的指標。欄位語意與單位見 schema.sql 註解。
    # 活動：訓練效果、運動負荷、跑步動態、功率、環境、心率區間
    ("activities", "anaerobic_te", "REAL"),
    ("activities", "activity_training_load", "REAL"),
    ("activities", "training_effect_label", "TEXT"),
    ("activities", "avg_grade_adj_pace_sec_per_km", "INTEGER"),
    ("activities", "avg_ground_contact_time_ms", "REAL"),
    ("activities", "avg_vertical_oscillation_cm", "REAL"),
    ("activities", "avg_vertical_ratio_pct", "REAL"),
    ("activities", "avg_stride_length_cm", "REAL"),
    ("activities", "avg_power_watts", "REAL"),
    ("activities", "max_power_watts", "REAL"),
    ("activities", "norm_power_watts", "REAL"),
    ("activities", "min_temperature_c", "REAL"),
    ("activities", "max_temperature_c", "REAL"),
    ("activities", "hr_time_in_zone_json", "TEXT"),
    # 2026-10-14：比賽／測驗的官方成績（見 schema.sql 註解）
    ("activities", "race_official_time_sec", "INTEGER"),
    ("activities", "race_official_distance_km", "REAL"),
    # 每日身體數據：HRV 基準線、短期負荷、睡眠階段、readiness 快照時點
    ("daily_wellness", "hrv_baseline_low_upper", "REAL"),
    ("daily_wellness", "hrv_baseline_balanced_low", "REAL"),
    ("daily_wellness", "hrv_baseline_balanced_upper", "REAL"),
    ("daily_wellness", "hrv_status", "TEXT"),
    ("daily_wellness", "hrv_last_night_5min_high", "REAL"),
    ("daily_wellness", "acute_load", "REAL"),
    ("daily_wellness", "rhr_7day_avg_bpm", "INTEGER"),
    ("daily_wellness", "sleep_deep_sec", "INTEGER"),
    ("daily_wellness", "sleep_light_sec", "INTEGER"),
    ("daily_wellness", "sleep_rem_sec", "INTEGER"),
    ("daily_wellness", "sleep_awake_sec", "INTEGER"),
    ("daily_wellness", "body_battery_at_wake", "INTEGER"),
    ("daily_wellness", "readiness_input_context", "TEXT"),
    ("daily_wellness", "readiness_timestamp_local", "TEXT"),
    # --- 2026-10-22：Garmin 網頁卡片所需欄位（見 schema.sql 註解）---
    ("athlete_profile", "sex", "TEXT"),
    ("training_status", "status_since_date", "TEXT"),
    ("training_status", "monthly_load_aerobic_low_target_min", "REAL"),
    ("training_status", "monthly_load_aerobic_low_target_max", "REAL"),
    ("training_status", "monthly_load_aerobic_high_target_min", "REAL"),
    ("training_status", "monthly_load_aerobic_high_target_max", "REAL"),
    ("training_status", "monthly_load_anaerobic_target_min", "REAL"),
    ("training_status", "monthly_load_anaerobic_target_max", "REAL"),
    ("daily_wellness", "readiness_level", "TEXT"),
    ("daily_wellness", "readiness_feedback_short", "TEXT"),
    ("daily_wellness", "sleep_start_local", "TEXT"),
    ("daily_wellness", "sleep_end_local", "TEXT"),
    ("daily_wellness", "respiration_sleep_avg", "REAL"),
    ("daily_wellness", "respiration_low", "REAL"),
    ("daily_wellness", "respiration_high", "REAL"),
    ("daily_wellness", "spo2_lowest", "REAL"),
    ("daily_wellness", "spo2_latest", "REAL"),
    ("daily_wellness", "body_battery_latest", "INTEGER"),
    ("daily_wellness", "body_battery_charged", "INTEGER"),
    ("daily_wellness", "body_battery_drained", "INTEGER"),
    ("daily_wellness", "calories_active", "INTEGER"),
    ("daily_wellness", "calories_resting", "INTEGER"),
    ("daily_wellness", "intensity_minutes_moderate", "INTEGER"),
    ("daily_wellness", "intensity_minutes_vigorous", "INTEGER"),
    ("daily_wellness", "intensity_minutes_weekly_goal", "INTEGER"),
    ("daily_wellness", "floors_ascended", "REAL"),
    ("daily_wellness", "floors_goal", "INTEGER"),
    ("daily_wellness", "steps_goal", "INTEGER"),
    # 2026-10-23：暖身與主課的關聯（N-25，見 schema.sql 註解）
    ("activities", "warmup_for_activity_id", "INTEGER"),
    ("activities", "warmup_link_source", "TEXT"),
    # 2026-10-23：課種先看外部課表（N-28，見 schema.sql 註解）
    ("activities", "detected_workout_type", "TEXT"),
    ("activities", "workout_type_basis", "TEXT"),
]


def _apply_column_migrations(conn: sqlite3.Connection) -> None:
    """為既有資料庫補上後來才加入的欄位；可重複執行不報錯。

    注意：SQLite 的 ALTER TABLE ADD COLUMN 不支援加上 CHECK 條件，
    因此這裡加的欄位沒有 CHECK 約束，但全新建立的資料庫（走 schema.sql）有。
    寫入端不應依賴資料庫層的約束來驗證這兩個欄位的值。
    """
    for table, column, coltype in _COLUMN_MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # 表還不存在，schema.sql 會負責建立（已含該欄位）
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")

    _backfill_training_plan_is_active(conn)
    _migrate_training_plan_check_constraint(conn)
    _fix_lactate_pace_unit(conn)
    _migrate_daily_feel_three_levels(conn)


# PRAGMA user_version 記錄已套用的一次性資料修正。欄位 migration 可以靠
# PRAGMA table_info 判斷是否已做；改寫既有值的修正無法從資料本身看出做過
# 沒有（修正後的值也是合法的配速），所以需要版本號擋住重複執行。
_DATA_FIX_LACTATE_PACE_UNIT = 1


def _fix_lactate_pace_unit(conn: sqlite3.Connection) -> None:
    """2026-10-22：更正 lactate_threshold_pace_sec_per_km 的單位換算錯誤。

    舊版以 `speed × 1000` 換算（把 speed 當成秒/公尺），實際上 speed 是
    10 公尺/秒，應為 `100 / speed`（見 garmin_sync_runner.normalize_performance_metrics）。
    舊值 = speed × 1000 → speed = 舊值 / 1000 → 正確值 = 100000 / 舊值。
    COALESCE 寫入不會覆寫既有值，重新同步修不到舊列，只能在這裡改。
    """
    if conn.execute("PRAGMA user_version").fetchone()[0] >= _DATA_FIX_LACTATE_PACE_UNIT:
        return
    existing = {row[1] for row in conn.execute("PRAGMA table_info(performance_metrics)")}
    if "lactate_threshold_pace_sec_per_km" in existing:
        conn.execute(
            "UPDATE performance_metrics "
            "SET lactate_threshold_pace_sec_per_km = CAST(ROUND(100000.0 / lactate_threshold_pace_sec_per_km) AS INTEGER) "
            "WHERE lactate_threshold_pace_sec_per_km > 0"
        )
    conn.execute(f"PRAGMA user_version = {_DATA_FIX_LACTATE_PACE_UNIT}")


def _migrate_training_plan_check_constraint(conn: sqlite3.Connection) -> None:
    """把既有資料庫的 training_plan 表從舊版 plan_source CHECK
    （'ai_coach'/'running_club'）遷移到新版（'generated'/'external'）。

    2026-08-20 實測發現：ALTER TABLE ADD COLUMN 無法修改既有欄位的 CHECK
    約束，導致舊資料庫即使補上了 is_active/superseded_by 欄位，plan_source
    仍卡在舊 CHECK，連合法的新值 'generated' 都會被擋下（sqlite3.IntegrityError）。
    純加欄位無法解決，必須重建整張表：建暫存新表（schema 與 schema.sql 一致，
    含新 CHECK）→ 複製既有資料、把舊值對應成新值 → 刪舊表 → 新表改名。

    只在偵測到舊 CHECK 仍存在時才執行（用 sqlite_master.sql 是否包含
    'ai_coach' 判斷），可重複執行不報錯：新資料庫或已遷移過的資料庫，
    CHECK 已是新版，本函式直接 no-op。
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'training_plan'"
    ).fetchone()
    if row is None or row["sql"] is None or "ai_coach" not in row["sql"]:
        return  # 表不存在，或已是新版 CHECK，無需遷移

    conn.execute("ALTER TABLE training_plan RENAME TO training_plan_legacy_pre_generated_external")
    conn.execute(
        """
        CREATE TABLE training_plan (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            athlete_id                  INTEGER NOT NULL REFERENCES athlete_profile(id),
            planned_date                TEXT NOT NULL,
            workout_type                TEXT NOT NULL CHECK (workout_type IN ('easy', 'tempo', 'interval', 'lsd', 'race', 'rest', 'strength', 'cross_training')),
            planned_distance_km         REAL,
            planned_duration_sec        INTEGER,
            planned_pace_sec_per_km     INTEGER,
            notes                       TEXT,
            plan_source                 TEXT NOT NULL CHECK (plan_source IN ('generated', 'external')),
            linked_activity_id          INTEGER REFERENCES activities(id),
            is_active                   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
            superseded_by               INTEGER REFERENCES training_plan(id),
            created_at                  TEXT NOT NULL
        )
        """
    )
    # 舊資料的 plan_source 只有 'ai_coach'/'running_club' 兩種值，皆對應新語意
    # 的 'generated'（AI 教練與跑團課表在舊版都代表「非使用者手動輸入」的
    # 產生課表；新版的 'external' 專指本輪才新增的外部課表協調語意，舊資料
    # 沒有這個概念，故一律映射為 'generated'，不臆測哪些舊列其實是外部課表）。
    conn.execute(
        """
        INSERT INTO training_plan
            (id, athlete_id, planned_date, workout_type, planned_distance_km,
             planned_duration_sec, planned_pace_sec_per_km, notes, plan_source,
             linked_activity_id, is_active, superseded_by, created_at)
        SELECT id, athlete_id, planned_date, workout_type, planned_distance_km,
               planned_duration_sec, planned_pace_sec_per_km, notes, 'generated',
               linked_activity_id, COALESCE(is_active, 1), superseded_by, created_at
        FROM training_plan_legacy_pre_generated_external
        """
    )
    conn.execute("DROP TABLE training_plan_legacy_pre_generated_external")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_training_plan_athlete_date ON training_plan (athlete_id, planned_date)"
    )


def _migrate_daily_feel_three_levels(conn: sqlite3.Connection) -> None:
    """2026-10-23：daily_feel 由 4 級（very_tired/tired/normal/good）合併為
    3 級（tired/normal/energetic）。

    SQLite 不能改既有欄位的 CHECK，只能重建表：舊表改名 → 建新表（與 schema.sql
    一致）→ 複製並對應舊值（very_tired、tired→tired；good→energetic）→ 刪舊表。
    只在舊 CHECK 仍含 'very_tired' 時執行，可重複執行；合併後「很累／有點累」
    的區別不可回復，這是使用者 2026-10-23 拍板的取捨。
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'daily_feel'"
    ).fetchone()
    if row is None or row["sql"] is None or "very_tired" not in row["sql"]:
        return  # 表不存在，或已是 3 級

    conn.execute("ALTER TABLE daily_feel RENAME TO daily_feel_legacy_four_levels")
    conn.execute(
        """
        CREATE TABLE daily_feel (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            athlete_id      INTEGER NOT NULL REFERENCES athlete_profile(id),
            date            TEXT NOT NULL,
            feel            TEXT NOT NULL CHECK (feel IN ('tired', 'normal', 'energetic')),
            note            TEXT,
            source          TEXT NOT NULL CHECK (source IN ('dashboard', 'coach', 'backfill')),
            updated_at      TEXT NOT NULL,
            UNIQUE (athlete_id, date)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO daily_feel (id, athlete_id, date, feel, note, source, updated_at)
        SELECT id, athlete_id, date,
               CASE feel WHEN 'good' THEN 'energetic'
                         WHEN 'normal' THEN 'normal'
                         ELSE 'tired' END,
               note, source, updated_at
        FROM daily_feel_legacy_four_levels
        """
    )
    conn.execute("DROP TABLE daily_feel_legacy_four_levels")


def _backfill_training_plan_is_active(conn: sqlite3.Connection) -> None:
    """既有資料庫補上 is_active 欄位後，把既有列（值為 NULL）回填為 1（生效中）。

    全新資料庫走 schema.sql 的 DEFAULT 1，不會有 NULL，此函式對其為 no-op。
    可重複執行：已回填過的列 is_active 不再是 NULL，不會被再次觸碰。
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(training_plan)")}
    if "is_active" not in existing:
        return  # 表還不存在，schema.sql 會負責建立
    conn.execute("UPDATE training_plan SET is_active = 1 WHERE is_active IS NULL")


def get_connection(db_path: str | Path, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open (creating if needed) the SQLite database and ensure the schema is applied.

    check_same_thread=False 供跨執行緒但保證序列化存取的呼叫端使用（例如 MCP
    server：lifespan 開一次 connection，各 tool call 在 SDK 的另一個 thread
    執行，但同一時間只有一個呼叫在跑，符合 SQLite 官方文件對關閉此檢查的前提）。
    """
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    _apply_column_migrations(conn)
    conn.commit()
    return conn
