"""找出「另存成獨立活動的暖身」並關聯到主課（N-25）。

學員的品質課習慣先跑約 10 分鐘暖身、停錶，再另開一筆主課，同日形成兩筆跑步。
沒有關聯時，暖身那筆是一場來路不明的 `unknown`：場次清單多一筆、AI 教練看到的
場次數偏多，且 1–2 km 的暖身會被算進分類器的「近期平均距離／配速」，間接放寬
LSD 與 tempo 的判定。負荷與週跑量**不受影響**（兩筆本來就分別加總，這是對的）。

分層比照 `workout_classifier.py`／`fit_import_runner.py`：`detect_warmup_links()`
為純函式、不碰資料庫；`link_warmups()` 負責讀寫，FIT 匯入與 Garmin 同步完成後各
呼叫一次，也可用 CLI 回填歷史資料：

    python -m src.main.python.services.warmup_links --db-path output/running_coach.db [--apply]
"""

from __future__ import annotations

import argparse
import datetime
import sqlite3

from src.main.python.models.db import get_connection
from src.main.python.services.activity_types import RUNNING_ACTIVITY_TYPES

# --- 可調閾值（集中管理）---
# 🔴 以下皆為工程決策、無文獻出處，依 2026-10-23 對真實資料的分布訂定
# （2022-12 起 40 組「同日、前一筆 ≤15 分鐘」的兩筆跑步）。

# 暖身時長範圍（秒）。實際暖身 35 組落在 6–14 分鐘（中位約 9.7 分鐘）。
# 下限排除另外 5 組約 2 分鐘、0.4km 的短段——那些配速比主課還快，
# 較像第一趟反覆或加速跑被另存，不是暖身（也不會被當成主課，見 _find_main）。
WARMUP_MIN_DURATION_SEC = 5 * 60
WARMUP_MAX_DURATION_SEC = 15 * 60

# 暖身開始到主課開始的最長間隔（秒）。品質課多在 20–40 分鐘，
# 但賽事日要走到起跑區等槍響，實測 56–65 分鐘；另有兩場 tempo 為 61、67 分鐘。
# 「主課前 30 分鐘內」只涵蓋 20/40 組，故放寬到 70 分鐘。
MAX_START_TO_START_SEC = 70 * 60


