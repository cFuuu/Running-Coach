"""把 Garmin 匯出包裡的 FIT 檔解析後寫入本地 SQLite（分圈、逐秒降頻、訓練類型）。

用法：
    python -m src.main.python.services.fit_import_runner \
        --input-dir input/garmin_export --db-path output/running_coach.db

要匯入的運動類型不是寫死的——預設只有 running，但可用 --sports 擴充
（例如 --sports running,cycling），之後要支援其他運動類型不需改這支程式；
--classifiable-sports 則控制哪些運動要套用配速結構分類，見 fit_parser.py 開頭說明。

⚠️ FIT 檔名的編號無法對應到資料庫（2026-08-17 實測結論）
    FIT 檔名：<email>_152627722646.fit → 12 位數的 upload ID
    DB external_id：23997807612        → 11 位數的 activity ID
    實測用 external_id 比對 266 場跑步：0 筆對得上。
因此改用 FIT session 的起始時間（UTC）＋總距離來比對既有活動，
時區偏移由資料自行推導，不預設任何地區（多使用者可用性要求）。
"""

from __future__ import annotations

import argparse
import io
import re
import statistics
import sqlite3
import time
import zipfile
from pathlib import Path

from src.main.python.models.db import get_connection
from src.main.python.services.activity_types import PACE_COMPARABLE_RUNNING_TYPES
from src.main.python.services import fit_parser
from src.main.python.services.warmup_links import link_warmups
from src.main.python.services.workout_type_resolution import (
    plan_type_for_activity,
    resolve_workout_type,
)
from src.main.python.services.workout_classifier import classify_workout

# 預設匯入的 FIT sport 值。這是「目前預設只做跑步」，不是「只能做跑步」——
# 兩處都可透過參數／CLI 覆寫，之後要匯入 cycling、swimming 等其他運動類型
# 只需傳入不同的集合，不需要改這支程式的邏輯。
DEFAULT_IMPORT_SPORTS = frozenset({"running"})

# 可套用「配速結構」分類（interval/lsd/tempo/easy）的運動類型。
# 與 DEFAULT_IMPORT_SPORTS 分開設計是因為：未來若匯入 cycling 等資料，
# 逐圈/逐秒仍值得存，但用跑步配速的門檻去分類騎車會產生誤導的標籤，
# 應該讓分類邏輯留白（unknown）而非硬套。之後每種運動要自己的分類器時，
# 在這裡擴充對應規則即可，不用動 import_fit_files 本體。
DEFAULT_CLASSIFIABLE_SPORTS = frozenset({"running"})

# 比對容忍值：FIT 的 UTC 時間與資料庫的當地時間最多差這麼多小時。
# 涵蓋全球時區（UTC-12 ~ UTC+14）。
MAX_TZ_OFFSET_HOURS = 14

# 距離差在此比例內視為同一場活動
DISTANCE_TOLERANCE_RATIO = 0.01

# 預設逐秒降頻間隔（秒）
DEFAULT_SAMPLE_EVERY_SEC = 10

# 計算「近期平均」時回看的活動筆數，供訓練類型判定做相對比較
RECENT_WINDOW = 20

# 計算「近期平均距離/配速」基準時，視為同一類活動的 DB activity_type 集合。
# 這是分類邏輯的參數，不是匯入邏輯的一部分——之後若對其他運動套用分類，
# 呼叫端應傳入對應的集合，而不是在這裡加更多寫死的運動類型。
# 定義見 activity_types.PACE_COMPARABLE_RUNNING_TYPES（刻意不含 trail_running）。
DEFAULT_COMPARISON_ACTIVITY_TYPES = PACE_COMPARABLE_RUNNING_TYPES


def find_uploaded_file_zips(base_dir: Path) -> list[Path]:
    """找出匯出包裡放 FIT 的 zip。目錄名含隨機 UUID，故用 glob 搜尋不寫死路徑。"""
    return sorted(base_dir.glob("**/DI-Connect-Uploaded-Files/*.zip"))


