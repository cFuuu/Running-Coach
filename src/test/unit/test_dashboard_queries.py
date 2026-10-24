"""dashboard_queries 的單元測試。

全部使用 fixtures.build_fixture_db() 建立的**合成假資料**臨時 SQLite，
絕不碰使用者真實的 output/running_coach.db（專案規範：個人資料不進版控、不進測試）。
"""

import datetime
import json
import tempfile
import unittest
from pathlib import Path

from src.main.python.models.db import get_connection
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

    def test_meta_exposes_hrmax_with_confidence(self):
        """HRmax 必須帶信賴度傳到判斷層（PLAN.md §7.3 三欄位契約）。

        過去 get_meta 只回傳 {id, name}，導致 max_hr_source 這個標記
        存在資料庫卻傳不出去，AI 無從得知以 %HRR 推算的一切都帶著
        同一個系統性偏移。fixture 未設來源 → 應保守標為未實測。
        """
        athlete = queries.get_meta(self.conn)["athlete"]
        self.assertEqual(set(athlete["max_hr"]), {"value", "source", "confidence"})
        self.assertEqual(athlete["max_hr"]["confidence"]["level"], "low")
        self.assertTrue(athlete["max_hr"]["confidence"]["biases"])

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

    def test_meta_wellness_metric_defs_is_single_source_of_truth(self):
        """前端刪掉了自己的 WELLNESS_METRICS 常數，改由這裡驅動渲染順序、
        標籤、單位、小數位與顯隱預設值。"""
        meta = queries.get_meta(self.conn)
        defs = meta["wellness_metric_defs"]
        self.assertEqual(len(defs), 13)
        keys = [d["key"] for d in defs]
        self.assertEqual(keys, list(queries.WELLNESS_METRICS))
        for d in defs:
            self.assertTrue(d["label"])
            self.assertIn(d["format"], ("number", "duration"))
            self.assertIsInstance(d["default_hidden"], bool)

    def test_meta_wellness_metric_defs_marks_respiration_rate_hidden_by_default(self):
        """respiration_rate 涵蓋率僅 17.6%，預設隱藏但仍可選（不是排除）。"""
        meta = queries.get_meta(self.conn)
        defs_by_key = {d["key"]: d for d in meta["wellness_metric_defs"]}
        self.assertTrue(defs_by_key["respiration_rate"]["default_hidden"])
        self.assertFalse(defs_by_key["hrv_ms"]["default_hidden"])

    def test_meta_wellness_metric_defs_excludes_body_battery_and_skin_temp(self):
        meta = queries.get_meta(self.conn)
        keys = [d["key"] for d in meta["wellness_metric_defs"]]
        self.assertNotIn("body_battery_max", keys)
        self.assertNotIn("body_battery_min", keys)
        self.assertNotIn("skin_temp_c", keys)


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


