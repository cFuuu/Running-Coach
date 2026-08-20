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


def _backfill_training_plan_is_active(conn: sqlite3.Connection) -> None:
    """既有資料庫補上 is_active 欄位後，把既有列（值為 NULL）回填為 1（生效中）。

    全新資料庫走 schema.sql 的 DEFAULT 1，不會有 NULL，此函式對其為 no-op。
    可重複執行：已回填過的列 is_active 不再是 NULL，不會被再次觸碰。
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(training_plan)")}
    if "is_active" not in existing:
        return  # 表還不存在，schema.sql 會負責建立
    conn.execute("UPDATE training_plan SET is_active = 1 WHERE is_active IS NULL")


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the SQLite database and ensure the schema is applied."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    _apply_column_migrations(conn)
    conn.commit()
    return conn