def match_activity(
    conn: sqlite3.Connection, start_time_utc, total_distance_m: float | None
) -> int | None:
    """用起始時間（UTC）與總距離找出對應的 activities.id，找不到回 None。

    資料庫存的是當地時間，FIT 存 UTC，兩者相差一個未知的時區偏移，
    因此以「時間差在合理時區範圍內、且距離幾乎相同」為條件，取距離最接近者。
    """
    if start_time_utc is None or not total_distance_m:
        return None

    distance_km = total_distance_m / 1000
    tolerance = max(distance_km * DISTANCE_TOLERANCE_RATIO, 0.01)
    iso_utc = start_time_utc.strftime("%Y-%m-%dT%H:%M:%S")

    rows = conn.execute(
        """
        SELECT id, distance_km,
               ABS(julianday(started_at) - julianday(?)) * 24 AS hours_diff
        FROM activities
        WHERE ABS(julianday(started_at) - julianday(?)) * 24 <= ?
          AND ABS(distance_km - ?) <= ?
        ORDER BY ABS(distance_km - ?) ASC, hours_diff ASC
        LIMIT 1
        """,
        (iso_utc, iso_utc, MAX_TZ_OFFSET_HOURS, distance_km, tolerance, distance_km),
    ).fetchall()
    return rows[0]["id"] if rows else None


def _recent_averages(
    conn: sqlite3.Connection,
    activity_id: int,
    comparison_activity_types: frozenset[str] = DEFAULT_COMPARISON_ACTIVITY_TYPES,
) -> tuple[float | None, int | None]:
    """取這場活動之前的近期平均距離與配速，供訓練類型做相對判斷。

    已關聯為暖身的活動不計入（N-25）：1–2 km 的慢速暖身會拉低平均距離、
    拉慢平均配速，間接讓 LSD 與 tempo 更容易被觸發。
    """
    placeholders = ",".join("?" for _ in comparison_activity_types)
    row = conn.execute(
        f"""
        SELECT AVG(distance_km) AS avg_km, AVG(avg_pace_sec_per_km) AS avg_pace
        FROM (
            SELECT distance_km, avg_pace_sec_per_km
            FROM activities
            WHERE athlete_id = (SELECT athlete_id FROM activities WHERE id = ?)
              AND activity_type IN ({placeholders})
              AND started_at < (SELECT started_at FROM activities WHERE id = ?)
              AND distance_km > 0
              AND warmup_for_activity_id IS NULL
            ORDER BY started_at DESC
            LIMIT ?
        )
        """,
        (activity_id, *comparison_activity_types, activity_id, RECENT_WINDOW),
    ).fetchone()
    if not row or row["avg_km"] is None:
        return None, None
    avg_pace = round(row["avg_pace"]) if row["avg_pace"] is not None else None
    return row["avg_km"], avg_pace


def store_laps(conn: sqlite3.Connection, activity_id: int, laps: list[dict]) -> int:
    """寫入分圈（先清空該活動既有分圈，故可安全重複執行）。

    ⚠️ 公開函式：`garmin_sync_runner` 亦呼叫本函式寫入 API 抓回的分圈，
    兩條匯入路徑共用同一份寫入邏輯（勿複製貼上另一份）。
    """
    conn.execute("DELETE FROM activity_laps WHERE activity_id = ?", (activity_id,))
    for lap in laps:
        conn.execute(
            """
            INSERT INTO activity_laps
                (activity_id, lap_index, distance_km, duration_sec, pace_sec_per_km, avg_hr_bpm, max_hr_bpm)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                activity_id,
                lap["lap_index"],
                lap["distance_km"],
                lap["duration_sec"],
                lap["pace_sec_per_km"],
                lap["avg_hr_bpm"],
                lap["max_hr_bpm"],
            ),
        )
    return len(laps)


def store_records(conn: sqlite3.Connection, activity_id: int, records: list[dict]) -> int:
    """覆寫單場的逐點紀錄。FIT 匯入與 API 同步（N-32）共用。"""
    conn.execute("DELETE FROM activity_records WHERE activity_id = ?", (activity_id,))
    for rec in records:
        conn.execute(
            """
            INSERT INTO activity_records
                (activity_id, elapsed_sec, distance_km, hr_bpm, pace_sec_per_km, cadence_spm,
                 altitude_m, lat, lon)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                activity_id,
                rec["elapsed_sec"],
                rec["distance_km"],
                rec["hr_bpm"],
                rec["pace_sec_per_km"],
                rec["cadence_spm"],
                rec["altitude_m"],
                rec.get("lat"),
                rec.get("lon"),
            ),
        )
    return len(records)