class TestLapStrip(DashboardQueryTestCase):
    """N-29 B1：/api/sessions 每場附 lap_strip。

    手錶設定 [0.54, 0.59, 0.74, 0.83, 0.88, 1.0]、靜止 56、HRmax 195 換算成 bpm：
    Z1 131–138、Z2 138–159、Z3 159–171、Z4 171–178、Z5 178–195。
    """

    def _set_watch_zones(self):
        self.conn.execute(
            "UPDATE athlete_profile SET max_hr_bpm = 195, resting_hr_bpm = 56, "
            "hr_zone_hrr_bounds_json = '[0.54, 0.59, 0.74, 0.83, 0.88, 1.0]'"
        )

    def _sessions(self, **kwargs) -> dict:
        result = queries.list_sessions(self.conn, range_key="all", **kwargs)
        return {s["title"]: s for s in result["sessions"]}

    def _replace_laps(self, title: str, laps: list[tuple[int, int | None]]):
        """把某場的分圈換成指定的 (lap_index, avg_hr_bpm)；配速設成 300 + lap_index。"""
        session_id = self._sessions()[title]["id"]
        self.conn.execute("DELETE FROM activity_laps WHERE activity_id = ?", (session_id,))
        for lap_index, hr in laps:
            self.conn.execute(
                "INSERT INTO activity_laps (activity_id, lap_index, distance_km, duration_sec, "
                "pace_sec_per_km, avg_hr_bpm) VALUES (?, ?, 1.0, 300.0, ?, ?)",
                (session_id, lap_index, 300 + lap_index, hr),
            )

    def test_lap_strip_absent_by_default(self):
        """MCP 的 list_running_sessions 走預設值，不得多出 lap_strip。"""
        for session in self._sessions().values():
            self.assertNotIn("lap_strip", session)

    def test_lap_strip_lists_fit_laps_in_lap_order(self):
        self._set_watch_zones()
        # 刻意倒序插入，驗證排序依 lap_index 而非寫入順序
        self._replace_laps("測試晨跑", [(3, 172), (1, 140), (2, 160)])
        strip = self._sessions(include_lap_strip=True)["測試晨跑"]["lap_strip"]
        self.assertEqual(
            strip,
            [
                {"pace_sec_per_km": 301, "avg_hr_bpm": 140, "zone": 2},
                {"pace_sec_per_km": 302, "avg_hr_bpm": 160, "zone": 3},
                {"pace_sec_per_km": 303, "avg_hr_bpm": 172, "zone": 4},
            ],
        )

    def test_zone_boundaries_lower_inclusive_and_out_of_range(self):
        self._set_watch_zones()
        laps = [130, 131, 137, 138, 158, 159, 171, 178, 195, 200]
        self._replace_laps("測試晨跑", list(enumerate(laps, start=1)))
        zones = [lap["zone"] for lap in self._sessions(include_lap_strip=True)["測試晨跑"]["lap_strip"]]
        # 低於 Z1 → 0（與 hr_zones 的 below_zone_1 同義）；邊界值歸較高區；高於 HRmax 仍是 Z5
        self.assertEqual(zones, [0, 1, 1, 2, 2, 3, 4, 5, 5, 5])

    def test_zone_null_without_watch_settings(self):
        """沒設定手錶區間就不換算，絕不套預設區間；配速與心率照給。"""
        strip = self._sessions(include_lap_strip=True)["測試晨跑"]["lap_strip"]
        self.assertEqual(len(strip), 3)
        self.assertTrue(all(lap["zone"] is None for lap in strip))
        self.assertEqual([lap["avg_hr_bpm"] for lap in strip], [146, 147, 148])

    def test_zone_null_when_lap_has_no_hr(self):
        self._set_watch_zones()
        self._replace_laps("測試晨跑", [(1, None), (2, 150)])
        strip = self._sessions(include_lap_strip=True)["測試晨跑"]["lap_strip"]
        self.assertEqual([(lap["avg_hr_bpm"], lap["zone"]) for lap in strip], [(None, None), (150, 2)])

    def test_session_without_fit_laps_gets_empty_list(self):
        """activity 200 只有手動分段：lap_strip 只取 FIT 分圈，不退回手動分段。"""
        self.assertEqual(self._sessions(include_lap_strip=True)["測試手動分圈跑"]["lap_strip"], [])

    def test_laps_fetched_in_single_query(self):
        """不能每場查一次（N+1）：整份清單只查一次 activity_laps。"""
        statements: list[str] = []
        self.conn.set_trace_callback(statements.append)
        self.addCleanup(self.conn.set_trace_callback, None)
        self._sessions(include_lap_strip=True)
        self.assertEqual(sum("activity_laps" in s for s in statements), 1)

    def test_dashboard_route_includes_lap_strip(self):
        """開關只在 dashboard 路由打開。"""
        from fastapi.testclient import TestClient

        from src.main.python.api.app import create_app
        from src.main.python.services import coach_chat

        self.conn.commit()
        assets_dir = Path(self._tmp.name) / "assets"
        assets_dir.mkdir()
        app = create_app(
            str(Path(self._tmp.name) / "test.db"),
            assets_dir=assets_dir,
            chat_config=coach_chat.ChatConfig(api_key_present=False),
        )
        sessions = TestClient(app).get("/api/sessions", params={"range": "all"}).json()["sessions"]
        by_title = {s["title"]: s for s in sessions}
        self.assertEqual(len(by_title["測試晨跑"]["lap_strip"]), 3)
        self.assertEqual(by_title["測試手動分圈跑"]["lap_strip"], [])


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

    def test_labeled_workout_blocks_nothing(self):
        detail = self._detail("測試晨跑")  # workout_type = easy
        self.assertEqual(detail["assessability"]["blocked"], [])

    def test_unknown_workout_type_blocks_pace_execution(self):
        """workout_type 為 unknown 時不得評配速執行度（PLAN.md §7.4）——
        unknown 是分類器失敗而非某個課種，拿 easy 的標準評一場其實是
        間歇的課會得到完全錯誤的結論。
        """
        session_id = self._detail("測試晨跑")["id"]
        self.conn.execute(
            "UPDATE activities SET workout_type = 'unknown' WHERE id = ?", (session_id,)
        )
        blocked = queries.get_session_detail(self.conn, session_id)["assessability"]["blocked"]
        self.assertEqual(len(blocked), 1)
        self.assertIn("pace_execution", blocked[0]["dimensions"])
        self.assertEqual(blocked[0]["bias_code"], "workout_type_unknown")

    def test_planned_is_none_when_no_plan_that_day(self):
        detail = self._detail("測試手動分圈跑")
        self.assertIsNone(detail["planned"])
        self.assertEqual(detail["planned_match"]["state"], "none")

    def test_inferred_plan_carries_low_confidence(self):
        """推論而來的計畫對應必須帶信賴度標記（PLAN.md §7.3）——
        目前 linked_activity_id 全為 NULL，故所有對應都是推論的。
        """
        detail = self._detail("測試晨跑")
        self.assertEqual(detail["planned_match"]["state"], "inferred")
        self.assertEqual(detail["planned_match"]["method"], "date_and_sport")
        self.assertEqual(detail["planned"]["confidence"]["level"], "low")
        codes = [b["code"] for b in detail["planned"]["confidence"]["biases"]]
        self.assertIn("plan_match_inferred", codes)

    def test_incompatible_activity_type_gets_no_plan(self):
        """走路那場不得配到當天的跑步課表——舊邏輯只比日期會配上去。"""
        session_id = self._detail("測試晨跑")["id"]
        self.conn.execute(
            "UPDATE activities SET activity_type = 'walking' WHERE id = ?", (session_id,)
        )
        detail = queries.get_session_detail(self.conn, session_id)
        self.assertIsNone(detail["planned"])
        self.assertEqual(detail["planned_match"]["reason"], "no_compatible_plan")

    def test_two_runs_one_plan_reports_ambiguous_not_a_guess(self):
        """同日兩場跑步搶一份課表時回報 ambiguous，而非任選一場宣稱執行了它。"""
        detail = self._detail("測試晨跑")
        session_id = detail["id"]
        row = self.conn.execute(
            "SELECT athlete_id, started_at, activity_type FROM activities WHERE id = ?",
            (session_id,),
        ).fetchone()
        self.conn.execute(
            "INSERT INTO activities (athlete_id, source, source_version, fetched_at, "
            "activity_type, title, started_at, distance_km, duration_sec) "
            "VALUES (?, 'garmin_export', 'test', '2026-03-10T00:00:00', ?, ?, ?, ?, ?)",
            (
                row["athlete_id"],
                row["activity_type"],
                "同日第二場跑步",
                # 同一天、不同時間；日期相同才會構成搶同一份課表
                row["started_at"][:10] + "T19:30:00",
                5.0,
                1800,
            ),
        )
        detail = queries.get_session_detail(self.conn, session_id)
        self.assertIsNone(detail["planned"])
        self.assertEqual(detail["planned_match"]["state"], "ambiguous")
        self.assertEqual(
            detail["planned_match"]["reason"], "multiple_activities_for_one_plan"
        )

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
        """暖身 10 分鐘後的 50 分鐘穩定段：前半心率 100、後半 110 → 漂移 +10.0%。

        共用 fixture 的逐秒資料只有 40 秒（其點數與取樣間隔另有測試斷言，
        不動它），而心率漂移要求暖身後至少 30 分鐘，故這裡就地補上一場
        夠長的活動來驗證計算本身。
        """
        session_id = self._detail("測試晨跑")["id"]
        self.conn.execute("DELETE FROM activity_records WHERE activity_id = ?", (session_id,))
        for elapsed in range(0, 3601, 10):
            self.conn.execute(
                "INSERT INTO activity_records "
                "(activity_id, elapsed_sec, distance_km, hr_bpm, pace_sec_per_km) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, elapsed, elapsed / 300, 100 if elapsed < 2100 else 110, 300),
            )
        drift = queries.get_session_detail(self.conn, session_id)["hr_drift"]
        self.assertTrue(drift["available"])
        self.assertEqual(drift["first_half_avg_hr"], 100.0)
        self.assertEqual(drift["second_half_avg_hr"], 110.0)
        self.assertEqual(drift["drift_pct"], 10.0)
        self.assertEqual(drift["tier"], "standard")

    def test_coords_only_when_requested(self):
        """N-32：Dashboard 要座標畫地圖；MCP 共用同一函式但預設不帶，免得佔 context。"""
        session_id = self._detail("測試晨跑")["id"]
        self.conn.execute(
            "UPDATE activity_records SET lat = 10.0, lon = 100.0 WHERE activity_id = ?",
            (session_id,),
        )
        default = queries.get_session_detail(self.conn, session_id)["records"]["points"][0]
        self.assertNotIn("lat", default)
        point = queries.get_session_detail(self.conn, session_id, include_coords=True)[
            "records"]["points"][0]
        self.assertEqual((point["lat"], point["lon"]), (10.0, 100.0))

    def test_no_coord_fields_when_activity_has_no_gps(self):
        point = self._detail("測試晨跑")  # fixture 沒有座標
        detail = queries.get_session_detail(self.conn, point["id"], include_coords=True)
        self.assertNotIn("lat", detail["records"]["points"][0])

    def test_short_activity_reports_drift_unavailable(self):
        """共用 fixture 的 40 秒活動短於下限，應明確標為不可計算並附理由。"""
        drift = self._detail("測試晨跑")["hr_drift"]
        self.assertFalse(drift["available"])
        self.assertIn("下限", drift["reason"])

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

    def test_hr_zones_export_source_treats_values_as_milliseconds(self):
        """官方匯出包的 hrTimeInZone_N 是毫秒，須除以 1000。"""
        result = queries._build_hr_zones(
            json.dumps({"hrTimeInZone_1": 203994.0, "hrTimeInZone_2": 5197439.0}),
            is_api=False,
        )
        self.assertEqual(result["zones"][0]["seconds"], 204)
        self.assertEqual(result["zones"][1]["seconds"], 5197)

    def test_hr_zones_api_sync_source_treats_values_as_seconds(self):
        """🐛 迴歸測試（2026-09-02 Phase 3 驗收時發現）：API 同步的 payload
        單位是秒，若沿用匯出包的 ms_to_sec() 會把 8/30 那場 5648 秒的長跑
        壓成 5 秒——實際發生過，且因不是 NULL、不拋錯而完全無聲。"""
        result = queries._build_hr_zones(
            json.dumps(
                {
                    "hrTimeInZone_1": 203.994,
                    "hrTimeInZone_2": 5197.439,
                    "hrTimeInZone_3": 116.002,
                }
            ),
            is_api=True,
        )
        self.assertEqual(result["zones"][0]["seconds"], 204)
        self.assertEqual(result["zones"][1]["seconds"], 5197)
        self.assertEqual(result["zones"][2]["seconds"], 116)
        self.assertEqual(result["total_seconds"], 5517)

    def test_hr_zones_api_sync_still_counts_zone_0_in_denominator(self):
        """換單位不得順手改掉分母規則：Z0 仍須計入，否則各區間佔比虛增
        （2026-08-18 改版 Phase 2 修過的 bug，不可在此回歸）。"""
        result = queries._build_hr_zones(
            json.dumps({"hrTimeInZone_0": 300.0, "hrTimeInZone_2": 2700.0}),
            is_api=True,
        )
        self.assertEqual(result["total_seconds"], 3000)
        self.assertEqual(result["below_zone_1"]["seconds"], 300)
        self.assertAlmostEqual(result["below_zone_1"]["pct"], 10.0)
        zone2 = next(z for z in result["zones"] if z["zone"] == 2)
        self.assertAlmostEqual(zone2["pct"], 90.0)

    def test_get_session_detail_wires_is_api_from_source_version(self):
        """端到端：`get_session_detail` 必須依該列的 source_version 決定單位。
        單元層測對了但沒把旗標接上，正是 2026-09-02 那個 bug 的形狀——寫入端
        修好了、讀取端沒跟上，中間沒有任何測試涵蓋這條接線。"""
        self.conn.execute(
            """
            INSERT INTO activities (
                athlete_id, source, source_version, fetched_at, external_id,
                activity_type, title, started_at, distance_km, duration_sec,
                raw_data_json
            ) VALUES (?, 'garmin_export', 'api_sync', '2026-03-10T00:00:00',
                      '99999999', 'running', '測試API同步跑',
                      '2026-03-10T18:00:00', 15.1, 5648, ?)
            """,
            (
                self.athlete_id,
                json.dumps({"hrTimeInZone_1": 203.994, "hrTimeInZone_2": 5197.439}),
            ),
        )
        self.conn.commit()

        zones = self._detail("測試API同步跑")["hr_zones"]
        self.assertTrue(zones["available"])
        self.assertEqual(zones["zones"][1]["seconds"], 5197)
        # 修正前這裡會是 5——與該場 5648 秒的實際時長差三個數量級
        self.assertEqual(zones["total_seconds"], 5401)

    def test_api_sync_source_version_matches_sync_runner(self):
        """`dashboard_queries` 刻意重寫這個字面值而不 import 同步層（避免把
        garmin_api_client／garmin_auth 的網路相依拖進純查詢層），故兩邊一致性
        由本測試強制。同步層改了字串而這裡沒跟上時，心率區間會靜默錯 1000 倍。"""
        from src.main.python.services import garmin_sync_runner

        self.assertEqual(
            queries.API_SYNC_SOURCE_VERSION,
            garmin_sync_runner.SYNC_SOURCE_VERSION,
        )


