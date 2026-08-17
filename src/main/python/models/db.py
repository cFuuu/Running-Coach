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
