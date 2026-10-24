"""garmin_cards 的單元測試——以真實 schema 建 :memory: 資料庫，合成資料，不發網路請求。"""

from __future__ import annotations

import json
import unittest

from src.main.python.models.db import get_connection
from src.main.python.services import garmin_cards


class TestVo2maxRating(unittest.TestCase):
    """★ 門檻為下限（≥），值取自 Garmin 手冊的 Cooper Institute 表；
    名稱用 Garmin 台灣譯名（95 百分位＝優異、80＝傑出）。"""

    def test_cutoff_is_inclusive_lower_bound(self):
        self.assertEqual(garmin_cards.vo2max_rating(55.4, "MALE", 27)["label"], "優異")
        self.assertEqual(garmin_cards.vo2max_rating(55.3, "MALE", 27)["label"], "傑出")

    def test_matches_garmin_connect_display(self):
        """2026-10-22 Garmin 網頁：27 歲男性 VO2max 52.5（顯示 53）為「傑出」。"""
        self.assertEqual(garmin_cards.vo2max_rating(52.5, "MALE", 27)["label"], "傑出")

    def test_below_fair_is_poor(self):
        self.assertEqual(garmin_cards.vo2max_rating(41.6, "MALE", 25)["label"], "不佳")

    def test_sex_selects_column(self):
        self.assertEqual(garmin_cards.vo2max_rating(50.0, "FEMALE", 25)["label"], "優異")
        self.assertEqual(garmin_cards.vo2max_rating(50.0, "MALE", 25)["label"], "良好")

    def test_missing_inputs_or_out_of_table_give_none(self):
        self.assertIsNone(garmin_cards.vo2max_rating(53, None, 27))
        self.assertIsNone(garmin_cards.vo2max_rating(53, "MALE", None))
        self.assertIsNone(garmin_cards.vo2max_rating(53, "MALE", 18))