class TestWellnessTrend(DashboardQueryTestCase):
    def test_all_thirteen_metrics_present_including_phase5_columns(self):
        """Phase 5 開放了 5 個原本存在但前端未顯示的指標，加上既有 8 個
        共 13 個。WELLNESS_METRIC_DEFS 是唯一真實來源，這裡直接對照它，
        不重複列一份字串清單（否則兩份清單各自維護又會漂移）。"""
        metrics = queries.get_wellness_trend(self.conn, range_key="30d")["metrics"]
        for name in queries.WELLNESS_METRICS:
            self.assertIn(name, metrics)
        self.assertEqual(len(queries.WELLNESS_METRICS), 13)

    def test_body_battery_and_skin_temp_never_exposed(self):
        """實測 Garmin 匯出包這三欄 100% 為 NULL，畫出來只會是空線，
        契約明文排除，不可因為新增指標而不小心又加回來。"""
        metrics = queries.get_wellness_trend(self.conn, range_key="all")["metrics"]
        self.assertNotIn("body_battery_max", metrics)
        self.assertNotIn("body_battery_min", metrics)
        self.assertNotIn("skin_temp_c", metrics)

    def test_phase5_metrics_have_correct_values(self):
        """交叉比對 fixture 實際寫入的值，確認新指標欄位真的接通 SQL。"""
        metrics = queries.get_wellness_trend(self.conn, range_key="30d")["metrics"]
        sleep_dur = {p["date"]: p["value"] for p in metrics["sleep_duration_sec"]["points"]}
        acwr = {p["date"]: p["value"] for p in metrics["acwr"]["points"]}
        self.assertEqual(sleep_dur["2026-03-10"], 27000)
        self.assertEqual(acwr["2026-03-10"], 1.1)

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


