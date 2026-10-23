"""warmup_links 的單元測試——全部使用合成資料，不含真實個人資料。"""

import unittest

from src.main.python.models.db import get_connection
from src.main.python.services import dashboard_queries, fit_import_runner
from src.main.python.services.warmup_links import detect_warmup_links, link_warmups


def _act(aid, started_at, minutes, pace):
    return {"id": aid, "started_at": started_at, "duration_sec": minutes * 60,
            "avg_pace_sec_per_km": pace}


class TestDetectWarmupLinks(unittest.TestCase):
    def test_typical_quality_day_is_linked(self):
        """暖身約 9 分鐘、6:50/km，29 分鐘後開始主課。"""
        acts = [_act(1, "2026-10-22T19:39:25", 9, 410), _act(2, "2026-10-22T20:08:31", 28, 332)]
        self.assertEqual(detect_warmup_links(acts), {1: 2})

    def test_race_day_long_gap_is_linked(self):
        """賽事日暖身後要走到起跑區，實測 56–65 分鐘。"""
        acts = [_act(1, "2025-12-21T05:55:00", 9, 392), _act(2, "2025-12-21T07:00:00", 117, 332)]
        self.assertEqual(detect_warmup_links(acts), {1: 2})

    def test_gap_beyond_limit_is_not_linked(self):
        acts = [_act(1, "2026-10-22T18:00:00", 9, 410), _act(2, "2026-10-22T19:15:00", 28, 332)]
        self.assertEqual(detect_warmup_links(acts), {})

    def test_short_fast_segment_is_not_a_warmup(self):
        """★ 約 2 分鐘、比主課還快的短段較像第一趟反覆，不是暖身。"""
        acts = [_act(1, "2025-09-18T19:58:00", 2, 287), _act(2, "2025-09-18T20:09:00", 43, 373)]
        self.assertEqual(detect_warmup_links(acts), {})

    def test_short_segment_between_is_skipped_when_finding_main(self):
        """★ 暖身 → 2 分鐘短段 → 主課：主課要比暖身長，短段不可被當成主課。"""
        acts = [
            _act(1, "2025-09-04T19:41:00", 11, 381),
            _act(2, "2025-09-04T20:04:00", 2, 321),
            _act(3, "2025-09-04T20:16:00", 43, 402),
        ]
        self.assertEqual(detect_warmup_links(acts), {1: 3})

    def test_interval_main_slower_on_average_is_still_linked(self):
        """★ 間歇主課的整場平均含休息，常比暖身慢——不可以配速排除。"""
        acts = [_act(1, "2025-10-30T19:30:00", 12, 348), _act(2, "2025-10-30T20:04:00", 45, 386)]
        self.assertEqual(detect_warmup_links(acts), {1: 2})

    def test_main_must_be_longer_than_warmup(self):
        acts = [_act(1, "2025-06-19T19:30:00", 6, 434), _act(2, "2025-06-19T20:08:00", 6, 425)]
        self.assertEqual(detect_warmup_links(acts), {})

    def test_too_long_first_run_is_not_a_warmup(self):
        acts = [_act(1, "2026-10-22T19:00:00", 16, 410), _act(2, "2026-10-22T19:30:00", 28, 332)]
        self.assertEqual(detect_warmup_links(acts), {})

    def test_different_days_are_not_linked(self):
        acts = [_act(1, "2026-10-21T23:50:00", 9, 410), _act(2, "2026-10-22T00:20:00", 28, 332)]
        self.assertEqual(detect_warmup_links(acts), {})

    def test_main_is_not_reused_as_next_warmup(self):
        """主課本身若也很短（例：3K 測驗 14 分鐘），不可再當下一筆的暖身。"""
        acts = [
            _act(1, "2026-10-08T19:43:00", 9, 400),
            _act(2, "2026-10-08T20:42:00", 14, 282),
            _act(3, "2026-10-08T21:00:00", 20, 270),
        ]
        self.assertEqual(detect_warmup_links(acts), {1: 2})


class _DbCase(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.conn.execute(
            "INSERT INTO athlete_profile (name, updated_at) VALUES ('Test', '2026-01-01T00:00:00')"
        )

    def tearDown(self):
        self.conn.close()

    def _insert(self, started_at, minutes, km, pace, activity_type="running"):
        return self.conn.execute(
            """
            INSERT INTO activities (athlete_id, activity_type, started_at, distance_km,
                duration_sec, avg_pace_sec_per_km, source, fetched_at)
            VALUES (1, ?, ?, ?, ?, ?, 'garmin_export', '2026-01-01T00:00:00')
            """,
            (activity_type, started_at, km, minutes * 60, pace),
        ).lastrowid

    def _link(self, aid):
        return tuple(self.conn.execute(
            "SELECT warmup_for_activity_id, warmup_link_source FROM activities WHERE id = ?",
            (aid,)).fetchone())


class TestLinkWarmups(_DbCase):
    def setUp(self):
        super().setUp()
        self.warmup = self._insert("2026-10-22T19:39:25", 9, 1.3, 410)
        self.main = self._insert("2026-10-22T20:08:31", 28, 5.0, 332)

    def test_dry_run_reports_without_writing(self):
        changes = link_warmups(self.conn, 1, apply=False)
        self.assertEqual([(c["activity_id"], c["new_main"]) for c in changes],
                         [(self.warmup, self.main)])
        self.assertEqual(self._link(self.warmup), (None, None))

    def test_apply_writes_auto_link_and_is_idempotent(self):
        link_warmups(self.conn, 1)
        self.assertEqual(self._link(self.warmup), (self.main, "auto"))
        self.assertEqual(link_warmups(self.conn, 1), [])

    def test_manual_rows_are_untouched(self):
        """人工標成「不是暖身」（manual＋NULL）後，偵測器不得再關聯。"""
        self.conn.execute(
            "UPDATE activities SET warmup_link_source = 'manual' WHERE id = ?", (self.warmup,))
        self.assertEqual(link_warmups(self.conn, 1), [])
        self.assertEqual(self._link(self.warmup), (None, "manual"))


class TestDownstreamExcludesWarmups(_DbCase):
    """N-25 下游：場次清單摺疊暖身、分類器的近期平均排除暖身。"""

    def setUp(self):
        super().setUp()
        self.warmup = self._insert("2026-10-22T19:39:25", 9, 1.3, 410)
        self.main = self._insert("2026-10-22T20:08:31", 28, 5.0, 332)
        link_warmups(self.conn, 1)

    def test_list_sessions_folds_warmup_into_main(self):
        sessions = dashboard_queries.list_sessions(
            self.conn, 1, "custom:2026-10-22:2026-10-22")["sessions"]
        self.assertEqual([s["id"] for s in sessions], [self.main])
        self.assertEqual(sessions[0]["warmup"]["id"], self.warmup)
        self.assertEqual(sessions[0]["warmup"]["distance_km"], 1.3)

    def test_session_detail_shows_warmup(self):
        detail = dashboard_queries.get_session_detail(self.conn, self.main, 1)
        self.assertEqual(detail["warmup"]["id"], self.warmup)
        warmup_detail = dashboard_queries.get_session_detail(self.conn, self.warmup, 1)
        self.assertEqual(warmup_detail["warmup_for_activity_id"], self.main)

    def test_recent_averages_exclude_warmups(self):
        later = self._insert("2026-10-24T19:00:00", 40, 6.0, 360)
        avg_km, avg_pace = fit_import_runner._recent_averages(self.conn, later)
        self.assertAlmostEqual(avg_km, 5.0)
        self.assertEqual(avg_pace, 332)


if __name__ == "__main__":
    unittest.main()
