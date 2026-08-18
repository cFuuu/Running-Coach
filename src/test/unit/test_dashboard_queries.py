"""dashboard_queries 的單元測試。

全部使用 fixtures.build_fixture_db() 建立的**合成假資料**臨時 SQLite，
絕不碰使用者真實的 output/running_coach.db（專案規範：個人資料不進版控、不進測試）。
"""

import json
import tempfile
import unittest
from pathlib import Path

from src.main.python.services import dashboard_queries as queries
from src.main.python.services import garmin_export_parser as parser
from src.test.unit.fixtures import FAKE_ATHLETE_NAME, build_fixture_db


class DashboardQueryTestCase(unittest.TestCase):
    """共用 setUp：每個測試各自建一個臨時資料庫，測完刪掉。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = build_fixture_db(Path(self._tmp.name) / "test.db")
        self.addCleanup(self.conn.close)
        self.athlete_id = queries.resolve_athlete_id(self.conn)


class TestAthleteResolution(DashboardQueryTestCase):
    def test_resolve_returns_first_athlete_when_not_specified(self):
        self.assertEqual(queries.resolve_athlete_id(self.conn), self.athlete_id)

    def test_resolve_passes_through_explicit_id(self):
        self.assertEqual(queries.resolve_athlete_id(self.conn, 99), 99)


class TestRangeResolution(DashboardQueryTestCase):
    def test_30d_window_ends_at_latest_data_date(self):
        window = queries.resolve_range(self.conn, self.athlete_id, "30d")
        self.assertEqual(window["end_date"], "2026-03-10")
        # 含當日往回推 30 天
        self.assertEqual(window["start_date"], "2026-02-09")

    def test_7d_window_boundary(self):
        window = queries.resolve_range(self.conn, self.athlete_id, "7d")
        self.assertEqual(window["start_date"], "2026-03-04")

    def test_all_range_has_no_start_date(self):
        window = queries.resolve_range(self.conn, self.athlete_id, "all")
        self.assertIsNone(window["start_date"])

    def test_invalid_range_raises(self):
        with self.assertRaises(ValueError):
            queries.resolve_range(self.conn, self.athlete_id, "42d")

    # --- 自訂區間（custom:YYYY-MM-DD:YYYY-MM-DD） ---

    def test_custom_range_parses_start_and_end(self):
        window = queries.resolve_range(self.conn, self.athlete_id, "custom:2025-01-01:2025-01-31")
        self.assertEqual(window["start_date"], "2025-01-01")
        self.assertEqual(window["end_date"], "2025-01-31")
        self.assertEqual(window["range"], "custom:2025-01-01:2025-01-31")

    def test_custom_range_does_not_anchor_to_latest_data_date(self):
        """自訂區間直接用使用者指定的日期，不套用「錨定資料庫最新日」規則——
        fixture 的最新資料日是 2026-03-10，這裡指定的區間完全不相關，
        end_date 應原樣是使用者指定的日期，不會被拉到 2026-03-10。"""
        window = queries.resolve_range(self.conn, self.athlete_id, "custom:2020-06-01:2020-06-07")
        self.assertEqual(window["end_date"], "2020-06-07")

    def test_custom_range_start_after_end_raises(self):
        with self.assertRaises(ValueError):
            queries.resolve_range(self.conn, self.athlete_id, "custom:2025-02-01:2025-01-01")

    def test_custom_range_malformed_date_raises(self):
        with self.assertRaises(ValueError):
            queries.resolve_range(self.conn, self.athlete_id, "custom:not-a-date:2025-01-01")

    def test_custom_range_wrong_part_count_raises(self):
        with self.assertRaises(ValueError):
            queries.resolve_range(self.conn, self.athlete_id, "custom:2025-01-01")

    def test_is_valid_range_accepts_enum_and_custom(self):
        self.assertTrue(queries.is_valid_range("30d"))
        self.assertTrue(queries.is_valid_range("all"))
        self.assertTrue(queries.is_valid_range("custom:2025-01-01:2025-01-31"))

    def test_is_valid_range_rejects_bad_values(self):
        self.assertFalse(queries.is_valid_range("42d"))
        self.assertFalse(queries.is_valid_range("custom:2025-02-01:2025-01-01"))
        self.assertFalse(queries.is_valid_range("custom:garbage"))

    def test_parse_range_returns_none_for_non_custom(self):
        """parse_range() 對非 custom 前綴回傳 None，讓 resolve_range() 走既有
        RANGE_DAYS 路徑——這個分界是既有 4 個 range 測試不需修改的關鍵。"""
        self.assertIsNone(queries.parse_range("30d"))
        self.assertIsNone(queries.parse_range("all"))


class TestMeta(DashboardQueryTestCase):
    def test_meta_returns_athlete_and_coverage(self):
        meta = queries.get_meta(self.conn)
        self.assertEqual(meta["athlete"]["name"], FAKE_ATHLETE_NAME)
        self.assertEqual(meta["metric_coverage"]["hrv_ms"]["earliest_date"], "2026-02-20")
        self.assertIn("notice", meta)

    def test_meta_ranges_is_single_source_of_truth_for_frontend(self):
        """前端依此動態產生 range 按鈕，不再各自硬編碼一份清單。"""
        meta = queries.get_meta(self.conn)
        keys = [r["key"] for r in meta["ranges"]]
        self.assertEqual(keys, list(queries.RANGE_DAYS.keys()))
        for r in meta["ranges"]:
            self.assertTrue(r["label"])  # 每個 key 都要有對應的顯示標籤

    def test_meta_data_bounds_spans_all_metrics(self):
        """data_bounds 供前端自訂日期選擇器的 min/max，取全部指標的聯集。"""
        meta = queries.get_meta(self.conn)
        self.assertIsNotNone(meta["data_bounds"])
        self.assertIn("earliest_date", meta["data_bounds"])
        self.assertIn("latest_date", meta["data_bounds"])


class TestListSessions(DashboardQueryTestCase):
    def test_only_running_activities_are_listed(self):
        result = queries.list_sessions(self.conn, range_key="all")
        titles = [s["title"] for s in result["sessions"]]
        self.assertNotIn("測試重訓", titles)  # strength_training 不算跑步場次

    def test_sessions_sorted_newest_first(self):
        result = queries.list_sessions(self.conn, range_key="all")
        dates = [s["started_at"] for s in result["sessions"]]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_range_filter_excludes_activity_outside_window(self):
        """activity 500（2026-01-15）在 30d 視窗外，在 all 視窗內。"""
        in_30d = [s["title"] for s in queries.list_sessions(self.conn, range_key="30d")["sessions"]]
        in_all = [s["title"] for s in queries.list_sessions(self.conn, range_key="all")["sessions"]]
        self.assertNotIn("測試舊資料跑", in_30d)
        self.assertIn("測試舊資料跑", in_all)

    def test_range_boundary_is_inclusive(self):
        """90d 視窗起日為 2025-12-11，2026-01-15 那場應該被包含。"""
        window = queries.resolve_range(self.conn, self.athlete_id, "90d")
        self.assertLess(window["start_date"], "2026-01-15")
        titles = [s["title"] for s in queries.list_sessions(self.conn, range_key="90d")["sessions"]]
        self.assertIn("測試舊資料跑", titles)

    def test_session_row_has_date_derived_from_started_at(self):
        session = queries.list_sessions(self.conn, range_key="30d")["sessions"][0]
        self.assertEqual(session["date"], session["started_at"][:10])

    def test_custom_range_filters_sessions_end_to_end(self):
        """自訂區間精確框住 activity 500（2026-01-15，30d/all 已在上面測過），
        驗證 custom: 前綴從 resolve_range() 到 list_sessions() 整條路徑接通。"""
        titles = [
            s["title"]
            for s in queries.list_sessions(self.conn, range_key="custom:2026-01-10:2026-01-20")["sessions"]
        ]
        self.assertIn("測試舊資料跑", titles)
        # 區間外的場次（例如落在 30d 視窗內的那場）不應出現
        in_30d_only = [
            s["title"] for s in queries.list_sessions(self.conn, range_key="30d")["sessions"]
        ]
        for title in in_30d_only:
            self.assertNotIn(title, titles)


class TestSessionDetail(DashboardQueryTestCase):
    def _detail(self, title: str) -> dict:
        for s in queries.list_sessions(self.conn, range_key="all")["sessions"]:
            if s["title"] == title:
                return queries.get_session_detail(self.conn, s["id"])
        raise AssertionError(f"fixture 裡找不到標題為 {title} 的活動")

    def test_missing_session_returns_none(self):
        self.assertIsNone(queries.get_session_detail(self.conn, 999999))

    def test_summary_and_planned_are_populated(self):
        detail = self._detail("測試晨跑")
        self.assertEqual(detail["summary"]["distance_km"], 10.0)
        self.assertEqual(detail["workout_type"], "easy")
        # 同一天有計畫課表（lsd）→ 可與實際的 easy 對照出課表遵從度
        self.assertEqual(detail["planned"]["workout_type"], "lsd")

    def test_planned_is_none_when_no_plan_that_day(self):
        self.assertIsNone(self._detail("測試手動分圈跑")["planned"])

    # --- 分圈來源優先序 ---

    def test_laps_prefer_fit_source(self):
        laps = self._detail("測試晨跑")["laps"]
        self.assertTrue(laps["available"])
        self.assertEqual(laps["source"], "fit")
        self.assertEqual(len(laps["laps"]), 3)
        self.assertEqual(laps["laps"][0]["lap"], 1)

    def test_laps_fall_back_to_manual_splits_and_filter_noise_types(self):
        laps = self._detail("測試手動分圈跑")["laps"]
        self.assertTrue(laps["available"])
        self.assertEqual(laps["source"], "garmin_manual_lap")
        # 5 段裡只有 3 段 type==17；type==3（整場總計）與 type==18 必須被濾掉
        self.assertEqual(len(laps["laps"]), 3)
        self.assertAlmostEqual(laps["laps"][0]["distance_km"], 3.0)
        self.assertEqual(laps["laps"][0]["duration_sec"], 900)
        self.assertEqual(laps["laps"][0]["pace_sec_per_km"], 300)
        self.assertEqual(laps["laps"][1]["pace_sec_per_km"], 310)  # 930s / 3km

    def test_laps_unavailable_when_only_one_manual_lap_remains(self):
        laps = self._detail("測試單段跑")["laps"]
        self.assertFalse(laps["available"])
        self.assertIn("reason", laps)

    def test_laps_unavailable_for_activity_without_any_lap_data(self):
        detail = queries.get_session_detail(
            self.conn,
            self.conn.execute(
                "SELECT id FROM activities WHERE activity_type = 'strength_training'"
            ).fetchone()["id"],
        )
        self.assertFalse(detail["laps"]["available"])
        self.assertFalse(detail["records"]["available"])
        self.assertFalse(detail["hr_drift"]["available"])
        self.assertFalse(detail["hr_zones"]["available"])

    # --- 逐秒與心率漂移 ---

    def test_records_report_sample_interval(self):
        records = self._detail("測試晨跑")["records"]
        self.assertTrue(records["available"])
        self.assertEqual(records["sample_every_sec"], 10)
        self.assertEqual(len(records["points"]), 4)

    def test_hr_drift_is_computed_from_records(self):
        """前半心率 100、後半 110 → 漂移 +10.0%。"""
        drift = self._detail("測試晨跑")["hr_drift"]
        self.assertTrue(drift["available"])
        self.assertEqual(drift["first_half_avg_hr"], 100.0)
        self.assertEqual(drift["second_half_avg_hr"], 110.0)
        self.assertEqual(drift["drift_pct"], 10.0)

    def test_hr_drift_unavailable_without_records(self):
        drift = self._detail("測試手動分圈跑")["hr_drift"]
        self.assertFalse(drift["available"])
        self.assertIn("reason", drift)

    # --- 心率區間 ---
    # fixture 原始資料：zone0=300s zone1=600s zone2=1800s zone3=0s zone4=300s
    # zone5=0s zone6=0s（total 以 zone0~5 計=3000s，zone6 恆為裝置 padding
    # 不計入）。zone0 是暖身、zone3/zone5 皆為 0。

    def test_hr_zones_always_returns_fixed_z1_to_z5(self):
        """跨場次比較要求圖表軸線一致：zones 固定回傳 Z1~Z5 共 5 筆，
        不管該場實際進過幾個區間，沒進過的區間 seconds=0 照樣列出，
        不會因為某場沒進某區間就少一列（例如這裡的 zone3/zone5）。"""
        zones = self._detail("測試晨跑")["hr_zones"]
        self.assertTrue(zones["available"])
        self.assertEqual([z["zone"] for z in zones["zones"]], [1, 2, 3, 4, 5])
        self.assertEqual(zones["zones"][2]["seconds"], 0)  # zone 3
        self.assertEqual(zones["zones"][4]["seconds"], 0)  # zone 5

    def test_hr_zones_excludes_zone_6_device_padding(self):
        """zone 6 是 Garmin 裝置對「超過最高區間」固定產生的 padding 桶位
        （實測 266 場全部恆為 0），不在 5 列固定軸線之列，也不計入分母。"""
        zones = self._detail("測試晨跑")["hr_zones"]
        self.assertNotIn(6, [z["zone"] for z in zones["zones"]])
        self.assertEqual(zones["total_seconds"], 3000)  # 不含 zone6 的 0

    def test_hr_zones_below_zone_1_reported_separately(self):
        """zone 0（低於 Z1 的暖身）獨立回傳，不混進 zones 長條圖。"""
        zones = self._detail("測試晨跑")["hr_zones"]
        self.assertEqual(zones["below_zone_1"]["seconds"], 300)

    def test_hr_zones_pct_denominator_includes_below_zone_1(self):
        """百分比分母須含暖身時間，否則各區間佔比會虛增。"""
        zones = self._detail("測試晨跑")["hr_zones"]
        self.assertEqual(zones["total_seconds"], 3000)
        self.assertAlmostEqual(zones["below_zone_1"]["pct"], 10.0)
        # zone 2：1800/3000 = 60%
        zone2 = next(z for z in zones["zones"] if z["zone"] == 2)
        self.assertAlmostEqual(zone2["pct"], 60.0)

    def test_hr_zones_unavailable_without_raw_json(self):
        zones = self._detail("測試手動分圈跑")["hr_zones"]
        self.assertFalse(zones["available"])

    def test_hr_zones_all_below_zone_1_still_shows_fixed_axis(self):
        """實測真實資料中，健走/重訓等低強度活動可能整場都在 Zone 1 以下
        （12 場抽樣中 6 場如此）。這種情況 available 仍為 True，zones 仍固定
        回傳 Z1~Z5（全部 seconds=0），below_zone_1 佔 100%——前端不能因為
        zones 全 0 就顯示「無資料」。"""
        result = queries._build_hr_zones(
            json.dumps({"hrTimeInZone_0": 1800000.0, "hrTimeInZone_1": 0.0})
        )
        self.assertTrue(result["available"])
        self.assertEqual([z["zone"] for z in result["zones"]], [1, 2, 3, 4, 5])
        self.assertTrue(all(z["seconds"] == 0 for z in result["zones"]))
        self.assertEqual(result["below_zone_1"]["seconds"], 1800)
        self.assertAlmostEqual(result["below_zone_1"]["pct"], 100.0)


class TestWellnessTrend(DashboardQueryTestCase):
    def test_all_eight_metrics_present_including_new_columns(self):
        metrics = queries.get_wellness_trend(self.conn, range_key="30d")["metrics"]
        for name in (
            "hrv_ms",
            "resting_hr_bpm",
            "spo2_pct",
            "sleep_score",
            "training_readiness_score",
            "stress_avg",
            "all_day_stress_avg",
            "steps",
        ):
            self.assertIn(name, metrics)

    def test_body_battery_never_exposed(self):
        metrics = queries.get_wellness_trend(self.conn, range_key="all")["metrics"]
        self.assertNotIn("body_battery_max", metrics)
        self.assertNotIn("body_battery_min", metrics)

    def test_stress_and_all_day_stress_stay_separate(self):
        metrics = queries.get_wellness_trend(self.conn, range_key="30d")["metrics"]
        stress = {p["date"]: p["value"] for p in metrics["stress_avg"]["points"]}
        all_day = {p["date"]: p["value"] for p in metrics["all_day_stress_avg"]["points"]}
        self.assertEqual(stress["2026-03-10"], 25)
        self.assertEqual(all_day["2026-03-10"], 35)

    def test_missing_values_are_omitted_not_zero_filled(self):
        """2026-03-07 的 hrv_ms 是 NULL——該日期不該出現在 points 裡，更不該是 0。"""
        metrics = queries.get_wellness_trend(self.conn, range_key="30d")["metrics"]
        hrv_dates = [p["date"] for p in metrics["hrv_ms"]["points"]]
        self.assertNotIn("2026-03-07", hrv_dates)
        self.assertNotIn(0, [p["value"] for p in metrics["hrv_ms"]["points"]])
        # 同一天的 resting_hr_bpm 有值，證明整列沒有被丟掉，只是缺的那個欄位不出現
        rhr_dates = [p["date"] for p in metrics["resting_hr_bpm"]["points"]]
        self.assertIn("2026-03-07", rhr_dates)

    def test_clipped_false_when_range_starts_after_coverage(self):
        """30d 起日 2026-02-09 晚於 hrv 起日 2026-02-20？否——2/9 早於 2/20，故 clipped。"""
        metrics = queries.get_wellness_trend(self.conn, range_key="7d")["metrics"]
        # 7d 起日 2026-03-04，晚於 hrv coverage 起日 2026-02-20 → 不 clipped
        self.assertFalse(metrics["hrv_ms"]["clipped"])

    def test_clipped_true_when_range_predates_coverage(self):
        metrics = queries.get_wellness_trend(self.conn, range_key="90d")["metrics"]
        # 90d 起日 2025-12-11 早於 hrv coverage 起日 2026-02-20 → clipped
        self.assertTrue(metrics["hrv_ms"]["clipped"])
        # resting_hr_bpm 從 2020 就有資料，同一個視窗不該被標成 clipped
        self.assertFalse(metrics["resting_hr_bpm"]["clipped"])

    def test_range_all_marks_metric_with_late_start_as_clipped(self):
        metrics = queries.get_wellness_trend(self.conn, range_key="all")["metrics"]
        self.assertTrue(metrics["hrv_ms"]["clipped"])

    def test_metric_without_data_reports_unavailable_with_reason(self):
        """7d 視窗內完全沒有的指標應該 available:false 並附原因，而非空陣列了事。"""
        self.conn.execute("UPDATE daily_wellness SET spo2_pct = NULL")
        self.conn.commit()
        metrics = queries.get_wellness_trend(self.conn, range_key="30d")["metrics"]
        self.assertFalse(metrics["spo2_pct"]["available"])
        self.assertIn("reason", metrics["spo2_pct"])
        self.assertEqual(metrics["spo2_pct"]["points"], [])


class TestTrainingDays(DashboardQueryTestCase):
    def test_training_days_are_unique_sorted_dates_in_range(self):
        result = queries.get_training_days(self.conn, range_key="30d")
        self.assertEqual(
            result["training_days"], ["2026-03-02", "2026-03-03", "2026-03-05", "2026-03-09"]
        )

    def test_training_days_respect_range(self):
        result = queries.get_training_days(self.conn, range_key="all")
        self.assertIn("2026-01-15", result["training_days"])


class TestRecoveryImpact(DashboardQueryTestCase):
    def _impact_for(self, training_date: str) -> dict:
        impacts = queries.get_recovery_impact(self.conn, range_key="30d")["impacts"]
        for impact in impacts:
            if impact["training_date"] == training_date:
                return impact
        raise AssertionError(f"找不到 {training_date} 的恢復關聯資料")

    def test_delta_is_next_day_minus_training_day(self):
        """2026-03-09 hrv 50 → 2026-03-10 hrv 45：delta -5.0、-10.0%。"""
        impact = self._impact_for("2026-03-09")
        next_day = impact["next_day"]
        self.assertTrue(next_day["available"])
        self.assertEqual(next_day["date"], "2026-03-10")
        self.assertEqual(next_day["hrv_delta"], -5.0)
        self.assertEqual(next_day["hrv_delta_pct"], -10.0)
        self.assertEqual(next_day["resting_hr_delta"], 3)  # 50 -> 53
        self.assertEqual(next_day["training_readiness_delta"], -10)  # 80 -> 70

    def test_next_day_unavailable_when_no_wellness_row(self):
        """2026-03-05 的隔天 2026-03-06 完全沒有 daily_wellness 列。"""
        next_day = self._impact_for("2026-03-05")["next_day"]
        self.assertFalse(next_day["available"])
        self.assertIn("reason", next_day)
        self.assertEqual(next_day["date"], "2026-03-06")
        # 不可用 0 假裝有資料
        self.assertNotIn("hrv_delta", next_day)

    def test_null_metric_produces_none_delta_not_zero(self):
        """單一指標缺值時 delta 應為 None（不可當成 0 去相減），其他指標不受影響。"""
        self.conn.execute("UPDATE daily_wellness SET hrv_ms = NULL WHERE date = '2026-03-10'")
        self.conn.commit()
        next_day = self._impact_for("2026-03-09")["next_day"]
        self.assertTrue(next_day["available"])
        self.assertIsNone(next_day["hrv_delta"])
        self.assertIsNone(next_day["hrv_delta_pct"])
        self.assertEqual(next_day["resting_hr_delta"], 3)

    def test_next_day_unavailable_when_training_day_has_no_wellness(self):
        """訓練當天沒有 wellness 列時，沒有基準可比較，應 available:false 附原因。

        fixture 的 2026-03-03 那場：隔天 2026-03-04 有 wellness，但訓練當天沒有。
        """
        next_day = self._impact_for("2026-03-03")["next_day"]
        self.assertFalse(next_day["available"])
        self.assertIn("reason", next_day)

    def test_non_running_activities_are_included(self):
        """重訓一樣會影響隔天身體狀況，不該被排除在恢復關聯之外。"""
        impact = self._impact_for("2026-03-02")
        self.assertEqual(impact["activity_type"], "strength_training")

    def test_impacts_respect_range(self):
        dates = [i["training_date"] for i in queries.get_recovery_impact(self.conn, range_key="30d")["impacts"]]
        self.assertNotIn("2026-01-15", dates)


class TestNoFastapiDependency(unittest.TestCase):
    def test_query_layer_does_not_import_fastapi(self):
        """查詢層必須維持純粹，才能在沒有 web 框架的情境下重用（也才好測）。

        只看真正的 import 陳述句（行首），不掃全文——註解裡提到 fastapi 三個字
        是說明用意，不代表有相依。
        """
        import_lines = [
            line.strip()
            for line in Path(queries.__file__).read_text(encoding="utf-8").splitlines()
            if line.startswith(("import ", "from "))
        ]
        self.assertTrue(import_lines)  # 確保真的有掃到 import 行，避免測試空轉
        for line in import_lines:
            self.assertNotIn("fastapi", line)


class TestUnitConversionHelpersArePublic(unittest.TestCase):
    """換算函式改為公開後仍須維持原本經真實資料驗證過的行為。"""

    def test_conversions_round_trip_known_values(self):
        self.assertAlmostEqual(parser.cm_to_km(1010886.03), 10.1089, places=4)
        self.assertAlmostEqual(parser.cm_to_m(500.0), 5.0)
        self.assertEqual(parser.ms_to_sec(3149739.01), 3150)
        self.assertEqual(parser.kj_to_kcal(418.4), 100)
        self.assertEqual(parser.speed_cm_per_ms_to_pace_sec_per_km(0.4), 250)

    def test_conversions_pass_none_through(self):
        self.assertIsNone(parser.cm_to_km(None))
        self.assertIsNone(parser.cm_to_m(None))
        self.assertIsNone(parser.ms_to_sec(None))
        self.assertIsNone(parser.kj_to_kcal(None))
        self.assertIsNone(parser.speed_cm_per_ms_to_pace_sec_per_km(None))
        self.assertIsNone(parser.speed_cm_per_ms_to_pace_sec_per_km(0))


if __name__ == "__main__":
    unittest.main()