class TestToday(DashboardQueryTestCase):
    """今日卡。fixture 的「今天」固定用 FAKE_TODAY（2026-03-10），
    這天剛好有完整的 daily_wellness 與活動資料。"""

    def test_today_with_full_data_evaluates_all_dimensions(self):
        result = queries.get_today(self.conn, today=datetime.date(2026, 3, 10))
        self.assertEqual(result["date"], "2026-03-10")
        self.assertEqual(result["freshness"]["wellness_latest"], "2026-03-10")
        self.assertEqual(result["freshness"]["days_behind"], 0)
        self.assertIsNotNone(result["readiness"]["today"])
        self.assertIsNotNone(result["load"])
        self.assertIsNotNone(result["hrv"])

    def test_future_date_with_no_data_returns_none_readiness_and_load(self):
        """N-13：完全無資料的日期，readiness.today 與 load 皆為 None，
        不可假裝評估過。"""
        far_future = datetime.date(2026, 6, 1)
        result = queries.get_today(self.conn, today=far_future)
        self.assertIsNone(result["readiness"]["today"]["readiness"])
        self.assertEqual(result["readiness"]["today"]["dimensions_evaluated"], [])
        self.assertIsNone(result["load"])
        self.assertIsNone(result["hrv"])

    def test_latest_evaluable_falls_back_to_most_recent_assessed_day(self):
        """在回溯窗（_READINESS_LOOKBACK_DAYS）內選一個無資料的日期，
        確認 latest_evaluable 能找回窗內最近一個有評估過的日子。"""
        within_lookback_but_no_data = datetime.date(2026, 3, 20)
        result = queries.get_today(self.conn, today=within_lookback_but_no_data)
        self.assertIsNone(result["readiness"]["today"]["readiness"])
        self.assertIsNotNone(result["readiness"]["latest_evaluable"])
        self.assertEqual(result["readiness"]["latest_evaluable"]["date"], "2026-03-10")

    def test_hrv_falls_back_to_yesterday_and_marks_as_of(self):
        """手錶隔天早上才上傳，今天常常還沒有 HRV。此時退回最近一筆，
        但必須用 as_of 標明那是哪一天的資料，不可假裝是今天的。"""
        day_after = datetime.date(2026, 3, 11)  # fixture 最新資料是 3/10
        result = queries.get_today(self.conn, today=day_after)
        self.assertIsNotNone(result["hrv"])
        self.assertEqual(result["hrv"]["as_of"], "2026-03-10")

    def test_hrv_as_of_is_none_when_today_has_its_own_data(self):
        """今天自己就有資料時不該標 as_of——標了會讓前端誤顯示
        「今日尚未同步」的註記。"""
        result = queries.get_today(self.conn, today=datetime.date(2026, 3, 10))
        self.assertIsNone(result["hrv"]["as_of"])

    def test_hrv_fallback_does_not_reach_beyond_window(self):
        """N-13：退回窗口只為涵蓋「今天還沒同步」，不是資料補洞。
        超出 _TODAY_FALLBACK_DAYS 後必須誠實回 None，不可拿舊資料充數。"""
        far_future = datetime.date(2026, 6, 1)
        result = queries.get_today(self.conn, today=far_future)
        self.assertIsNone(result["hrv"])

    def test_garmin_hrv_status_is_display_only_not_a_fact(self):
        """Garmin HRV 狀態是 Firstbeat 黑盒輸出，必須走 garmin_reference
        且標 for_display_only——長得與 fact() 的三欄位契約不同，
        下游若誤當 fact 使用會立刻壞掉，而非靜默混進判斷層。"""
        self.conn.execute(
            "UPDATE daily_wellness SET hrv_status = 'UNBALANCED' WHERE date = '2026-03-10'"
        )
        result = queries.get_today(self.conn, today=datetime.date(2026, 3, 10))
        gref = result["hrv"]["garmin_reference"]
        self.assertIsNotNone(gref)
        self.assertTrue(gref["for_display_only"])
        self.assertEqual(gref["status"], "UNBALANCED")
        # 不可有 fact() 的三欄位形狀，否則下游會誤用
        self.assertNotIn("confidence", gref)

    def _insert_health_status(self, date, rows):
        athlete_id = self.conn.execute("SELECT id FROM athlete_profile LIMIT 1").fetchone()["id"]
        for metric, status in rows:
            self.conn.execute(
                "INSERT INTO health_status (athlete_id, date, metric, value, baseline_lower, "
                "baseline_upper, status, percentage, source, fetched_at) "
                "VALUES (?, ?, ?, 50, 40, 60, ?, 50, 'garmin_export', 'x')",
                (athlete_id, date, metric, status),
            )

    def test_garmin_health_reference_flags_outliers_as_caution(self):
        """Garmin 健康狀態有任一指標在範圍外 → caution=True，並列出是哪幾項；
        走 reference_only 形狀，不可有 fact() 的三欄位（避免被當事實層使用）。"""
        self._insert_health_status(
            "2026-03-10", [("HR", "IN_RANGE"), ("HRV", "BELOW"), ("SPO2", "IN_RANGE")]
        )
        ref = queries.get_today(self.conn, today=datetime.date(2026, 3, 10))["garmin_reference"]
        self.assertTrue(ref["reference_only"])
        self.assertTrue(ref["caution"])
        self.assertEqual([o["metric"] for o in ref["health_status"]["outliers"]], ["HRV"])
        self.assertEqual(ref["health_status"]["outliers"][0]["status"], "BELOW")
        self.assertEqual(ref["health_status"]["metrics_total"], 3)
        self.assertIsNone(ref["health_status"]["as_of"])
        self.assertNotIn("confidence", ref)

    def test_garmin_health_reference_all_in_range_is_not_caution(self):
        self._insert_health_status("2026-03-10", [("HR", "IN_RANGE"), ("HRV", "IN_RANGE")])
        ref = queries.get_today(self.conn, today=datetime.date(2026, 3, 10))["garmin_reference"]
        self.assertFalse(ref["caution"])
        self.assertEqual(ref["health_status"]["outliers"], [])

    def test_garmin_health_reference_does_not_change_readiness(self):
        """硬邊界：Garmin 異常只影響 caution 旗標，readiness 判定本身不得被它改變。"""
        today = datetime.date(2026, 3, 10)
        before = queries.get_today(self.conn, today=today)["readiness"]["today"]["readiness"]
        self._insert_health_status("2026-03-10", [("HRV", "BELOW"), ("HR", "ABOVE")])
        result = queries.get_today(self.conn, today=today)
        self.assertEqual(result["readiness"]["today"]["readiness"], before)
        self.assertTrue(result["garmin_reference"]["caution"])

    def test_garmin_health_reference_fallback_marks_as_of_and_stops_at_window(self):
        self._insert_health_status("2026-03-10", [("HRV", "BELOW")])
        day_after = queries.get_today(self.conn, today=datetime.date(2026, 3, 11))
        self.assertEqual(day_after["garmin_reference"]["health_status"]["as_of"], "2026-03-10")
        far = queries.get_today(self.conn, today=datetime.date(2026, 6, 1))
        self.assertIsNone(far["garmin_reference"])

    def test_hrv_last_night_5min_high_is_a_fact(self):
        """首頁 HRV 卡比照 Garmin HRV 狀態頁列出「最高 5 分鐘平均」；
        這是手錶量測值不是黑盒判讀，走 fact()；沒有值時回 None。"""
        self.conn.execute(
            "UPDATE daily_wellness SET hrv_last_night_5min_high = 98 WHERE date = '2026-03-10'"
        )
        result = queries.get_today(self.conn, today=datetime.date(2026, 3, 10))
        high = result["hrv"]["last_night_5min_high_ms"]
        self.assertEqual(high["value"], 98)
        self.assertIn("confidence", high)

        self.conn.execute(
            "UPDATE daily_wellness SET hrv_last_night_5min_high = NULL WHERE date = '2026-03-10'"
        )
        result = queries.get_today(self.conn, today=datetime.date(2026, 3, 10))
        self.assertIsNone(result["hrv"]["last_night_5min_high_ms"])

    def test_planned_today_reflects_training_plan_row(self):
        """fixture 的 training_plan 只有 2026-03-09 一筆（lsd 12km）。"""
        result = queries.get_today(self.conn, today=datetime.date(2026, 3, 9))
        self.assertIsNotNone(result["planned"]["today"])
        self.assertEqual(result["planned"]["today"]["workout_type"], "lsd")
        self.assertEqual(result["planned"]["today"]["planned_distance_km"], 12.0)

    def test_planned_missing_day_is_none(self):
        result = queries.get_today(self.conn, today=datetime.date(2026, 3, 10))
        self.assertIsNone(result["planned"]["today"])
        self.assertIsNone(result["planned"]["tomorrow"])

    def test_last_activity_reflects_most_recent_on_or_before_today(self):
        result = queries.get_today(self.conn, today=datetime.date(2026, 3, 10))
        self.assertIsNotNone(result["last_activity"])
        self.assertEqual(result["last_activity"]["date"], "2026-03-09")

    def test_load_fact_carries_confidence(self):
        result = queries.get_today(self.conn, today=datetime.date(2026, 3, 10))
        for key in ("atl", "ctl", "tsb"):
            with self.subTest(key=key):
                self.assertIn("confidence", result["load"][key])
                self.assertIn("value", result["load"][key])

    def test_unsynced_today_falls_back_to_yesterday_with_as_of(self):
        """今天還沒同步時，準備度與負荷退回昨天的資料，並標明日期。"""
        result = queries.get_today(self.conn, today=datetime.date(2026, 3, 11))
        self.assertIsNone(result["readiness"]["today"]["readiness"])
        self.assertEqual(result["readiness"]["today_fallback"]["date"], "2026-03-10")
        self.assertIsNotNone(result["load"])
        self.assertEqual(result["load"]["as_of"], "2026-03-10")

    def test_readiness_carries_measurements(self):
        """今日卡的訓練完備程度要並列各維度的數值 / 門檻。"""
        result = queries.get_today(self.conn, today=datetime.date(2026, 3, 10))
        m = result["readiness"]["today"]["measurements"]
        for key in ("consecutive_training_days", "tsb", "hrv_change_pct"):
            with self.subTest(key=key):
                self.assertIn("value", m[key])
                self.assertIn("threshold", m[key])

    def test_load_tsb_matches_readiness_tsb(self):
        """同一頁的訓練負荷 TSB 與訓練完備程度的 TSB 必須是同一個數字。"""
        result = queries.get_today(self.conn, today=datetime.date(2026, 3, 10))
        readiness_tsb = result["readiness"]["today"]["measurements"]["tsb"]["value"]
        self.assertIsNotNone(readiness_tsb)
        self.assertEqual(result["load"]["tsb"]["value"], readiness_tsb)

    def test_today_with_data_has_no_fallback(self):
        result = queries.get_today(self.conn, today=datetime.date(2026, 3, 10))
        self.assertIsNone(result["readiness"]["today_fallback"])
        self.assertIsNone(result["load"]["as_of"])

    def test_fallback_does_not_reach_beyond_window(self):
        """N-13：超出退回窗口就不拿舊資料代替今天，準備度與負荷都誠實給 None。"""
        result = queries.get_today(self.conn, today=datetime.date(2026, 3, 20))
        self.assertIsNone(result["readiness"]["today_fallback"])
        self.assertIsNone(result["load"])

    def test_races_countdown_from_schedule(self):
        schedule = {
            "target": {"name": "目標馬", "date": "2026-03-20"},
            "upcoming": [{"name": "暖身賽", "date": "2026-03-15"}],
        }
        result = queries.get_today(
            self.conn, today=datetime.date(2026, 3, 10), race_schedule=schedule
        )
        self.assertEqual(result["races"]["next"]["name"], "暖身賽")
        self.assertEqual(result["races"]["next"]["days_left"], 5)
        self.assertEqual(result["races"]["target"]["days_left"], 10)

    def test_races_is_none_without_schedule(self):
        result = queries.get_today(self.conn, today=datetime.date(2026, 3, 10))
        self.assertIsNone(result["races"])

    def test_no_athlete_returns_safe_defaults(self):
        empty_conn = get_connection(":memory:")
        self.addCleanup(empty_conn.close)
        result = queries.get_today(empty_conn, today=datetime.date(2026, 3, 10))
        self.assertIsNone(result["readiness"])
        self.assertIsNone(result["last_activity"])


