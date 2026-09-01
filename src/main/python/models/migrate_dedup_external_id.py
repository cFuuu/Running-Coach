"""一次性 migration：以 external_id 為活動去重鍵，清除跨來源重複列。

背景（2026-09-01 實測發現）
--------------------------
`activities` 原本的去重約束是
`UNIQUE (athlete_id, started_at, activity_type, distance_km)`，
但兩條匯入路徑存的距離精度不同：

    官方匯出包：distance_km = 10.1586        （四捨五入後）
    REST API  ：distance_km = 10.1585703125  （完整浮點數）

兩者不相等，UNIQUE 撞不到，**同一場活動被寫成兩列**。實測資料庫中有 2 組
（2026-08-13、2026-08-16），正落在 API 同步區間與匯出包的來源重疊區。
後果是 `list_running_sessions` 把同一場跑步列兩次，一次有 `workout_type`、
一次是 NULL，AI 教練看到的場次數量是錯的。

本 migration 做兩件事
--------------------
1. 合併重複列：**保留有 `workout_type` 的那列**（來自匯出包，已含分類，
   且可能已有分圈／逐秒資料），刪除另一列及其附帶的 laps/records。
   若兩列都有或都無分類，保留 id 較小者（較早匯入、被其他表參照的機率高）。
2. 建立 `UNIQUE INDEX idx_activities_external_id (athlete_id, external_id)`，
   讓往後跨來源重複在資料庫層就被擋下。

安全性
------
- 預設為 **dry-run**，只印出將要做什麼，不動資料。加 `--apply` 才真的執行。
- `--apply` 前會自動備份資料庫到同目錄的 `.bak-<timestamp>`。
- 整批包在單一 transaction，任何一步失敗即全部 rollback。
- 建立唯一索引前會先檢查是否仍有重複，有的話中止而非強制建立。

用法
----
    python -m src.main.python.models.migrate_dedup_external_id --db-path output/running_coach.db
    python -m src.main.python.models.migrate_dedup_external_id --db-path output/running_coach.db --apply
"""

from __future__ import annotations

import argparse
import datetime
import shutil
import sqlite3
from pathlib import Path

INDEX_NAME = "idx_activities_external_id"


def _connect(db_path: Path) -> sqlite3.Connection:
    """直接連線，**不**套用 `schema.sql`。

    ⚠️ 刻意不用 `models.db.get_connection()`：它會執行 schema.sql，而其中
    新增的 `idx_activities_external_id` 唯一索引在重複列尚未清除前建不起來
    （IntegrityError）——正是本 migration 要解決的問題。用它會形成雞生蛋。
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def find_duplicate_groups(conn: sqlite3.Connection) -> list[dict]:
    """找出同一 (athlete_id, external_id) 有多列的活動，並決定各組保留哪一列。"""
    groups = []
    rows = conn.execute(
        """
        SELECT athlete_id, external_id, COUNT(*) AS n
        FROM activities
        WHERE external_id IS NOT NULL
        GROUP BY athlete_id, external_id
        HAVING n > 1
        ORDER BY external_id
        """
    ).fetchall()

    for row in rows:
        members = conn.execute(
            """
            SELECT id, started_at, distance_km, workout_type, workout_type_source,
                   COALESCE(source_version, 'export') AS sv
            FROM activities
            WHERE athlete_id = ? AND external_id = ?
            ORDER BY id
            """,
            (row["athlete_id"], row["external_id"]),
        ).fetchall()

        # 保留有分類的那列（匯出包來源，資料較完整）；都有或都無則取 id 最小者。
        keep = next((m for m in members if m["workout_type"]), None) or members[0]
        drop = [m for m in members if m["id"] != keep["id"]]

        groups.append(
            {
                "athlete_id": row["athlete_id"],
                "external_id": row["external_id"],
                "keep": keep,
                "drop": drop,
            }
        )
    return groups


def count_children(conn: sqlite3.Connection, activity_id: int) -> tuple[int, int]:
    """回傳該活動附帶的 (分圈數, 逐秒紀錄數)。"""
    laps = conn.execute(
        "SELECT COUNT(*) FROM activity_laps WHERE activity_id = ?", (activity_id,)
    ).fetchone()[0]
    records = conn.execute(
        "SELECT COUNT(*) FROM activity_records WHERE activity_id = ?", (activity_id,)
    ).fetchone()[0]
    return laps, records


def _print_plan(conn: sqlite3.Connection, groups: list[dict]) -> None:
    if not groups:
        print("[i] 沒有發現重複列。")
        return
    print(f"[i] 發現 {len(groups)} 組重複：")
    for g in groups:
        k = g["keep"]
        print(f"\n  external_id={g['external_id']}  ({k['started_at']})")
        print(
            f"    保留 id={k['id']}  distance={k['distance_km']}  "
            f"workout_type={k['workout_type']}  來源={k['sv']}"
        )
        for d in g["drop"]:
            laps, records = count_children(conn, d["id"])
            print(
                f"    刪除 id={d['id']}  distance={d['distance_km']}  "
                f"workout_type={d['workout_type']}  來源={d['sv']}"
                f"  （附帶 laps={laps}、records={records} 一併刪除）"
            )


def _backup(db_path: Path) -> Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = db_path.with_suffix(db_path.suffix + f".bak-{stamp}")
    shutil.copy2(db_path, dest)
    return dest


def migrate(conn: sqlite3.Connection, apply: bool) -> int:
    groups = find_duplicate_groups(conn)
    _print_plan(conn, groups)

    has_index = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (INDEX_NAME,)
    ).fetchone()
    print(f"\n[i] 唯一索引 {INDEX_NAME}：{'已存在' if has_index else '尚未建立'}")

    if not apply:
        print("\n[i] 這是 dry-run，未做任何變更。確認上列計畫無誤後加 --apply 執行。")
        return 0

    try:
        conn.execute("BEGIN")
        deleted = 0
        for g in groups:
            for d in g["drop"]:
                conn.execute(
                    "DELETE FROM activity_laps WHERE activity_id = ?", (d["id"],)
                )
                conn.execute(
                    "DELETE FROM activity_records WHERE activity_id = ?", (d["id"],)
                )
                conn.execute("DELETE FROM activities WHERE id = ?", (d["id"],))
                deleted += 1

        # 建索引前再確認一次沒有殘留重複，避免強制建立失敗留下半套狀態。
        remaining = find_duplicate_groups(conn)
        if remaining:
            conn.execute("ROLLBACK")
            print(f"[!] 仍有 {len(remaining)} 組重複未解決，已 rollback，未做任何變更。")
            return 1

        if not has_index:
            conn.execute(
                f"CREATE UNIQUE INDEX {INDEX_NAME} ON activities (athlete_id, external_id)"
            )
        conn.execute("COMMIT")
    except Exception as e:
        conn.execute("ROLLBACK")
        print(f"[!] 執行失敗，已 rollback：{type(e).__name__}: {e}")
        return 1

    print(f"\n[OK] 已刪除 {deleted} 筆重複列，唯一索引 {INDEX_NAME} 已就緒。")
    print(f"     活動總數：{conn.execute('SELECT COUNT(*) FROM activities').fetchone()[0]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db-path", required=True, help="SQLite 資料庫路徑")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="真的執行（不加則只顯示計畫）。執行前會自動備份資料庫",
    )
    args = ap.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"[!] 找不到資料庫檔案：{db_path}")
        return 1

    if args.apply:
        backup = _backup(db_path)
        print(f"[i] 已備份至 {backup}\n")

    conn = _connect(db_path)
    try:
        return migrate(conn, apply=args.apply)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