class TestGetGarminCards(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.conn.execute(
            "INSERT INTO athlete_profile (id, name, sex, updated_at) VALUES (1, 'T', 'MALE', 'x')")
        for date, score, steps, mod, vig in (
            ("2026-10-19", 70, 9000, 30, 0),   # 週一
            ("2026-10-20", None, 8000, 0, 20),
            ("2026-10-21", 80, 1306, 10, 5),
        ):
            self.conn.execute(
                "INSERT INTO daily_wellness (athlete_id, date, sleep_score, steps, steps_goal, "
                "intensity_minutes_moderate, intensity_minutes_vigorous, "
                "intensity_minutes_weekly_goal, source, fetched_at) "
                "VALUES (1, ?, ?, ?, 12360, ?, ?, 150, 'garmin_export', 'x')",
                (date, score, steps, mod, vig),
            )
        self.conn.execute(
            "INSERT INTO wellness_intraday (athlete_id, date, kind, points_json, source, fetched_at) "
            "VALUES (1, '2026-10-21', 'sleep_levels', ?, 'garmin_export', 'x')",
            (json.dumps([["01:37", "02:00", 1]]),),
        )
        for date, phrase in (("2026-10-20", "PRODUCTIVE_9"), ("2026-10-21", "PRODUCTIVE_3")):
            self.conn.execute(
                "INSERT INTO training_status (athlete_id, date, training_status_phrase, "
                "status_since_date, vo2max_precise, source, fetched_at) "
                "VALUES (1, ?, ?, '2026-10-09', 53.4, 'garmin_export', 'x')",
                (date, phrase),
            )
        self.conn.execute(
            "INSERT INTO performance_metrics (athlete_id, date, chronological_age, source, fetched_at) "
            "VALUES (1, '2026-10-21', 27, 'garmin_export', 'x')")
        self.cards = garmin_cards.get_garmin_cards(self.conn, 1, "7d")["cards"]

    def tearDown(self):
        self.conn.close()

    def test_each_card_uses_its_own_latest_day(self):
        self.assertEqual(self.cards["sleep"]["score"], 80)
        self.assertEqual(self.cards["sleep"]["levels"], [["01:37", "02:00", 1]])

    def test_series_skips_days_without_value(self):
        self.assertEqual([p["date"] for p in self.cards["sleep"]["series"]],
                         ["2026-10-19", "2026-10-21"])

    def test_hrv_series_carries_daily_balanced_range(self):
        for date, avg, low, upper in (("2026-10-22", 57, 52, 77), ("2026-10-23", 58, None, None)):
            self.conn.execute(
                "INSERT INTO daily_wellness (athlete_id, date, hrv_weekly_avg_ms, "
                "hrv_baseline_balanced_low, hrv_baseline_balanced_upper, source, fetched_at) "
                "VALUES (1, ?, ?, ?, ?, 'garmin_mcp', 'x')", (date, avg, low, upper))
        series = garmin_cards.get_garmin_cards(self.conn, 1, "7d")["cards"]["hrv"]["series"]
        self.assertEqual([(p["date"], p["hrv_baseline_balanced_low"], p["hrv_baseline_balanced_upper"])
                          for p in series],
                         [("2026-10-22", 52, 77), ("2026-10-23", None, None)])

    def test_steps_with_goal(self):
        self.assertEqual((self.cards["steps"]["value"], self.cards["steps"]["goal"]), (1306, 12360))

    def test_intensity_week_total_counts_vigorous_double(self):
        im = self.cards["intensity_minutes"]
        self.assertEqual(im["week_start"], "2026-10-19")
        self.assertEqual(im["week_total"], 30 + 2 * 20 + 10 + 2 * 5)
        self.assertEqual(im["weekly_goal"], 150)

    def test_training_readiness_card_carries_factor_feedback(self):
        """完備程度卡附上六個因素的評級代碼與對應數值（Garmin 網頁的「因素」清單）。"""
        self.conn.execute(
            "UPDATE daily_wellness SET training_readiness_score = 61, readiness_level = 'MODERATE', "
            "readiness_sleep_feedback = 'GOOD', readiness_recovery_feedback = 'GOOD', "
            "readiness_hrv_feedback = 'VERY_GOOD', readiness_load_feedback = 'GOOD', "
            "readiness_sleep_history_feedback = 'MODERATE', readiness_stress_feedback = 'GOOD', "
            "recovery_time_hours = 9, acute_load = 406, hrv_weekly_avg_ms = 58, hrv_status = 'BALANCED' "
            "WHERE date = '2026-10-21'")
        card = garmin_cards.get_garmin_cards(self.conn, 1, "7d")["cards"]["training_readiness"]
        self.assertEqual(card["score"], 61)
        self.assertEqual((card["sleep_feedback"], card["sleep_score"]), ("GOOD", 80))
        self.assertEqual((card["recovery_feedback"], card["recovery_time_hours"]), ("GOOD", 9))
        self.assertEqual((card["hrv_feedback"], card["hrv_weekly_avg_ms"], card["hrv_status"]),
                         ("VERY_GOOD", 58, "BALANCED"))
        self.assertEqual((card["load_feedback"], card["acute_load"]), ("GOOD", 406))
        self.assertEqual(card["sleep_history_feedback"], "MODERATE")
        self.assertEqual(card["stress_feedback"], "GOOD")

    def test_training_status_strips_variant_suffix_and_uses_garmin_since(self):
        """PRODUCTIVE_3 → PRODUCTIVE；起始日用 Garmin 給的，即使早於 7d 區間。"""
        ts = self.cards["training_status"]
        self.assertEqual(ts["status"], "PRODUCTIVE")
        self.assertEqual(ts["since"], "2026-10-09")

    def test_vo2max_rating_uses_profile_sex_and_age(self):
        # 27 歲男性：53.4 ≥ 51.1（傑出下限）、< 55.4（優異下限）
        rating = self.cards["vo2max"]["rating"]
        self.assertEqual(rating["label"], "傑出")
        self.assertEqual(rating["cutoffs"], [41.7, 45.4, 51.1, 55.4])

    def test_load_focus_has_three_categories_with_targets(self):
        self.conn.execute(
            "UPDATE training_status SET monthly_load_aerobic_low = 879, monthly_load_aerobic_high = 487, "
            "monthly_load_anaerobic = 11, monthly_load_aerobic_low_target_min = 333, "
            "monthly_load_aerobic_low_target_max = 552, training_balance_feedback = 'LOW_AEROBIC_OVERAGE' "
            "WHERE date = '2026-10-21'")
        focus = garmin_cards.get_garmin_cards(self.conn, 1, "7d")["cards"]["load_focus"]
        self.assertEqual([c["key"] for c in focus["categories"]], ["anaerobic", "aerobic_high", "aerobic_low"])
        low = focus["categories"][2]
        self.assertEqual((low["value"], low["target_min"], low["target_max"]), (879, 333, 552))
        self.assertEqual(focus["feedback"], "LOW_AEROBIC_OVERAGE")

    def test_health_status_ordered_like_garmin_and_counts_outliers(self):
        for metric, value, status in (("HRV", 63, "IN_RANGE"), ("SPO2", 88, "BELOW_RANGE"),
                                      ("HR", 50, "IN_RANGE"), ("RESPIRATION", 14.8, "IN_RANGE")):
            self.conn.execute(
                "INSERT INTO health_status (athlete_id, date, metric, value, baseline_lower, "
                "baseline_upper, status, percentage, source, fetched_at) "
                "VALUES (1, '2026-10-21', ?, ?, 0, 100, ?, 50, 'garmin_export', 'x')",
                (metric, value, status))
        card = garmin_cards.get_garmin_cards(self.conn, 1, "7d")["cards"]["health_status"]
        self.assertEqual([m["metric"] for m in card["metrics"]], ["HR", "HRV", "RESPIRATION", "SPO2"])
        self.assertEqual(card["outliers"], 1)

    def test_cards_without_data_are_omitted(self):
        self.assertNotIn("calories", self.cards)
        self.assertNotIn("lactate_threshold", self.cards)


if __name__ == "__main__":
    unittest.main()