class TestCalendar(DashboardQueryTestCase):
    def test_covers_full_requested_range(self):
        result = queries.get_calendar(
            self.conn, start="2026-03-01", end="2026-03-05", today=datetime.date(2026, 3, 10)
        )
        self.assertEqual(len(result["days"]), 5)
        self.assertEqual(result["days"][0]["date"], "2026-03-01")
        self.assertEqual(result["days"][-1]["date"], "2026-03-05")

    def test_planned_entry_carries_coach_notes_and_duration(self):
        """外部課表只給時間的課（FR 60'）要能顯示時間與教練原文，不只剩課種。"""
        self.conn.execute(
            "INSERT INTO training_plan (athlete_id, planned_date, workout_type, "
            "planned_duration_sec, notes, plan_source, created_at) VALUES (?, '2026-03-02', "
            "'easy', 3900, 'D1：60''-70''FR+ST*3', 'external', '2026-03-01T00:00:00')",
            (self.athlete_id,),
        )
        result = queries.get_calendar(
            self.conn, start="2026-03-02", end="2026-03-02", today=datetime.date(2026, 3, 10)
        )
        plan = result["days"][0]["planned"][0]
        self.assertEqual(plan["planned_duration_sec"], 3900)
        self.assertEqual(plan["notes"], "D1：60'-70'FR+ST*3")

    def test_time_only_plan_gets_estimated_distance_from_easy_pace_midpoint(self):
        """課表只寫時間：用輕鬆配速區間中點換算預計里程，與教練寫的距離分開放。"""
        self.conn.execute(
            "INSERT INTO training_plan (athlete_id, planned_date, workout_type, "
            "planned_duration_sec, plan_source, created_at) VALUES (?, '2026-03-02', "
            "'easy', 2700, 'external', '2026-03-01T00:00:00')",
            (self.athlete_id,),
        )
        result = queries.get_calendar(
            self.conn, start="2026-03-02", end="2026-03-02",
            today=datetime.date(2026, 3, 10), easy_pace_range=(360, 390),
        )
        plan = result["days"][0]["planned"][0]
        self.assertIsNone(plan["planned_distance_km"])
        self.assertAlmostEqual(plan["estimated_distance_km"], 2700 / 375)

    def test_no_estimate_without_easy_pace_or_when_distance_given(self):
        self.conn.execute(
            "INSERT INTO training_plan (athlete_id, planned_date, workout_type, "
            "planned_duration_sec, plan_source, created_at) VALUES (?, '2026-03-02', "
            "'easy', 2700, 'external', '2026-03-01T00:00:00')",
            (self.athlete_id,),
        )
        self.conn.execute(
            "INSERT INTO training_plan (athlete_id, planned_date, workout_type, "
            "planned_distance_km, planned_duration_sec, plan_source, created_at) "
            "VALUES (?, '2026-03-03', 'easy', 8.0, 3000, 'external', '2026-03-01T00:00:00')",
            (self.athlete_id,),
        )
        no_pace = queries.get_calendar(
            self.conn, start="2026-03-02", end="2026-03-02", today=datetime.date(2026, 3, 10)
        )
        self.assertIsNone(no_pace["days"][0]["planned"][0]["estimated_distance_km"])
        with_distance = queries.get_calendar(
            self.conn, start="2026-03-03", end="2026-03-03",
            today=datetime.date(2026, 3, 10), easy_pace_range=(360, 390),
        )
        self.assertIsNone(with_distance["days"][0]["planned"][0]["estimated_distance_km"])

    def test_planned_day_matched_to_activity_is_planned_done(self):
        """2026-03-09 有 lsd 課表且有一場跑步活動——應配對成功。"""
        result = queries.get_calendar(
            self.conn, start="2026-03-09", end="2026-03-09", today=datetime.date(2026, 3, 10)
        )
        day = result["days"][0]
        self.assertEqual(day["day_state"], "planned_done")
        self.assertEqual(len(day["activities"]), 1)
        self.assertEqual(day["activities"][0]["plan_match"]["state"], "inferred")
        # 當日明細要顯示開始時間（N-29）
        self.assertTrue(day["activities"][0]["started_at"].startswith("2026-03-09T"))

    def test_two_runs_on_planned_day_is_planned_done_not_missed(self):
        """熱身另存一場活動時，單場配對是 ambiguous，但「當天有沒有執行」不需
        知道是哪一場——不可因此把整天判成未執行。"""
        row = self.conn.execute(
            "SELECT athlete_id, activity_type FROM activities "
            "WHERE date(started_at) = '2026-03-09'"
        ).fetchone()
        self.conn.execute(
            "INSERT INTO activities (athlete_id, source, source_version, fetched_at, "
            "activity_type, title, started_at, distance_km, duration_sec) "
            "VALUES (?, 'garmin_export', 'test', '2026-03-10T00:00:00', ?, '熱身', "
            "'2026-03-09T05:00:00', 1.7, 600)",
            (row["athlete_id"], row["activity_type"]),
        )
        self.conn.commit()
        result = queries.get_calendar(
            self.conn, start="2026-03-09", end="2026-03-09", today=datetime.date(2026, 3, 10)
        )
        day = result["days"][0]
        self.assertEqual(day["day_state"], "planned_done")
        # 單場層級仍誠實回報歧義，不替任何一場宣稱執行了課表
        for activity in day["activities"]:
            self.assertEqual(activity["plan_match"]["state"], "ambiguous")

    def test_linked_warmup_is_not_a_competitor(self):
        """暖身已關聯到主課（N-25）時不算競爭者：主課 inferred、暖身回
        warmup_of_main；單場明細（MCP get_session_detail 同路徑）也輸出課表。"""
        main = self.conn.execute(
            "SELECT id, athlete_id, activity_type FROM activities "
            "WHERE date(started_at) = '2026-03-09'"
        ).fetchone()
        cur = self.conn.execute(
            "INSERT INTO activities (athlete_id, source, source_version, fetched_at, "
            "activity_type, title, started_at, distance_km, duration_sec, "
            "warmup_for_activity_id) "
            "VALUES (?, 'garmin_export', 'test', '2026-03-10T00:00:00', ?, '熱身', "
            "'2026-03-09T05:00:00', 1.7, 600, ?)",
            (main["athlete_id"], main["activity_type"], main["id"]),
        )
        warmup_id = cur.lastrowid
        self.conn.commit()
        day = queries.get_calendar(
            self.conn, start="2026-03-09", end="2026-03-09", today=datetime.date(2026, 3, 10)
        )["days"][0]
        states = {a["id"]: a["plan_match"] for a in day["activities"]}
        self.assertEqual(states[main["id"]]["state"], "inferred")
        self.assertEqual(states[warmup_id]["reason"], "warmup_of_main")
        self.assertEqual(day["day_state"], "planned_done")

        detail = queries.get_session_detail(self.conn, main["id"])
        self.assertEqual(detail["planned_match"]["state"], "inferred")
        self.assertIsNotNone(detail["planned"])
        warmup_detail = queries.get_session_detail(self.conn, warmup_id)
        self.assertEqual(warmup_detail["planned_match"]["reason"], "warmup_of_main")
        self.assertIsNone(warmup_detail["planned"])

    def test_planned_day_without_activity_before_today_is_planned_missed(self):
        """2026-03-01 無課表、無活動——先驗證 rest_ok，再驗證有課表無活動的情境。"""
        self.conn.execute(
            "INSERT INTO training_plan (athlete_id, planned_date, workout_type, "
            "planned_distance_km, plan_source, created_at) VALUES (?, '2026-03-01', "
            "'tempo', 5.0, 'generated', '2026-03-01T00:00:00')",
            (self.athlete_id,),
        )
        self.conn.commit()
        result = queries.get_calendar(
            self.conn, start="2026-03-01", end="2026-03-01", today=datetime.date(2026, 3, 10)
        )
        self.assertEqual(result["days"][0]["day_state"], "planned_missed")

    def test_today_with_pending_plan_is_today_pending_not_missed(self):
        """今天有課表但還沒活動——今天還沒過完，不可判為錯過。"""
        self.conn.execute(
            "INSERT INTO training_plan (athlete_id, planned_date, workout_type, "
            "planned_distance_km, plan_source, created_at) VALUES (?, '2026-03-10', "
            "'easy', 5.0, 'generated', '2026-03-01T00:00:00')",
            (self.athlete_id,),
        )
        self.conn.commit()
        result = queries.get_calendar(
            self.conn, start="2026-03-10", end="2026-03-10", today=datetime.date(2026, 3, 10)
        )
        self.assertEqual(result["days"][0]["day_state"], "today_pending")

    def test_future_day_is_future(self):
        result = queries.get_calendar(
            self.conn, start="2026-03-15", end="2026-03-15", today=datetime.date(2026, 3, 10)
        )
        self.assertEqual(result["days"][0]["day_state"], "future")

    def test_day_with_no_plan_and_no_activity_is_rest_ok(self):
        result = queries.get_calendar(
            self.conn, start="2026-03-01", end="2026-03-01", today=datetime.date(2026, 3, 10)
        )
        self.assertEqual(result["days"][0]["day_state"], "rest_ok")

    def test_non_running_activity_on_unplanned_day_is_rest_ok(self):
        """2026-03-02 是重訓活動，無跑步課表——重訓不佔用跑步的「有排課」語意。"""
        result = queries.get_calendar(
            self.conn, start="2026-03-02", end="2026-03-02", today=datetime.date(2026, 3, 10)
        )
        day = result["days"][0]
        self.assertEqual(day["day_state"], "rest_ok")
        self.assertFalse(day["activities"][0]["is_running"])

    def test_all_activity_types_are_included_not_only_running(self):
        result = queries.get_calendar(
            self.conn, start="2026-03-02", end="2026-03-02", today=datetime.date(2026, 3, 10)
        )
        self.assertEqual(result["days"][0]["activities"][0]["activity_type"], "strength_training")

    def test_no_athlete_returns_empty_days(self):
        empty_conn = get_connection(":memory:")
        self.addCleanup(empty_conn.close)
        result = queries.get_calendar(
            empty_conn, start="2026-03-01", end="2026-03-05", today=datetime.date(2026, 3, 10)
        )
        self.assertEqual(result["days"], [])