def _parse(started_at: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(started_at)


def detect_warmup_links(activities: list[dict]) -> dict[int, int]:
    """回傳 {暖身 id: 主課 id}。

    `activities` 每筆需含 `id`、`started_at`（ISO 當地時間）、`duration_sec`、
    `avg_pace_sec_per_km`，且皆為跑步類活動。條件：
      1. 本筆時長在 WARMUP_MIN～MAX_DURATION_SEC 之間；
      2. 主課 = 同日、開始時間在 MAX_START_TO_START_SEC 內、**比本筆長**的
         第一筆跑步。中間夾著的短段（例：約 2 分鐘被另存的第一趟反覆）跳過，
         不會被誤當成主課（實例 2025-09-04：暖身 → 2 分鐘短段 → 主課）。
    已被當成主課的那筆不會再被當成別人的暖身。

    ⚠️ **刻意不比配速**：原計畫要求「暖身慢於主課」以排除快的短段，但間歇日
    主課的整場平均配速含組間休息（常是站著或走路），反而比暖身慢——實測 11 組
    因此漏配（例 2025-10-30：暖身 5:48/km、間歇主課整場 6:26/km）。快的短段
    都只有約 2 分鐘，已由時長下限排除。
    """
    ordered = sorted(
        (a for a in activities if a.get("started_at")), key=lambda a: a["started_at"]
    )
    links: dict[int, int] = {}
    mains: set[int] = set()
    for i, warmup in enumerate(ordered):
        duration = warmup.get("duration_sec") or 0
        if warmup["id"] in mains:
            continue
        if not WARMUP_MIN_DURATION_SEC <= duration <= WARMUP_MAX_DURATION_SEC:
            continue
        main = _find_main(warmup, ordered[i + 1:])
        if main is None:
            continue
        links[warmup["id"]] = main["id"]
        mains.add(main["id"])
    return links


def _find_main(warmup: dict, later: list[dict]) -> dict | None:
    """同日、時間範圍內、比暖身長的第一筆跑步。"""
    start = _parse(warmup["started_at"])
    for candidate in later:
        if candidate["started_at"][:10] != warmup["started_at"][:10]:
            return None
        if (_parse(candidate["started_at"]) - start).total_seconds() > MAX_START_TO_START_SEC:
            return None
        if (candidate.get("duration_sec") or 0) > (warmup.get("duration_sec") or 0):
            return candidate
    return None


def link_warmups(conn: sqlite3.Connection, athlete_id: int, apply: bool = True) -> list[dict]:
    """對該學員全部跑步重跑偵測，更新自動關聯；回傳有變動的暖身。

    冪等：每次以偵測結果覆寫 `warmup_link_source='auto'`（或尚未設定）的列；
    `manual` 的列一律不動，也不會被當成別人的暖身或主課重新配對。
    `apply=False` 只回傳差異、不寫入。
    """
    types = sorted(RUNNING_ACTIVITY_TYPES)
    placeholders = ",".join("?" for _ in types)
    rows = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT id, started_at, duration_sec, distance_km, avg_pace_sec_per_km,
                   warmup_for_activity_id, warmup_link_source
            FROM activities
            WHERE athlete_id = ? AND activity_type IN ({placeholders})
            """,
            (athlete_id, *types),
        )
    ]
    manual_ids = {r["id"] for r in rows if r["warmup_link_source"] == "manual"}
    manual_mains = {r["warmup_for_activity_id"] for r in rows if r["id"] in manual_ids}
    detected = detect_warmup_links(
        [r for r in rows if r["id"] not in manual_ids and r["id"] not in manual_mains]
    )

    changes = []
    for row in rows:
        if row["id"] in manual_ids:
            continue
        new_main = detected.get(row["id"])
        if new_main == row["warmup_for_activity_id"]:
            continue
        changes.append({
            "activity_id": row["id"],
            "date": row["started_at"][:10],
            "duration_min": round((row["duration_sec"] or 0) / 60, 1),
            "distance_km": round(row["distance_km"] or 0, 2),
            "old_main": row["warmup_for_activity_id"],
            "new_main": new_main,
        })
        if apply:
            conn.execute(
                "UPDATE activities SET warmup_for_activity_id = ?, warmup_link_source = ? "
                "WHERE id = ?",
                (new_main, "auto" if new_main else None, row["id"]),
            )
    return changes


def main() -> None:
    ap = argparse.ArgumentParser(description="找出另存成獨立活動的暖身並關聯到主課（N-25）。")
    ap.add_argument("--db-path", required=True, help="SQLite 資料庫路徑")
    ap.add_argument("--athlete-id", type=int, default=None, help="學員 ID（預設第一位）")
    ap.add_argument("--apply", action="store_true", help="實際寫入；未加時只列出差異")
    args = ap.parse_args()

    conn = get_connection(args.db_path)
    try:
        athlete_id = args.athlete_id or conn.execute(
            "SELECT id FROM athlete_profile ORDER BY id LIMIT 1"
        ).fetchone()["id"]
        changes = link_warmups(conn, athlete_id, apply=args.apply)
        if args.apply:
            conn.commit()
    finally:
        conn.close()
    for c in changes:
        print(f"  {c['date']}  #{c['activity_id']}  {c['duration_min']:>4} 分  "
              f"{c['distance_km']:>4} km  主課 {c['old_main']} → {c['new_main']}")
    action = "已寫入" if args.apply else "未寫入（加 --apply 才會寫入）"
    print(f"\n共 {len(changes)} 筆暖身關聯變動，{action}")


if __name__ == "__main__":
    main()
