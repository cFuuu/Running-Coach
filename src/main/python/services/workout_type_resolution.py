"""決定一場跑步的「生效課種」：手動標註 → 當日外部課表 → 分圈判斷（N-28）。

為什麼課表要排在分圈判斷之前：學員的暖身習慣分開按錶重新計時，Garmin 分圈本身
不區分暖身與主段，光靠配速結構猜課種，節奏跑這類「整場平均看不出來」的課會一再
誤判（N-14／N-27）。學員知道當天課表排什麼，那是比任何啟發式都可靠的資訊。

⚠️ 與 PLAN §1 2026-09-01「品質評估不做課表對照」的關係：課表**只用來決定課種標籤**，
評估維度（配速執行度、強度、遵從度）仍然只看實際練的內容、不拿課表的目標當基準。

生效順序（`resolve_workout_type()`）：
  1. `workout_type_source='manual'`：不動（呼叫端負責略過）
  2. 當日有生效中的**外部**課表，課種為 easy／tempo／interval／lsd，且
     `plan_reconciliation.match_activity_to_plan` 對該場為 exact／inferred
     → 採課表課種，`workout_type_basis='plan'`
  3. 否則採分圈判斷結果，`workout_type_basis='laps'`

不由課表決定的情況：
  - 課表標 `race`：race 會進最佳成績與 VDOT，誤標代價高，維持手動標註
  - 自動產生（generated）的課表：已全部停用且曾與實際脫節
  - 暖身（`warmup_for_activity_id` 非空）：不參與配對，課表對到主課
  - 同日多場跑步無法唯一對應（ambiguous）：退回分圈判斷

`detected_workout_type` 永遠存純分圈判斷結果，不受課表影響；與 `workout_type` 不同
就代表「課表寫 X、分圈看起來像 Y」，是遵從度分析的依據。

分層：`resolve_workout_type()` 為純函式；查課表與寫入在其餘函式。本模組**不做**分圈
分類（那在 `workout_classifier`／`fit_import_runner`），只吃它存下來的結果。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from src.main.python.services import plan_reconciliation
from src.main.python.services.activity_types import RUNNING_ACTIVITY_TYPES

# 可由課表決定課種的課表課種。race 刻意不在內（見模組說明）。
PLAN_DRIVEN_TYPES = frozenset({"easy", "tempo", "interval", "lsd"})

BASIS_PLAN = "plan"
BASIS_LAPS = "laps"


def resolve_workout_type(
    detected: str | None, plan_type: str | None
) -> tuple[str | None, str | None]:
    """回傳 (生效課種, 依據)。`manual` 場次不應呼叫本函式。

    沒有可用課表時採分圈判斷；分圈判斷也沒有結果（None）時依據為 None。
    """
    if plan_type in PLAN_DRIVEN_TYPES:
        return plan_type, BASIS_PLAN
    if detected is not None:
        return detected, BASIS_LAPS
    return None, None


def plan_type_for_activity(conn: sqlite3.Connection, activity_id: int) -> str | None:
    """這場活動當日對應到的外部課表課種；沒有、無法唯一對應、或課種不由課表決定時回 None。"""
    row = conn.execute(
        "SELECT athlete_id, activity_type, date(started_at) AS d, warmup_for_activity_id "
        "FROM activities WHERE id = ?",
        (activity_id,),
    ).fetchone()
    if row is None or row["warmup_for_activity_id"] is not None:
        return None  # 暖身不參與配對

    plans = [
        dict(p)
        for p in conn.execute(
            """
            SELECT id, workout_type, linked_activity_id
            FROM training_plan
            WHERE athlete_id = ? AND planned_date = ? AND is_active = 1
              AND plan_source = 'external'
            """,
            (row["athlete_id"], row["d"]),
        )
    ]
    if not plans:
        return None

    # 同日候選活動不含暖身：暖身是另存的獨立活動，不該和主課爭同一份課表
    same_day = [
        dict(a)
        for a in conn.execute(
            """
            SELECT id, activity_type FROM activities
            WHERE athlete_id = ? AND date(started_at) = ? AND warmup_for_activity_id IS NULL
            """,
            (row["athlete_id"], row["d"]),
        )
    ]
    match = plan_reconciliation.match_activity_to_plan(
        {"id": activity_id, "activity_type": row["activity_type"]}, plans, same_day
    )
    if match["state"] not in ("exact", "inferred"):
        return None
    return match["plan"]["workout_type"]


def resolve_for_dates(
    conn: sqlite3.Connection,
    athlete_id: int,
    dates: list[str] | None = None,
    apply: bool = True,
) -> list[dict[str, Any]]:
    """依已存的分圈判斷結果與當日外部課表，重新決定生效課種；回傳有變動的場次。

    不重跑分圈分類（那需要分圈與近期平均），只用 `detected_workout_type`。
    舊資料尚未回填 `detected_workout_type` 時，把沿用分圈判斷的 `workout_type`
    （`workout_type_basis` 為 NULL）視為偵測結果，並順手補上。
    外部課表寫入或更新後對受影響日期呼叫，也供 CLI 回填；`manual` 不動。
    """
    types = sorted(RUNNING_ACTIVITY_TYPES)
    sql = f"""
        SELECT id, date(started_at) AS d, workout_type, workout_type_basis,
               detected_workout_type
        FROM activities
        WHERE athlete_id = ? AND activity_type IN ({",".join("?" for _ in types)})
          AND COALESCE(workout_type_source, 'auto') != 'manual'
          AND warmup_for_activity_id IS NULL
    """
    params: list[Any] = [athlete_id, *types]
    if dates is not None:
        if not dates:
            return []
        sql += f" AND date(started_at) IN ({','.join('?' for _ in dates)})"
        params.extend(dates)

    changes = []
    for row in conn.execute(sql, params).fetchall():
        detected = row["detected_workout_type"]
        if detected is None and row["workout_type_basis"] is None:
            detected = row["workout_type"]
        new_type, basis = resolve_workout_type(
            detected, plan_type_for_activity(conn, row["id"])
        )
        if new_type is None:
            continue
        if new_type != row["workout_type"]:
            changes.append({
                "activity_id": row["id"],
                "date": row["d"],
                "old": row["workout_type"],
                "new": new_type,
                "basis": basis,
                "detected": detected,
            })
        if apply:
            conn.execute(
                "UPDATE activities SET workout_type = ?, workout_type_source = 'auto', "
                "workout_type_basis = ?, detected_workout_type = ? WHERE id = ?",
                (new_type, basis, detected, row["id"]),
            )
    return changes
