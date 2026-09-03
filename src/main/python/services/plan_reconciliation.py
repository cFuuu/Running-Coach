"""把一場實際活動配對到當初的計畫課表——純函式，不碰資料庫、不寫入任何決策。

## 為什麼需要這層

`training_plan.linked_activity_id` 是明確的連結欄位，但**目前全專案沒有任何
一行寫入它**（2026-09-03 實測：246 筆課表中 0 筆有值）。因此實務上每一次
配對都走「同一天」的啟發式，而先前的實作只做了「同日期 + 取第一筆」——
不看運動類型、不管同一天有沒有別場活動也在搶同一份課表、歧義時直接選一個
而不告訴呼叫端這是猜的。

本專案 65 天有多場活動，最常見組合是 `walking, running`。舊邏輯會把走路那場
配到當天的跑步課表，而且以裸資料的形式送進 MCP `get_session_detail` 給教練，
沒有任何信賴度標記——違反 PLAN.md §7.3「事實層輸出必附信賴度」。

## 本模組的原則

1. **精確優先**：先看 `linked_activity_id`，再退到啟發式。
2. **啟發式要有防護**：日期相符**且**運動類型相容**且**該課表沒有被同一天
   其他活動同時競爭。
3. **歧義就說歧義**：候選超過一個時回傳 `ambiguous` 並附原因，**不選**。
   呼叫端據此不輸出計畫，而非硬塞一個可能錯的。
4. **不落地**：本模組只回傳判斷結果，不寫 `linked_activity_id`。是否要把
   配對結果持久化屬 N-7 爭點②（版本化模型），未定案前不可先斬。

## 尚未涵蓋

反方向的「計畫 → 有沒有被執行」（漏課偵測）不在本模組——那是 N-7 的 B2，
需要先定義「脫節」的門檻與出處。本模組只回答「這場活動當初排的是什麼」。
"""

from __future__ import annotations

from typing import Any, Literal

from src.main.python.services.activity_types import (
    RUNNING_ACTIVITY_TYPES,
    STRENGTH_ACTIVITY_TYPES,
)

MatchState = Literal["exact", "inferred", "ambiguous", "none"]

# `training_plan.workout_type` → 可視為執行了該課表的 `activities.activity_type`。
#
# `rest` 刻意不列：休息日沒有「對應的活動」，當天真的去跑了不代表執行了休息課表。
# `cross_training` 用排除法（非跑步、非重訓）——交叉訓練的定義本來就是「跑步與
# 重訓以外的有氧」，逐一列舉單車/游泳/健行只會漏掉沒想到的種類。
_RUNNING_WORKOUT_TYPES = frozenset({"easy", "tempo", "interval", "lsd", "race"})
_STRENGTH_WORKOUT_TYPES = frozenset({"strength"})
_CROSS_TRAINING_WORKOUT_TYPES = frozenset({"cross_training"})


def is_compatible(workout_type: str | None, activity_type: str | None) -> bool:
    """該課種是否可能由這個運動類型的活動來執行。"""
    if not workout_type or not activity_type:
        return False
    if workout_type in _RUNNING_WORKOUT_TYPES:
        return activity_type in RUNNING_ACTIVITY_TYPES
    if workout_type in _STRENGTH_WORKOUT_TYPES:
        return activity_type in STRENGTH_ACTIVITY_TYPES
    if workout_type in _CROSS_TRAINING_WORKOUT_TYPES:
        return (
            activity_type not in RUNNING_ACTIVITY_TYPES
            and activity_type not in STRENGTH_ACTIVITY_TYPES
        )
    # 'rest' 與任何未來新增的課種：不臆測相容性
    return False


def match_activity_to_plan(
    activity: dict[str, Any],
    plan_rows: list[dict[str, Any]],
    same_day_activities: list[dict[str, Any]],
) -> dict[str, Any]:
    """把一場活動配對到計畫課表。

    參數：
        activity            ：至少含 `id`、`activity_type`
        plan_rows           ：**該活動當天**的生效課表列，含 `id`、`workout_type`、
                              `linked_activity_id`
        same_day_activities ：同一天的所有活動（含 `activity` 自己），至少含
                              `id`、`activity_type`

    回傳 `{state, method, plan, reason}`：
        state  ：exact / inferred / ambiguous / none
        method ：linked_activity_id / date_and_sport / None
        plan   ：配對到的課表列；state 非 exact/inferred 時為 None
        reason ：未能配對或歧義的原因代碼；配對成功時為 None
    """
    activity_id = activity.get("id")

    # 1) 精確配對：明確的連結欄位。目前無人寫入，但一旦 N-7 決定落地即生效。
    for plan in plan_rows:
        if activity_id is not None and plan.get("linked_activity_id") == activity_id:
            return {
                "state": "exact",
                "method": "linked_activity_id",
                "plan": plan,
                "reason": None,
            }

    # 2) 啟發式候選：運動類型相容，且該課表未被連結到「別場」活動。
    candidates = [
        plan
        for plan in plan_rows
        if is_compatible(plan.get("workout_type"), activity.get("activity_type"))
        and (
            plan.get("linked_activity_id") is None
            or plan.get("linked_activity_id") == activity_id
        )
    ]
    if not candidates:
        return {
            "state": "none",
            "method": None,
            "plan": None,
            "reason": "no_compatible_plan",
        }
    if len(candidates) > 1:
        return {
            "state": "ambiguous",
            "method": None,
            "plan": None,
            "reason": "multiple_plan_candidates",
        }

    # 3) 反向競爭：同一天有多場活動同樣相容於這份課表時，無法斷定是哪一場
    #    執行了它。硬選一場會讓另一場也宣稱自己執行了同一份課表。
    plan = candidates[0]
    competitors = [
        other
        for other in same_day_activities
        if other.get("id") != activity_id
        and is_compatible(plan.get("workout_type"), other.get("activity_type"))
    ]
    if competitors:
        return {
            "state": "ambiguous",
            "method": None,
            "plan": None,
            "reason": "multiple_activities_for_one_plan",
        }

    return {
        "state": "inferred",
        "method": "date_and_sport",
        "plan": plan,
        "reason": None,
    }