class TestTrainingLoad(DashboardQueryTestCase):
    def test_series_covers_range(self):
        result = queries.get_training_load(self.conn, range_key="7d")
        self.assertEqual(len(result["series"]), 7)
        self.assertEqual(result["series"][0]["date"], result["start_date"])
        self.assertEqual(result["series"][-1]["date"], result["end_date"])

    def test_each_entry_has_atl_ctl_tsb_with_confidence(self):
        result = queries.get_training_load(self.conn, range_key="7d")
        for entry in result["series"]:
            for key in ("atl", "ctl", "tsb"):
                with self.subTest(date=entry["date"], key=key):
                    self.assertIn("confidence", entry[key])

    def test_respects_range_key(self):
        short = queries.get_training_load(self.conn, range_key="7d")
        long = queries.get_training_load(self.conn, range_key="30d")
        self.assertLess(len(short["series"]), len(long["series"]))

    def test_no_athlete_returns_empty_series(self):
        empty_conn = get_connection(":memory:")
        self.addCleanup(empty_conn.close)
        result = queries.get_training_load(empty_conn, range_key="7d")
        self.assertEqual(result["series"], [])
        self.assertIsNone(result["garmin_reference"])

    def _add_training_status(self, date, acute, chronic):
        self.conn.execute(
            """
            INSERT INTO training_status (athlete_id, date, training_status_phrase,
                acute_load, chronic_load, acwr_ratio, acwr_status,
                chronic_load_optimal_min, chronic_load_optimal_max,
                source, fetched_at)
            VALUES (?, ?, 'PRODUCTIVE_3', ?, ?, ?, 'OPTIMAL', ?, ?,
                    'garmin_export', '2026-03-10T00:00:00')
            """,
            (self.athlete_id, date, acute, chronic, round(acute / chronic, 1),
             chronic * 0.8, chronic * 1.5),
        )

    def test_garmin_reference_none_without_training_status(self):
        result = queries.get_training_load(self.conn, range_key="7d")
        self.assertIsNone(result["garmin_reference"])

    def test_garmin_reference_is_display_only_and_limited_to_range(self):
        self._add_training_status("2026-03-09", 403.0, 332.0)
        self._add_training_status("2026-01-01", 100.0, 100.0)  # 視窗外
        ref = queries.get_training_load(self.conn, range_key="7d")["garmin_reference"]
        self.assertTrue(ref["for_display_only"])
        self.assertEqual([d["date"] for d in ref["series"]], ["2026-03-09"])
        day = ref["series"][0]
        self.assertEqual(day["status"], "PRODUCTIVE_3")
        self.assertEqual(day["acute_load"], 403.0)
        self.assertEqual(day["chronic_load"], 332.0)

    def test_garmin_optimal_range_is_exposed_as_acute_load_range(self):
        """DB 欄位名叫 chronic_load_optimal_*，但語意是急性負荷的建議範圍。"""
        self._add_training_status("2026-03-09", 403.0, 332.0)
        day = queries.get_training_load(self.conn, range_key="7d")["garmin_reference"]["series"][0]
        self.assertEqual(day["acute_load_optimal_min"], 265.6)
        self.assertEqual(day["acute_load_optimal_max"], 498.0)
        self.assertNotIn("chronic_load_optimal_min", day)


