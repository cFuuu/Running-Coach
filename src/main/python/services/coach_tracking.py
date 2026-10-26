"""追蹤事項（TODO N-35 ①②）的判斷層：「這次該不該追問」由程式算好。

Fu 的情境（2026-10-26）：上次提到受傷，這次沒講，教練就不會注意。追蹤事項
存在 context_notes（tracking_status ≠ 'none'，存取與狀態轉換見
context_notes_store.py），本模組只做**唯讀計算**：

- 對每個事項算出這次該做的事（action）、下次追問日與一句中文理由。
- 組出帶進教練 prompt 的段落（build_tracking_brief）。

設計原則同「事實由程式算、教練負責解讀」：日期相減由這裡做，教練照結論做，
不交給模型自己算（模型算日期會錯，也無法驗證）。

⚠️ **硬邊界：追蹤事項是學員自述的軟性輸入（PLAN.md §7.5），永不進事實層。**
readiness.py／training_load.py／session_quality.py **皆不得** import 本模組。

分層比照 context_notes_store.py：吃 sqlite3.Connection，不 import fastapi。
"""

from __future__ import annotations

import datetime
import sqlite3
from typing import Any

from src.main.python.services import context_notes_store

# Fu 2026-10-26 決策：每件事最多每 3 天追問一次。
FOLLOWUP_INTERVAL_DAYS = 3
# Fu 2026-10-26 決策：學員 30 天都沒提起的事項，教練只問一次「還要繼續追蹤嗎？」，
# 不自動結案——傷沒好也可能只是沒提。
STALE_DAYS = 30

ACTION_AWAIT_CONSENT = "await_consent"
ACTION_ASK_CONTINUE = "ask_continue"
ACTION_FOLLOW_UP = "follow_up"
ACTION_NONE = "none"

# 給教練看的「這次該做的事」
_ACTION_INSTRUCTIONS = {
    ACTION_AWAIT_CONSENT: "等學員回覆要不要追蹤：同意記 accept，不要就記 decline",
    ACTION_ASK_CONTINUE: "問一次「還要繼續追蹤嗎？」，問完記 stale_asked；不要自行結案",
    ACTION_FOLLOW_UP: "這次主動追問近況，問完記 followed_up",
    ACTION_NONE: "這次不用主動追問；學員自己提到時記 mentioned",
}


def _as_date(value: datetime.date | str | None) -> datetime.date | None:
    if value is None or isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(value)


def _md(day: datetime.date) -> str:
    """10/24 這種短日期，給 action_reason 用。"""
    return f"{day.month}/{day.day}"


