"""coach_tracking 的單元測試——「這次該不該追問」的日期判斷與 prompt 段落。

全部使用合成假資料與臨時 SQLite，不含真實個人資料。
"""

import datetime
import unittest

from src.main.python.models.db import get_connection
from src.main.python.services import coach_tracking
from src.main.python.services.context_notes_store import (
    add_note,
    add_tracking_item,
    apply_tracking_action,
)

TODAY = datetime.date(2026, 10, 27)


def _item(status="tracking", start="2026-10-01", followup=None, mentioned=None, stale=None):
    return {
        "tracking_status": status,
        "start_date": start,
        "last_followup_date": followup,
        "last_mentioned_date": mentioned,
        "stale_asked_date": stale,
    }


def _days_ago(n: int) -> str:
    return (TODAY - datetime.timedelta(days=n)).isoformat()


class TestComputeAction(unittest.TestCase):
    def test_proposed_awaits_consent(self):
        result = coach_tracking.compute_action(_item("proposed"), TODAY)
        self.assertEqual(result["action"], "await_consent")
        self.assertIsNone(result["next_followup_date"])

    def test_closed_items_need_nothing(self):
        for status in ("resolved", "dismissed"):
            with self.subTest(status=status):
                self.assertEqual(coach_tracking.compute_action(_item(status), TODAY)["action"], "none")

    def test_followup_boundary_is_three_days(self):
        two = coach_tracking.compute_action(_item(followup=_days_ago(2), mentioned=_days_ago(2)), TODAY)
        self.assertEqual(two["action"], "none")
        self.assertEqual(two["next_followup_date"], "2026-10-28")
        self.assertIn("10/28 再追問", two["action_reason"])

        three = coach_tracking.compute_action(_item(followup=_days_ago(3), mentioned=_days_ago(10)), TODAY)
        self.assertEqual(three["action"], "follow_up")
        self.assertEqual(three["next_followup_date"], TODAY.isoformat())
        self.assertEqual(three["action_reason"], "上次追問 10/24，已滿 3 天")

    def test_recent_mention_resets_followup_clock(self):
        result = coach_tracking.compute_action(
            _item(followup=_days_ago(10), mentioned=_days_ago(1)), TODAY
        )
        self.assertEqual(result["action"], "none")
        self.assertIn("學員提起 10/26", result["action_reason"])

    def test_stale_asks_once_after_thirty_days_without_mention(self):
        at_29 = coach_tracking.compute_action(_item(followup=_days_ago(1), mentioned=_days_ago(29)), TODAY)
        self.assertNotEqual(at_29["action"], "ask_continue")

        at_30 = coach_tracking.compute_action(_item(followup=_days_ago(1), mentioned=_days_ago(30)), TODAY)
        self.assertEqual(at_30["action"], "ask_continue")
        self.assertIn("30 天沒提起", at_30["action_reason"])

    def test_stale_question_is_not_repeated_but_followups_continue(self):
        """問過一次就不再問；問過也算一次追問，3 天後回到一般追問。"""
        asked_today = coach_tracking.compute_action(
            _item(followup=_days_ago(5), mentioned=_days_ago(40), stale=_days_ago(0)), TODAY
        )
        self.assertEqual(asked_today["action"], "none")

        asked_long_ago = coach_tracking.compute_action(
            _item(followup=_days_ago(5), mentioned=_days_ago(40), stale=_days_ago(5)), TODAY
        )
        self.assertEqual(asked_long_ago["action"], "follow_up")

    def test_new_silent_period_after_mention_asks_again(self):
        """問過後學員又提起，再沉默滿 30 天要再問一次（stale_asked 早於最後提起）。"""
        result = coach_tracking.compute_action(
            _item(followup=_days_ago(1), mentioned=_days_ago(31), stale=_days_ago(60)), TODAY
        )
        self.assertEqual(result["action"], "ask_continue")

    def test_without_dates_falls_back_to_start_date(self):
        result = coach_tracking.compute_action(_item(start=_days_ago(4)), TODAY)
        self.assertEqual(result["action"], "follow_up")


class TestOpenItemsAndBrief(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = self.conn.execute(
            "INSERT INTO athlete_profile (name, updated_at) VALUES ('測試學員', 'x')"
        ).lastrowid
        self.conn.commit()

    def test_brief_is_empty_without_open_items(self):
        add_note(self.conn, self.athlete_id, "出差", "2026-10-20")
        self.assertEqual(coach_tracking.build_tracking_brief(self.conn, self.athlete_id, TODAY), "")

    def test_brief_lists_open_items_with_action(self):
        knee = add_tracking_item(self.conn, self.athlete_id, "左膝外側跑完會痛", "2026-10-20")
        apply_tracking_action(self.conn, self.athlete_id, knee, "accept", "2026-10-24")
        cold = add_tracking_item(self.conn, self.athlete_id, "感冒第二天", "2026-10-26")

        brief = coach_tracking.build_tracking_brief(self.conn, self.athlete_id, TODAY)
        self.assertIn("不進事實層", brief)
        self.assertIn(f"#{knee}「左膝外側跑完會痛」", brief)
        self.assertIn("上次追問 10/24，已滿 3 天", brief)
        self.assertIn(f"#{cold}「感冒第二天」", brief)
        self.assertIn("等學員回覆要不要追蹤", brief)

    def test_open_items_shape(self):
        item_id = add_tracking_item(self.conn, self.athlete_id, "左膝痛", "2026-10-20")
        items = coach_tracking.get_open_items(self.conn, self.athlete_id, TODAY)
        self.assertEqual(
            set(items[0]),
            {
                "id", "note", "start_date", "tracking_status", "status_label",
                "last_followup_date", "last_mentioned_date", "stale_asked_date",
                "closed_date", "created_at", "action", "next_followup_date", "action_reason",
            },
        )
        self.assertEqual(items[0]["id"], item_id)
        self.assertEqual(items[0]["status_label"], "待學員同意")


if __name__ == "__main__":
    unittest.main()