def update_workout_type(conn: sqlite3.Connection, activity_id: int, laps: list[dict]) -> str:
    """推測訓練類型並寫回，但不覆蓋使用者手動設定的值。

    呼叫端須先確認該活動的運動類型在 classifiable_sports 內才呼叫本函式——
    這裡不重複判斷，避免把「是否該分類」這個決定散落在兩個地方。

    ⚠️ 公開函式：`garmin_sync_runner` 亦呼叫本函式對 API 抓回的分圈分類，
    確保兩條匯入路徑用**同一套分類標準**（否則同一欄位會混入兩種語意）。
    """
    current = conn.execute(
        "SELECT distance_km, workout_type_source FROM activities WHERE id = ?", (activity_id,)
    ).fetchone()
    detected = _classify_activity(conn, activity_id, laps, current)
    if current and current["workout_type_source"] == "manual":
        # 手動標註的生效課種不動，但偵測結果照存，供事後比對
        conn.execute(
            "UPDATE activities SET detected_workout_type = ? WHERE id = ?", (detected, activity_id)
        )
        return "manual-kept"

    # N-28：生效課種先看當日外部課表，沒有才用分圈判斷；偵測結果另存
    workout_type, basis = resolve_workout_type(detected, plan_type_for_activity(conn, activity_id))
    conn.execute(
        "UPDATE activities SET workout_type = ?, workout_type_source = 'auto', "
        "workout_type_basis = ?, detected_workout_type = ? WHERE id = ?",
        (workout_type, basis, detected, activity_id),
    )
    return workout_type


def _classify_activity(
    conn: sqlite3.Connection, activity_id: int, laps: list[dict], current: sqlite3.Row | None
) -> str:
    """以近期平均為基準跑分類器，不寫入。"""
    avg_km, avg_pace = _recent_averages(conn, activity_id)
    return classify_workout(
        laps,
        total_distance_km=current["distance_km"] if current else None,
        recent_avg_distance_km=avg_km,
        recent_avg_pace_sec_per_km=avg_pace,
    )


def reclassify_stored_activities(
    conn: sqlite3.Connection,
    apply: bool = False,
    comparison_activity_types: frozenset[str] = DEFAULT_COMPARISON_ACTIVITY_TYPES,
) -> list[dict]:
    """重跑自動分類並套用生效順序，回傳課種有變動的場次（N-24／N-28）。

    有分圈的活動用資料庫已存的分圈重跑分類器（不需重新解析 FIT 或重打 API），
    再依當日外部課表決定生效課種（見 workout_type_resolution）。沒有分圈的活動
    沿用已存的偵測結果，只有在當日有外部課表時才會改變。
    `workout_type_source='manual'` 一律不動。`apply=False`（預設）只回傳
    差異清單、不寫入——改版後應先把清單給使用者確認再寫入。
    寫入時一併補上每場的 `detected_workout_type` 與 `workout_type_basis`（這兩欄
    不算課種變動，不出現在差異清單）。
    """
    placeholders = ",".join("?" for _ in comparison_activity_types)
    activities = conn.execute(
        f"""
        SELECT id, date(started_at) AS date, distance_km, workout_type,
               workout_type_basis, detected_workout_type
        FROM activities
        WHERE activity_type IN ({placeholders})
          AND COALESCE(workout_type_source, 'auto') != 'manual'
        ORDER BY started_at
        """,
        tuple(comparison_activity_types),
    ).fetchall()

    changes = []
    for activity in activities:
        laps = [
            dict(row)
            for row in conn.execute(
                "SELECT lap_index, distance_km, duration_sec, pace_sec_per_km, avg_hr_bpm, max_hr_bpm "
                "FROM activity_laps WHERE activity_id = ? ORDER BY lap_index",
                (activity["id"],),
            )
        ]
        if laps:
            detected = _classify_activity(conn, activity["id"], laps, activity)
        else:
            # 沒有分圈：沿用已存的偵測結果；舊資料沒有則把沿用分圈判斷的 workout_type 當作偵測結果
            detected = activity["detected_workout_type"]
            if detected is None and activity["workout_type_basis"] is None:
                detected = activity["workout_type"]
        new_type, basis = resolve_workout_type(
            detected, plan_type_for_activity(conn, activity["id"])
        )
        if new_type is None:
            continue
        if new_type != activity["workout_type"]:
            changes.append({
                "activity_id": activity["id"],
                "date": activity["date"],
                "distance_km": round(activity["distance_km"], 2),
                "old": activity["workout_type"],
                "new": new_type,
                "basis": basis,
                "detected": detected,
            })
        if apply:
            conn.execute(
                "UPDATE activities SET workout_type = ?, workout_type_source = 'auto', "
                "workout_type_basis = ?, detected_workout_type = ? WHERE id = ?",
                (new_type, basis, detected, activity["id"]),
            )
    if apply:
        conn.commit()
    return changes


