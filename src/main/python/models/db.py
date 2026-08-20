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
]


def _apply_column_migrations(conn: sqlite3.Connection) -> None:
    """為既有資料庫補上後來才加入的欄位；可重複執行不報錯。

    注意：SQLite 的 ALTER TABLE ADD COLUMN 不支援加上 CHECK 條件，
    因此這裡加的欄位沒有 CHECK 約束，但全新建立的資料庫（走 schema.sql）有。
    寫入端不應依賴資料庫層的約束來驗證這兩個欄位的值。

    同理，既有資料庫中 training_plan.plan_source 欄位原本的 CHECK
    （'ai_coach'/'running_club'）不會被本函式更新為新的合法值集合
    （'generated'/'external'）——ALTER TABLE 無法修改既有欄位的 CHECK 約束。
    既有資料庫上寫入新值時，應用層需自行確保只寫入新的合法值，不依賴
    資料庫層擋下舊值。
    """
    for table, column, coltype in _COLUMN_MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # 表還不存在，schema.sql 會負責建立（已含該欄位）
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")

    _backfill_training_plan_is_active(conn)


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
