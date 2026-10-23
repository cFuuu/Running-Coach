"""把 periodization_scheduler.generate_schedule() 的輸出寫入 training_plan，
並維護 PLAN.md §5.8 定案的版本歷史設計（Issue #19）。

本模組是資料庫存取層：吃 sqlite3.Connection，不 import fastapi，方便單元測試
用臨時 SQLite 驗證（與 dashboard_queries.py 的分層方式一致）。與
periodization_scheduler.py／vdot_engine.py 等「純函式、不碰資料庫」的模組
定位不同——這裡的職責就是把純函式的輸出落地到資料庫。

版本化寫入規則——對應 Issue #19 Solution 段落：
    - 每次呼叫 save_schedule() 視為「產生一批新排程」，寫入的每一列
      plan_source='generated'、is_active=1。
    - 新舊排程的對應邏輯：同一 athlete_id、同一 planned_date、且
      plan_source='generated' 的既有生效列（is_active=1），視為被同一天的
      新列取代——舊列改為 is_active=0，superseded_by 指向新列 id，
      舊列本身不刪除、不覆蓋其餘欄位。
    - plan_source='external'（外部課表，如跟團課表）的既有列永遠不被本模組
      的寫入邏輯取代或觸碰——這呼應 periodization_scheduler 的
      _apply_external_dates()：外部課表日期本來就不會出現在
      generate_schedule() 的輸出中，本模組進一步確保寫入層也不會誤動它們。
    - 沒有舊排程可取代的日期（首次寫入該天），新列 is_active=1，
      superseded_by 維持 NULL，無需額外處理。

配速轉換：generate_schedule() 的 pace_zone 是區間
（{"fast_sec_per_km", "slow_sec_per_km"} 或 None），但 training_plan.
planned_pace_sec_per_km 是單一欄位。取區間中點作為代表值——本表本來就
只存單一配速供概覽查詢用，完整區間仍可從當初呼叫 vdot_engine 的結果
重新取得，不需要在 training_plan 這張表裡重複保存。workout_type='rest'
（pace_zone=None）時 planned_pace_sec_per_km 為 NULL。
"""

from __future__ import annotations

import datetime
import sqlite3
from typing import Any

from src.main.python.services.plan_reconciliation import is_compatible
from src.main.python.services.workout_type_resolution import resolve_for_dates


def _pace_zone_midpoint(pace_zone: dict[str, float] | None) -> int | None:
    if pace_zone is None:
        return None
    return round((pace_zone["fast_sec_per_km"] + pace_zone["slow_sec_per_km"]) / 2)


def save_schedule(
    conn: sqlite3.Connection,
    athlete_id: int,
    schedule: list[dict[str, Any]],
    created_at: str | None = None,
) -> list[int]:
    """把 generate_schedule() 的輸出寫入 training_plan，維護版本歷史。

    schedule：periodization_scheduler.generate_schedule() 的回傳值，每筆至少
        含 "date"／"workout_type"／"target_distance_km"／"pace_zone"。

    created_at：本批寫入的時間戳記（ISO 格式字串）。未提供時使用呼叫當下的
        UTC 時間；供測試需要固定時間戳記時覆寫。

    回傳：本次新寫入列的 id 清單，依 schedule 原始順序對應。

    交易邊界：整批寫入（含取代舊列）在單一資料庫交易內完成，任何一步失敗
    整批 rollback，不會留下「舊列已停用但新列未寫入」的中間狀態。
    """
    if created_at is None:
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    new_ids: list[int] = []
    try:
        for day in schedule:
            planned_date = day["date"].isoformat()

            existing = conn.execute(
                """
                SELECT id FROM training_plan
                WHERE athlete_id = ? AND planned_date = ?
                  AND plan_source = 'generated' AND is_active = 1
                """,
                (athlete_id, planned_date),
            ).fetchone()

            cursor = conn.execute(
                """
                INSERT INTO training_plan
                    (athlete_id, planned_date, workout_type, planned_distance_km,
                     planned_pace_sec_per_km, plan_source, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, 'generated', 1, ?)
                """,
                (
                    athlete_id,
                    planned_date,
                    day["workout_type"],
                    day["target_distance_km"],
                    _pace_zone_midpoint(day["pace_zone"]),
                    created_at,
                ),
            )
            new_id = cursor.lastrowid
            new_ids.append(new_id)

            if existing is not None:
                conn.execute(
                    "UPDATE training_plan SET is_active = 0, superseded_by = ? WHERE id = ?",
                    (new_id, existing["id"]),
                )

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return new_ids