class TestOverview(DashboardQueryTestCase):
    """fixture 的 30d 視窗（2026-02-09 ~ 2026-03-10）內有三場跑步：
    100（03-09，10km，有心率與區間）、200（03-05，9km，有心率無區間）、
    300（03-03，5km，無心率）。"""

    TODAY = datetime.date(2026, 3, 10)

    def _overview(self, **kwargs):
        return queries.get_overview(self.conn, range_key="30d", today=self.TODAY, **kwargs)

    def _mark_race(self, external_id):
        self.conn.execute(
            "UPDATE activities SET workout_type = 'race', workout_type_source = 'manual' "
            "WHERE external_id = ?",
            (external_id,),
        )

    def _add_run(self, started_at, distance_km, duration_sec, workout_type="race"):
        cur = self.conn.execute(
            """
            INSERT INTO activities (athlete_id, activity_type, started_at, distance_km,
                                    duration_sec, avg_pace_sec_per_km, workout_type,
                                    source, fetched_at)
            VALUES (?, 'running', ?, ?, ?, ?, ?, 'garmin_export', '2026-03-10T00:00:00')
            """,
            (self.athlete_id, started_at, distance_km, duration_sec,
             round(duration_sec / distance_km), workout_type),
        )
        return cur.lastrowid

    def _pb(self, distance):
        """最佳成績固定列出五個距離，依距離代碼取該格。"""
        return {p["distance"]: p for p in self._overview()["personal_bests"]}[distance]

    def test_effective_sessions_excludes_runs_without_hr(self):
        sessions = self._overview()["sessions"]
        self.assertEqual(sessions["total"], 3)
        self.assertEqual(sessions["effective"], 2)  # 300 沒有心率

    def test_weekly_intensity_splits_distance_by_zone_time(self):
        weeks = {w["week_start"]: w for w in self._overview()["weekly_intensity"]["weeks"]}
        # 100：Z0 300s + Z1 600s + Z2 1800s = 2700s 低強度、Z4 300s 高強度，共 3000s
        self.assertEqual(weeks["2026-03-09"]["low_km"], 9.0)
        self.assertEqual(weeks["2026-03-09"]["mid_km"], 0.0)
        self.assertEqual(weeks["2026-03-09"]["high_km"], 1.0)
        # 200、300 都沒有心率區間 → 整場算 unknown，不猜強度
        self.assertEqual(weeks["2026-03-02"]["unknown_km"], 14.0)
        self.assertEqual(weeks["2026-03-02"]["low_km"], 0.0)

    def test_weekly_intensity_lists_every_iso_week_and_flags_partial(self):
        weeks = self._overview()["weekly_intensity"]["weeks"]
        self.assertEqual(weeks[0]["week_start"], "2026-02-09")  # 視窗起日剛好是週一
        self.assertFalse(weeks[0]["partial"])
        self.assertTrue(weeks[-1]["partial"])  # 03-09 那週被 03-10 截斷
        empty = [w for w in weeks if w["week_start"] == "2026-02-16"][0]
        self.assertEqual(empty["total_km"], 0.0)

    def test_hr_zone_distribution_excludes_zone_zero_from_denominator(self):
        zones = self._overview()["hr_zones"]
        self.assertTrue(zones["available"])
        pct = {z["zone"]: z["pct"] for z in zones["zones"]}
        # 分母只有 Z1~Z5：600 + 1800 + 300 = 2700
        self.assertEqual(pct, {1: 22.2, 2: 66.7, 3: 0.0, 4: 11.1, 5: 0.0})
        self.assertEqual(zones["sessions_counted"], 1)

    def test_this_week_uses_iso_week_anchored_on_today(self):
        week = self._overview()["this_week"]
        self.assertEqual(week["week_start"], "2026-03-09")
        self.assertEqual(week["done_km"], 10.0)
        self.assertEqual(week["planned_km"], 12.0)
        self.assertEqual(week["planned_without_distance"], 0)

    def test_this_week_without_plan_is_none_not_zero(self):
        week = queries.get_overview(
            self.conn, range_key="30d", today=datetime.date(2026, 3, 20)
        )["this_week"]
        self.assertIsNone(week["planned_km"])

    def _add_easy_run(self, started_at, pace, hr, gap=None, workout_type="easy"):
        run_id = self._add_run(started_at, 10.0, pace * 10, workout_type=workout_type)
        self.conn.execute(
            "UPDATE activities SET avg_hr_bpm = ?, avg_grade_adj_pace_sec_per_km = ? WHERE id = ?",
            (hr, gap, run_id),
        )

    def _seed_ef_runs(self):
        """近 6 週 3 場 EF 較高（6:00、140bpm），更早 4 場 EF 較低（6:00、150bpm）。
        先把共用 fixture 的輕鬆跑改成未分類，只留這裡建的場次方便算期望值。"""
        self.conn.execute("UPDATE activities SET workout_type = NULL")
        for day in ("2026-03-01", "2026-02-20", "2026-02-10"):
            self._add_easy_run(f"{day}T06:00:00", 360, 140, gap=360)
        for day in ("2025-12-01", "2025-11-01", "2025-10-15", "2025-10-01"):
            self._add_easy_run(f"{day}T06:00:00", 360, 150, gap=360)

    def test_aerobic_efficiency_compares_recent_to_baseline(self):
        self._seed_ef_runs()
        ef = self._overview()["aerobic_efficiency"]
        self.assertEqual((ef["recent_runs"], ef["baseline_runs"]), (3, 7))
        change = ef["change"]["value"]
        # EF = (60000/360)/HR：近期 166.67/140=1.190；基準 (3×1.190+4×1.111)/7=1.145
        self.assertEqual(change["recent_ef"], 1.190)
        # 每場 EF 先四捨五入到 3 位再平均，故實得 3.9 而非精確的 3.96
        self.assertAlmostEqual(change["change_pct"], 4.0, delta=0.15)
        codes = {b["code"] for b in ef["change"]["confidence"]["biases"]}
        self.assertEqual(codes, {"ef_season_unadjusted"})

    def test_aerobic_efficiency_ignores_hard_sessions(self):
        self._seed_ef_runs()
        self._add_easy_run("2026-03-02T06:00:00", 300, 170, gap=300, workout_type="interval")
        self.assertEqual(self._overview()["aerobic_efficiency"]["recent_runs"], 3)

    def test_aerobic_efficiency_flags_missing_gap(self):
        self._seed_ef_runs()
        self._add_easy_run("2026-03-02T06:00:00", 360, 140, gap=None)
        codes = {
            b["code"]
            for b in self._overview()["aerobic_efficiency"]["change"]["confidence"]["biases"]
        }
        self.assertIn("ef_pace_not_grade_adjusted", codes)

    def test_aerobic_efficiency_needs_enough_runs(self):
        ef = self._overview()["aerobic_efficiency"]
        self.assertIsNone(ef["change"])
        self.assertIn("場次不足", ef["reason"])

    def test_vdot_ignores_non_race_runs(self):
        race = self._overview()["vdot"]["race"]
        self.assertIsNone(race["value"])
        self.assertIn("沒有標記為比賽", race["reason"])

    def test_vdot_from_fresh_race(self):
        self._mark_race("10000001")
        fact = self._overview()["vdot"]["race"]["value"]
        self.assertIsNotNone(fact)
        self.assertIn("2026-03-09 10K", fact["source"])
        self.assertEqual(fact["confidence"]["level"], "high")
        basis = self._overview()["vdot"]["race"]["basis"]
        self.assertEqual(basis, {"date": "2026-03-09", "distance": "10K"})

    def test_vdot_accepts_3k_time_trial(self):
        self._add_run("2026-03-01T07:00:00", 3.05, 840)
        fact = self._overview()["vdot"]["race"]["value"]
        self.assertIn("3K", fact["source"])

    def test_vdot_half_marathon_past_90_days_is_low_confidence(self):
        self._add_run("2025-10-01T07:00:00", 21.2, 7200)  # 160 天前
        fact = self._overview()["vdot"]["race"]["value"]
        self.assertEqual(fact["confidence"]["level"], "low")
        self.assertEqual(fact["confidence"]["biases"][0]["code"], "vdot_race_stale")

    def test_vdot_stale_race_reports_reason(self):
        self._add_run("2025-01-01T07:00:00", 10.0, 3000)
        race = self._overview()["vdot"]["race"]
        self.assertIsNone(race["value"])
        self.assertIn("新鮮度", race["reason"])

    def test_garmin_reference_is_display_only(self):
        self.assertIsNone(self._overview()["vdot"]["garmin_reference"])
        self.conn.execute(
            """
            INSERT INTO performance_metrics (athlete_id, date, race_predict_10k_sec,
                                             source, fetched_at)
            VALUES (?, '2026-03-08', 3000, 'garmin_mcp', '2026-03-08T00:00:00')
            """,
            (self.athlete_id,),
        )
        ref = self._overview()["vdot"]["garmin_reference"]
        self.assertTrue(ref["for_display_only"])
        self.assertEqual(ref["as_of"], "2026-03-08")
        self.assertEqual([p["distance"] for p in ref["predictions"]], ["10K"])
        # 10K 50:00 與「比賽 10K 50:00」推出的 VDOT 必須一致（同一條換算路徑）
        self._mark_race("10000001")
        race_vdot = self._overview()["vdot"]["race"]["value"]["value"]
        self.assertEqual(ref["predictions"][0]["vdot"], race_vdot)

    def test_personal_bests_group_by_distance_fastest_first(self):
        self._mark_race("10000001")                                   # 10K 50:00
        self._add_run("2025-06-01T07:00:00", 10.1, 3300)       # 10K 55:00
        self._add_run("2025-05-01T07:00:00", 21.1, 7000)       # 半馬
        self._add_run("2025-04-01T07:00:00", 15.0, 5000)       # 非標準距離，不列
        self._add_run("2025-03-01T07:00:00", 5.0, 1400, workout_type="easy")  # 非比賽
        pbs = self._overview()["personal_bests"]
        self.assertEqual([p["label"] for p in pbs if p["records"]], ["10K", "半馬"])
        ten_k = self._pb("10k")["records"]
        self.assertEqual([r["time_sec"] for r in ten_k], [3000, 3300])
        self.assertFalse(ten_k[0]["official"])

    def test_personal_bests_always_list_all_five_distances(self):
        pbs = self._overview()["personal_bests"]
        self.assertEqual([p["label"] for p in pbs], ["3K", "5K", "10K", "半馬", "全馬"])
        self.assertTrue(all(p["records"] == [] for p in pbs))

    def test_official_time_overrides_watch_time(self):
        # 手錶 3.07km / 866s（忘了按停），官方成績 3K 839s
        run_id = self._add_run("2026-03-01T07:00:00", 3.07, 866)
        self.conn.execute(
            "UPDATE activities SET race_official_time_sec = 839 WHERE id = ?", (run_id,)
        )
        overview = self._overview()
        record = overview["personal_bests"][0]["records"][0]
        self.assertEqual(record["time_sec"], 839)
        self.assertEqual(record["pace_sec_per_km"], 280)  # 839 / 3.0
        self.assertTrue(record["official"])
        # VDOT 也用官方成績：比照一場剛好 3.0km / 839s 的紀錄
        with_official = overview["vdot"]["race"]["value"]["value"]
        self.conn.execute(
            "UPDATE activities SET race_official_time_sec = NULL, distance_km = 3.0, "
            "duration_sec = 839, avg_pace_sec_per_km = 280 WHERE id = ?",
            (run_id,),
        )
        self.assertEqual(self._overview()["vdot"]["race"]["value"]["value"], with_official)

    def test_official_distance_rescues_gps_drifted_race(self):
        # 舊錶 GPS 把半馬記成 22.30km（超出半馬 ±1.05km 容忍），沒填官方距離就不列
        run_id = self._add_run("2025-03-01T07:00:00", 22.30, 10214)
        self.assertEqual(self._pb("half_marathon")["records"], [])
        self.conn.execute(
            "UPDATE activities SET race_official_distance_km = 21.0975 WHERE id = ?", (run_id,)
        )
        record = self._pb("half_marathon")["records"][0]
        self.assertEqual(record["time_sec"], 10214)
        self.assertFalse(record["official"])  # 時間仍是手錶紀錄
        self.assertEqual(record["pace_sec_per_km"], round(10214 / 21.0975))

    def test_race_pace_uses_standard_distance_not_gps(self):
        # 手錶 10.11km / 52:30：配速應為 52:30 ÷ 10km = 5:15，而非手錶的 5:12
        self._add_run("2025-11-23T07:00:00", 10.11, 3150)
        record = self._pb("10k")["records"][0]
        self.assertEqual(record["pace_sec_per_km"], 315)

    def test_race_name_comes_from_profile_with_title_fallback(self):
        self._add_run("2025-11-23T07:00:00", 10.0, 3150)
        self._add_run("2026-10-08T07:00:00", 3.0, 839)
        self._add_run("2025-10-01T07:00:00", 10.0, 3300)
        bests = queries.get_overview(
            self.conn,
            range_key="30d",
            today=self.TODAY,
            race_names={"2025-11-23": "城市 10K", "2026-10-08": "跑班 3K 速度測試"},
        )["personal_bests"]
        by_date = {r["date"]: r for pb in bests for r in pb["records"]}
        self.assertEqual(by_date["2025-11-23"]["name"], "城市 10K")
        self.assertFalse(by_date["2025-11-23"]["personal_test"])
        self.assertTrue(by_date["2026-10-08"]["personal_test"])
        # 學員檔沒寫的場次退回活動名稱（_add_run 建的活動沒有 title → None）
        self.assertEqual(by_date["2025-10-01"]["name"], by_date["2025-10-01"]["title"])

    def test_personal_bests_return_every_race_fastest_first(self):
        times = [1530, 1500, 1550, 1510, 1520]
        for i, t in enumerate(times):
            self._add_run(f"2025-0{i + 1}-01T07:00:00", 5.0, t)
        records = self._pb("5k")["records"]
        self.assertEqual([r["time_sec"] for r in records], sorted(times))

    def test_this_week_zone_seconds(self):
        week = self._overview()["this_week"]
        # 本週（03-09 起）只有 100：Z1 600s、Z2 1800s、Z4 300s
        self.assertEqual(
            {z["zone"]: z["seconds"] for z in week["zone_seconds"]},
            {1: 600, 2: 1800, 3: 0, 4: 300, 5: 0},
        )
        self.assertEqual((week["sessions"], week["zone_sessions_counted"]), (1, 1))

    def test_recent_volume_rolling_one_week(self):
        recent = self._overview()["recent_volume"]
        # 03-04~03-10：100(10) + 200(9)；再往前 02-25~03-03：300(5)
        self.assertEqual((recent["start"], recent["end"]), ("2026-03-04", "2026-03-10"))
        self.assertEqual(recent["km"], 19.0)
        self.assertEqual(recent["previous_km"], 5.0)

    def test_today_freshness_marks_sync_time_as_utc(self):
        """最後更新時間由 /api/today 帶出（側邊列每頁都看得到），總覽不再重複輸出。"""
        f = queries.get_today(self.conn, today=self.TODAY)["freshness"]
        # fixture 的 fetched_at 是 2026-03-10T00:00:00（UTC，無時區）
        self.assertEqual(f["last_synced_at"], "2026-03-10T00:00:00Z")
        self.assertEqual(f["activity_latest_at"], "2026-03-09T07:00")
        self.assertEqual(f["wellness_latest"], "2026-03-10")
        self.assertNotIn("data_freshness", self._overview())

    def test_hr_zone_bounds_unavailable_without_watch_settings(self):
        bounds = self._overview()["hr_zone_bounds"]
        self.assertFalse(bounds["available"])
        self.assertEqual([z["name_en"] for z in bounds["zones"]],
                         ["Warm Up", "Easy run", "Aerobic", "Threshold", "Maximum"])

    def test_hr_zone_bounds_converted_to_bpm(self):
        self.conn.execute(
            "UPDATE athlete_profile SET max_hr_bpm = 195, resting_hr_bpm = 56, "
            "max_hr_source = 'watch_display', "
            "hr_zone_hrr_bounds_json = '[0.54, 0.59, 0.74, 0.83, 0.88, 1.0]'"
        )
        bounds = self._overview()["hr_zone_bounds"]
        self.assertTrue(bounds["available"])
        z2 = bounds["zones"][1]
        self.assertEqual((z2["name"], z2["bpm_low"], z2["bpm_high"]), ("輕鬆", 138, 159))
        self.assertEqual(bounds["zones"][-1]["bpm_high"], 195)
        self.assertEqual(bounds["max_hr"]["confidence"]["level"], "low")

    def test_vdot_history_lists_races_oldest_first(self):
        self._add_run("2025-12-01T07:00:00", 21.1, 7000)
        self._add_run("2026-02-01T07:00:00", 3.0, 840)
        history = self._overview()["vdot"]["history"]
        self.assertEqual([h["distance"] for h in history], ["半馬", "3K"])
        self.assertTrue(all(isinstance(h["vdot"], float) for h in history))

    def test_no_athlete_returns_unavailable(self):
        empty_conn = get_connection(":memory:")
        self.addCleanup(empty_conn.close)
        result = queries.get_overview(empty_conn, range_key="30d", today=self.TODAY)
        self.assertFalse(result["available"])


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