def compute_action(item: dict[str, Any], today: datetime.date | str) -> dict[str, Any]:
    """算一個追蹤事項這次該做的事，回傳 {action, next_followup_date, action_reason}。

    - proposed → await_consent。
    - tracking：
      1. 學員最後提起日（last_mentioned_date；沒有就用同意追蹤那天——accept 會把
         它設為當天——再沒有才退回 start_date）距今 ≥ STALE_DAYS，且 stale_asked_date
         為空或早於最後提起日 → ask_continue（同一段沒提起的期間只問一次）。
      2. 否則取「上次追問、學員最後提起、問過要不要繼續」三者最晚的一天，
         距今 ≥ FOLLOWUP_INTERVAL_DAYS → follow_up；否則 none。
         問「還要繼續追蹤嗎」本身也算一次追問，所以 stale_asked_date 也計入。
    - resolved／dismissed → none（已結案）。

    next_followup_date：下次該主動問的日子（ISO 字串）；已到期時就是今天，
    待同意與已結案為 None。
    """
    today_d = _as_date(today)
    status = item["tracking_status"]

    if status == "proposed":
        return {
            "action": ACTION_AWAIT_CONSENT,
            "next_followup_date": None,
            "action_reason": "教練已提議追蹤，等學員同意",
        }
    if status != "tracking":
        return {
            "action": ACTION_NONE,
            "next_followup_date": None,
            "action_reason": f"已結案（{context_notes_store.status_label(status)}）",
        }

    start = _as_date(item["start_date"])
    followup = _as_date(item.get("last_followup_date"))
    mentioned = _as_date(item.get("last_mentioned_date")) or followup or start
    stale_asked = _as_date(item.get("stale_asked_date"))

    silent_days = (today_d - mentioned).days
    if silent_days >= STALE_DAYS and (stale_asked is None or stale_asked < mentioned):
        return {
            "action": ACTION_ASK_CONTINUE,
            "next_followup_date": today_d.isoformat(),
            "action_reason": (
                f"學員最後提起是 {_md(mentioned)}，已 {silent_days} 天沒提起"
                f"（滿 {STALE_DAYS} 天），問一次還要不要繼續追蹤"
            ),
        }

    # 最近一次接觸：追問、學員提起、問過要不要繼續，取最晚者
    candidates = [(mentioned, "學員提起")]
    if followup is not None:
        candidates.append((followup, "上次追問"))
    if stale_asked is not None:
        candidates.append((stale_asked, "問過要不要繼續追蹤"))
    # 同一天時「上次追問」優先顯示（比「學員提起」好懂），故依日期再依標籤順序挑
    priority = {"上次追問": 0, "問過要不要繼續追蹤": 1, "學員提起": 2}
    last_contact, label = max(candidates, key=lambda c: (c[0], -priority[c[1]]))
    due = last_contact + datetime.timedelta(days=FOLLOWUP_INTERVAL_DAYS)

    if today_d >= due:
        return {
            "action": ACTION_FOLLOW_UP,
            "next_followup_date": today_d.isoformat(),
            "action_reason": f"{label} {_md(last_contact)}，已滿 {FOLLOWUP_INTERVAL_DAYS} 天",
        }
    return {
        "action": ACTION_NONE,
        "next_followup_date": due.isoformat(),
        "action_reason": (
            f"{label} {_md(last_contact)}，未滿 {FOLLOWUP_INTERVAL_DAYS} 天，{_md(due)} 再追問"
        ),
    }


def to_public_item(item: dict[str, Any], today: datetime.date | str) -> dict[str, Any]:
    """把 context_notes 的一列轉成對外（MCP 工具／REST）的形狀，附上算好的 action。

    不含 athlete_id——前端與教練都用不到，少一個欄位少一分誤用。
    """
    return {
        "id": item["id"],
        "note": item["note"],
        "start_date": item["start_date"],
        "tracking_status": item["tracking_status"],
        "status_label": context_notes_store.status_label(item["tracking_status"]),
        "last_followup_date": item["last_followup_date"],
        "last_mentioned_date": item["last_mentioned_date"],
        "stale_asked_date": item["stale_asked_date"],
        "closed_date": item["closed_date"],
        "created_at": item["created_at"],
        **compute_action(item, today),
    }


def get_open_items(
    conn: sqlite3.Connection, athlete_id: int, today: datetime.date | str
) -> list[dict[str, Any]]:
    """進行中（待同意＋追蹤中）的事項，各自附上這次該做的事。依起始日排序。"""
    return [
        to_public_item(row, today)
        for row in context_notes_store.list_tracking_items(conn, athlete_id)
    ]


def build_tracking_brief(
    conn: sqlite3.Connection, athlete_id: int, today: datetime.date | str
) -> str:
    """給教練 prompt 開頭用的「追蹤事項」段落；沒有進行中事項時回空字串。

    ⚠️ 內容是學員自述，段落開頭明講不進事實層；action 已由程式算好，
    教練照做、不要自己算日期。
    """
    items = get_open_items(conn, athlete_id, today)
    if not items:
        return ""
    today_d = _as_date(today)
    lines = [
        f"## 追蹤事項（今天 {today_d.isoformat()}）",
        "以下是學員自述、要持續關注的狀況，引號內是學員原話。"
        "這是軟性輸入，**不是量測數據、不進事實層**，不可拿來推算任何數值或調整判讀門檻。",
        "「這次該做的事」已由程式依日期算好，照做即可，不要自己算日期。"
        "記錄與結案用 update_tracking_item（帶 id）。",
    ]
    for item in items:
        lines.append(
            f"- #{item['id']}「{item['note']}」（起始 {item['start_date']}，{item['status_label']}）"
            f"→ {_ACTION_INSTRUCTIONS[item['action']]}。依據：{item['action_reason']}"
        )
    return "\n".join(lines)