def get_active_schedule(conn: sqlite3.Connection, athlete_id: int) -> list[dict[str, Any]]:
    """取得該學員目前生效中的完整課表（is_active=1），依日期升冪排序。

    涵蓋 plan_source='generated' 與 'external' 兩者——「目前生效的完整課表」
    對使用者而言不分來源，皆是「現在該照哪個安排練」的一部分。
    """
    rows = conn.execute(
        """
        SELECT id, athlete_id, planned_date, workout_type, planned_distance_km,
               planned_duration_sec, planned_pace_sec_per_km, notes, plan_source,
               linked_activity_id, is_active, superseded_by, created_at
        FROM training_plan
        WHERE athlete_id = ? AND is_active = 1
        ORDER BY planned_date
        """,
        (athlete_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_plan_history_for_date(
    conn: sqlite3.Connection, athlete_id: int, planned_date: datetime.date
) -> list[dict[str, Any]]:
    """取得該學員、該日期的完整排程版本歷史（含已被取代的舊列與目前生效列）。

    依 created_at 升冪排序，讓呼叫端能看到「原計畫 → 後續調整」的時間順序，
    呼應 PLAN.md §5.8「原計畫 vs 調整後計畫」的偏差比對需求。
    """
    rows = conn.execute(
        """
        SELECT id, athlete_id, planned_date, workout_type, planned_distance_km,
               planned_duration_sec, planned_pace_sec_per_km, notes, plan_source,
               linked_activity_id, is_active, superseded_by, created_at
        FROM training_plan
        WHERE athlete_id = ? AND planned_date = ?
        ORDER BY created_at
        """,
        (athlete_id, planned_date.isoformat()),
    ).fetchall()
    return [dict(row) for row in rows]


# 外部課表允許的課種——與 schema.sql 的 training_plan.workout_type CHECK 一致。
# ⚠️ 跑團教練口中的課種名稱不保證落在這 8 種之內（例如「配速跑」「山路」
# 「間歇 400×8」）。無法對應時的正確做法是挑最接近的類別，並把**原文**放進
# notes 保留，而不是新增一個 CHECK 擋不住的值——課種欄位是給規則引擎比對用的
# 有限集合，notes 才是給人與判斷層看的原始描述。
EXTERNAL_WORKOUT_TYPES: tuple[str, ...] = (
    "easy",
    "tempo",
    "interval",
    "lsd",
    "race",
    "rest",
    "strength",
    "cross_training",
)


def _to_iso_date(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime.date) else value


def save_external_workouts(
    conn: sqlite3.Connection,
    athlete_id: int,
    workouts: list[dict[str, Any]],
    deactivate_generated_between: bool = False,
) -> list[int]:
    """把外部來源（如跟團教練）的課表逐日寫入 training_plan。

    workouts：每筆為 dict，必要欄位 "planned_date"（datetime.date 或
        YYYY-MM-DD 字串）與 "workout_type"（須為 EXTERNAL_WORKOUT_TYPES 之一）；
        選填 "planned_distance_km"／"planned_duration_sec"／
        "planned_pace_sec_per_km"／"notes"。

    ⚠️ **刻意不定義任何跑團課表的檔案格式**（CSV／截圖／PDF 皆不在此處理）。
    P-1 卡住的正是「還沒看到真實格式」，但**逐日欄位本身與格式無關**——
    先把落地能力做好，日後不論從哪種格式解析，出口都是這個函式。

    版本化行為（承 §1「training_plan 保留版本歷史，不覆蓋刪除」）：
        - 同一天已有生效中的列**不分來源**（generated 或 external）皆標記
          is_active=0、superseded_by 指向新列。這與 save_schedule() 只取代
          'generated' 的行為**刻意不同**：§1 已拍板「跟團課表介入時系統退居
          分析層，以跟團課表為唯一基準」，故外部課表必須能蓋掉系統自產的
          那天，否則同一天會有兩份生效課表，get_active_schedule() 會回傳
          兩列、教練不知該聽誰的。反向則不成立——save_schedule() 依舊不碰
          external 列，系統不得偷偷用自產課表覆蓋教練的安排。
        - 舊列一律保留（只改旗標），偏差比對迴圈仍看得到「原本排什麼」。

    deactivate_generated_between：為 True 時，把本批課表**日期範圍內**
        （最早到最晚那天）其餘還生效的 'generated' 列一併標記為失效。
        預設 False——這是有副作用的批次操作，須由呼叫端明確要求。

        為什麼需要這個選項：跑團課表通常只排有練的日子（如週二、四、日），
        中間空檔不會出現在 workouts 裡。若不處理，那些日子仍留著系統自產的
        排程（常是 'rest' 或 easy），教練查課表時會看到「跑團課 + 系統補的課」
        混在一起，違反 §1「跟團課表介入時系統退居分析層、不在教練沒排的
        日子自動補課」。標記失效而非刪除，版本歷史仍完整保留。

        ⚠️ 只影響 'generated'，不動其他 'external' 列——同一段期間可能有
        先前已落地的跑團課，那些不該被這批蓋掉（同日的取代仍由上述逐日
        邏輯處理）。

    回傳：新寫入列的 id 清單，順序同輸入。

    重複執行同一批課表會產生新版本列（舊列失效），不會出現重複的生效列——
    這讓「教練改了某天的課」只要重跑一次即可，不需要先刪除。
    """
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    new_ids: list[int] = []

    for workout in workouts:
        planned_date = _to_iso_date(workout["planned_date"])

        workout_type = workout["workout_type"]
        if workout_type not in EXTERNAL_WORKOUT_TYPES:
            raise ValueError(
                f"workout_type 必須是 {EXTERNAL_WORKOUT_TYPES} 之一，收到 {workout_type!r}；"
                "跑團的原始課種名稱請放進 notes 保留"
            )

        superseded_rows = conn.execute(
            """
            SELECT id FROM training_plan
            WHERE athlete_id = ? AND planned_date = ? AND is_active = 1
            """,
            (athlete_id, planned_date),
        ).fetchall()

        cursor = conn.execute(
            """
            INSERT INTO training_plan
                (athlete_id, planned_date, workout_type, planned_distance_km,
                 planned_duration_sec, planned_pace_sec_per_km, notes,
                 plan_source, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'external', 1, ?)
            """,
            (
                athlete_id,
                planned_date,
                workout_type,
                workout.get("planned_distance_km"),
                workout.get("planned_duration_sec"),
                workout.get("planned_pace_sec_per_km"),
                workout.get("notes"),
                created_at,
            ),
        )
        new_id = cursor.lastrowid
        new_ids.append(new_id)

        for row in superseded_rows:
            conn.execute(
                "UPDATE training_plan SET is_active = 0, superseded_by = ? WHERE id = ?",
                (new_id, row["id"]),
            )

    if deactivate_generated_between and workouts:
        dates = [
            _to_iso_date(w["planned_date"]) for w in workouts
        ]
        conn.execute(
            """
            UPDATE training_plan SET is_active = 0
            WHERE athlete_id = ? AND is_active = 1 AND plan_source = 'generated'
              AND planned_date >= ? AND planned_date <= ?
            """,
            (athlete_id, min(dates), max(dates)),
        )

    # N-28：課種先看當日外部課表。課表寫入或更新後，重新決定受影響日期上跑步的生效課種
    # （被取代的課表不再生效，該天會自動退回分圈判斷）。
    resolve_for_dates(conn, athlete_id, sorted({_to_iso_date(w["planned_date"]) for w in workouts}))

    conn.commit()
    return new_ids


# --------------------------------------------------------------------------
# 學員確認的課表配對（PLAN.md §1 2026-10-24／N-29）
# --------------------------------------------------------------------------


class PlanLinkNotFoundError(LookupError):
    """課表或活動不存在（或不屬於這位學員）。路由層轉 404。"""


class PlanLinkRejectedError(ValueError):
    """課表與活動都存在，但這個連結不合法。路由層轉 422。"""


def set_plan_link(
    conn: sqlite3.Connection,
    athlete_id: int,
    plan_id: int,
    activity_id: int | None,
) -> dict[str, Any]:
    """把學員在行事曆確認的「這場是課表」寫入 `training_plan.linked_activity_id`。

    `activity_id=None` 清除連結。**只寫這一欄**，不改課表內容、不動其他列——
    這是 PLAN.md §1「課表配對歧義時由學員確認」唯一開放的寫入；啟發式配對的
    結果仍不落地（見 plan_reconciliation 原則 4）。

    檢查（任一不過就不寫）：
        - 課表存在、屬於這位學員（否則 PlanLinkNotFoundError——不區分「不存在」
          與「是別人的」，避免透露其他學員的資料是否存在）
        - 課表 `is_active=1`：已被取代的舊版本不接受新連結
        - 活動存在、屬於這位學員（同上）
        - 活動日期 `date(started_at)` 等於 `planned_date`（與行事曆
          `dashboard_queries.get_calendar()` 同一種日期口徑）
        - 活動不是已併入主課的暖身（`warmup_for_activity_id` 非空）——要連的是主課
        - 運動種類相容（`plan_reconciliation.is_compatible`，與配對規則同一份判斷）
        - 該活動沒有已連結到另一份生效課表：否則兩份課表都會宣稱被同一場執行

    ⚠️ 已知缺口（N-7 處理）：課表日後被 `superseded_by` 取代時，新的那一列
    **不會**帶入這裡寫的連結，該天會退回啟發式配對。刻意不在本函式處理——
    連結要不要、怎麼跟著版本走，屬 N-7 版本化模型的範圍。

    回傳 `{"plan_id", "linked_activity_id"}`。
    """
    plan = conn.execute(
        "SELECT id, planned_date, workout_type, is_active FROM training_plan "
        "WHERE id = ? AND athlete_id = ?",
        (plan_id, athlete_id),
    ).fetchone()
    if plan is None:
        raise PlanLinkNotFoundError(f"找不到課表 id={plan_id}")
    if not plan["is_active"]:
        raise PlanLinkRejectedError("這份課表已被新版本取代，不能再連結活動")

    if activity_id is not None:
        activity = conn.execute(
            "SELECT id, activity_type, date(started_at) AS d, warmup_for_activity_id "
            "FROM activities WHERE id = ? AND athlete_id = ?",
            (activity_id, athlete_id),
        ).fetchone()
        if activity is None:
            raise PlanLinkNotFoundError(f"找不到活動 id={activity_id}")
        if activity["d"] != plan["planned_date"]:
            raise PlanLinkRejectedError(
                f"活動日期（{activity['d']}）與課表日期（{plan['planned_date']}）不同"
            )
        if activity["warmup_for_activity_id"] is not None:
            raise PlanLinkRejectedError("這場是已併入主課的暖身，請改連結主課")
        if not is_compatible(plan["workout_type"], activity["activity_type"]):
            raise PlanLinkRejectedError(
                f"運動種類不相容：課表是 {plan['workout_type']}，"
                f"活動是 {activity['activity_type']}"
            )
        other = conn.execute(
            "SELECT id FROM training_plan WHERE athlete_id = ? AND linked_activity_id = ? "
            "AND is_active = 1 AND id != ?",
            (athlete_id, activity_id, plan_id),
        ).fetchone()
        if other is not None:
            raise PlanLinkRejectedError(
                f"這場活動已連結到另一份課表（id={other['id']}），請先取消那份的連結"
            )

    conn.execute(
        "UPDATE training_plan SET linked_activity_id = ? WHERE id = ?",
        (activity_id, plan_id),
    )
    conn.commit()
    return {"plan_id": plan_id, "linked_activity_id": activity_id}