def import_fit_files(
    conn: sqlite3.Connection,
    base_dir: Path,
    sample_every_sec: int = DEFAULT_SAMPLE_EVERY_SEC,
    min_file_bytes: int = fit_parser.DEFAULT_MIN_FILE_BYTES,
    import_sports: frozenset[str] | None = None,
    classifiable_sports: frozenset[str] | None = None,
    progress_every: int = 200,
) -> dict:
    """掃描匯出包中的 FIT 檔，解析符合 import_sports 的活動並寫入資料庫。回傳統計摘要。

    import_sports：要匯入分圈/逐秒資料的 FIT sport 值集合，預設只有跑步；
    classifiable_sports：要套用配速結構分類（interval/lsd/tempo/easy）的子集合，
    通常應是 import_sports 的子集——分圈照樣存，只是不分類，避免用跑步的判斷
    邏輯誤判其他運動。
    """
    import_sports = import_sports if import_sports is not None else DEFAULT_IMPORT_SPORTS
    classifiable_sports = (
        classifiable_sports if classifiable_sports is not None else DEFAULT_CLASSIFIABLE_SPORTS
    )

    zips = find_uploaded_file_zips(base_dir)
    if not zips:
        raise FileNotFoundError(f"在 {base_dir} 下找不到 DI-Connect-Uploaded-Files/*.zip")

    stats = {
        "scanned": 0,
        "skipped_small": 0,
        "no_session": 0,
        "sport_excluded": 0,
        "unmatched": 0,
        "imported": 0,
        "laps_written": 0,
        "records_written": 0,
        "errors": 0,
        "workout_types": {},
    }
    started = time.time()

    # 活動本身已由匯出包寫入；先關聯暖身，分類時的「近期平均」才不含暖身（N-25）
    for (athlete_id,) in conn.execute("SELECT id FROM athlete_profile").fetchall():
        link_warmups(conn, athlete_id)
    conn.commit()

    for zip_path in zips:
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                if not info.filename.lower().endswith(".fit"):
                    continue
                stats["scanned"] += 1
                if stats["scanned"] % progress_every == 0:
                    print(
                        f"  掃描 {stats['scanned']} 個檔案，已匯入 {stats['imported']} 場，"
                        f"耗時 {time.time() - started:.0f}s",
                        flush=True,
                    )

                # 多數 FIT 是每日監測片段（實測大小中位數僅 793 bytes），
                # 先用檔案大小過濾可大幅縮短掃描時間。
                if info.file_size < min_file_bytes:
                    stats["skipped_small"] += 1
                    continue

                try:
                    parsed = fit_parser.parse_fit(z.read(info.filename))
                except Exception:
                    stats["errors"] += 1
                    continue

                if parsed is None:
                    stats["no_session"] += 1
                    continue

                session = parsed["session"]
                sport = session.get("sport")
                if sport not in import_sports:
                    stats["sport_excluded"] += 1
                    continue

                activity_id = match_activity(
                    conn, session["start_time_utc"], session["total_distance_m"]
                )
                if activity_id is None:
                    stats["unmatched"] += 1
                    continue

                laps = parsed["laps"]
                records = fit_parser.downsample_records(parsed["records"], sample_every_sec)
                stats["laps_written"] += store_laps(conn, activity_id, laps)
                stats["records_written"] += store_records(conn, activity_id, records)

                if sport in classifiable_sports:
                    wtype = update_workout_type(conn, activity_id, laps)
                    stats["workout_types"][wtype] = stats["workout_types"].get(wtype, 0) + 1

                stats["imported"] += 1
                conn.commit()

    stats["elapsed_sec"] = round(time.time() - started)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(
        description="解析 Garmin 匯出包中的 FIT 檔，寫入分圈與逐秒降頻資料。"
    )
    ap.add_argument("--input-dir", help="解壓後的 Garmin 匯出目錄（會遞迴尋找 FIT zip）")
    ap.add_argument("--db-path", required=True, help="SQLite 資料庫路徑")
    ap.add_argument(
        "--reclassify",
        action="store_true",
        help="不匯入 FIT，改用資料庫已存的分圈重跑自動分類，列出課種有變動的場次（manual 不動）",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="搭配 --reclassify：實際寫入。未加時只列出差異、不寫入資料庫",
    )
    ap.add_argument(
        "--sample-every-sec",
        type=int,
        default=DEFAULT_SAMPLE_EVERY_SEC,
        help=f"逐秒資料降頻間隔，預設 {DEFAULT_SAMPLE_EVERY_SEC} 秒",
    )
    ap.add_argument(
        "--min-file-bytes",
        type=int,
        default=fit_parser.DEFAULT_MIN_FILE_BYTES,
        help="小於此大小的 FIT 檔直接略過（多為每日監測片段而非活動）",
    )
    ap.add_argument(
        "--sports",
        default=",".join(sorted(DEFAULT_IMPORT_SPORTS)),
        help=(
            "要匯入的 FIT sport 值，逗號分隔（預設只有 running）。"
            "之後要匯入 cycling、swimming 等其他運動類型時用此參數擴充，不需改程式碼。"
        ),
    )
    ap.add_argument(
        "--classifiable-sports",
        default=",".join(sorted(DEFAULT_CLASSIFIABLE_SPORTS)),
        help="要套用配速結構分類（interval/lsd/tempo/easy）的運動類型，逗號分隔，應為 --sports 的子集",
    )
    args = ap.parse_args()

    if args.reclassify:
        conn = get_connection(args.db_path)
        try:
            changes = reclassify_stored_activities(conn, apply=args.apply)
        finally:
            conn.close()
        for c in changes:
            note = ""
            if c["basis"] == "plan":
                note = f"  （依課表；分圈偵測為 {c['detected']}）"
            print(f"  {c['date']}  #{c['activity_id']}  {c['distance_km']:>5} km  "
                  f"{c['old']} → {c['new']}{note}")
        action = "已寫入" if args.apply else "未寫入（加 --apply 才會寫入）"
        print(f"\n共 {len(changes)} 場課種變動，{action}")
        return
    if not args.input_dir:
        ap.error("匯入 FIT 需要 --input-dir（或改用 --reclassify）")

    import_sports = frozenset(s.strip() for s in args.sports.split(",") if s.strip())
    classifiable_sports = frozenset(
        s.strip() for s in args.classifiable_sports.split(",") if s.strip()
    )

    conn = get_connection(args.db_path)
    try:
        stats = import_fit_files(
            conn,
            Path(args.input_dir),
            sample_every_sec=args.sample_every_sec,
            min_file_bytes=args.min_file_bytes,
            import_sports=import_sports,
            classifiable_sports=classifiable_sports,
        )
    finally:
        conn.close()

    print("\n=== 匯入完成 ===")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
