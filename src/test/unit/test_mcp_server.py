"""mcp/server.py 的單元測試——直接呼叫 @mcp.tool() 裝飾後的函式本體
（不透過 MCP protocol/stdio），驗證薄封裝層正確轉呼叫既有 services 函式、
且回傳結構中的 datetime.date/datetime.datetime 已序列化成 ISO 字串。

全部使用合成假資料建立臨時 SQLite，不含真實個人資料。
"""

import datetime
import unittest
from types import SimpleNamespace

from src.main.python.models.db import get_connection
from src.main.python.mcp.server import (
    _BEHAVIOR_CONSTRAINTS,
    _ROLE,
    _serialize,
    add_context_note,
    assess_session_quality,
    coach,
    generate_training_plan,
    get_active_training_plan,
    get_athlete_meta,
    get_readiness_status,
    get_context_notes,
    get_recovery_impact,
    get_session_detail,
    get_today,
    get_wellness_trend,
    list_running_sessions,
    save_external_workouts,
)

START = datetime.date(2026, 8, 1)


def _fake_ctx(conn):
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=SimpleNamespace(db=conn))
    )


def _build_athlete(conn, max_hr_bpm=None, resting_hr_bpm=None) -> int:
    cursor = conn.execute(
        """
        INSERT INTO athlete_profile (name, max_hr_bpm, resting_hr_bpm, updated_at)
        VALUES ('測試學員', ?, ?, '2026-01-01T00:00:00')
        """,
        (max_hr_bpm, resting_hr_bpm),
    )
    return cursor.lastrowid


def _insert_activity(conn, athlete_id: int, date: datetime.date, avg_hr_bpm=140) -> int:
    cursor = conn.execute(
        """
        INSERT INTO activities
            (athlete_id, activity_type, started_at, distance_km, duration_sec,
             avg_hr_bpm, source, fetched_at)
        VALUES (?, 'running', ?, 5.0, 1800, ?, 'fit_manual', ?)
        """,
        (
            athlete_id,
            f"{date.isoformat()}T07:00:00",
            avg_hr_bpm,
            f"{date.isoformat()}T07:00:00",
        ),
    )
    return cursor.lastrowid


class TestSerialize(unittest.TestCase):
    def test_date_becomes_iso_string(self):
        self.assertEqual(_serialize(datetime.date(2026, 8, 20)), "2026-08-20")

    def test_datetime_becomes_iso_string(self):
        dt = datetime.datetime(2026, 8, 20, 10, 30, 0)
        self.assertEqual(_serialize(dt), dt.isoformat())

    def test_nested_dict_and_list_recursively_serialized(self):
        value = {
            "date": datetime.date(2026, 8, 20),
            "items": [{"d": datetime.date(2026, 8, 21)}, "plain"],
            "n": 5,
        }
        result = _serialize(value)
        self.assertEqual(result["date"], "2026-08-20")
        self.assertEqual(result["items"][0]["d"], "2026-08-21")
        self.assertEqual(result["items"][1], "plain")
        self.assertEqual(result["n"], 5)

    def test_none_passes_through(self):
        self.assertIsNone(_serialize(None))


class TestReadOnlyTools(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)
        self.conn.commit()

    def test_get_athlete_meta_returns_athlete_info(self):
        result = get_athlete_meta(_fake_ctx(self.conn))
        self.assertEqual(result["athlete"]["id"], self.athlete_id)
        self.assertEqual(result["athlete"]["name"], "測試學員")

    def test_list_running_sessions_returns_no_json_incompatible_types(self):
        _insert_activity(self.conn, self.athlete_id, START)
        self.conn.commit()

        result = list_running_sessions(_fake_ctx(self.conn), range_key="all")
        self.assertEqual(len(result["sessions"]), 1)
        # started_at 已是字串（DB 存字串），此處確認整體結構走過序列化無報錯
        self.assertIsInstance(result["sessions"][0]["started_at"], str)

    def test_get_session_detail_returns_none_for_missing_session(self):
        result = get_session_detail(_fake_ctx(self.conn), session_id=9999)
        self.assertIsNone(result)

    def test_get_session_detail_returns_dict_for_existing_session(self):
        activity_id = _insert_activity(self.conn, self.athlete_id, START)
        self.conn.commit()

        result = get_session_detail(_fake_ctx(self.conn), session_id=activity_id)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], activity_id)

    def test_get_wellness_trend_returns_dict_with_metrics(self):
        result = get_wellness_trend(_fake_ctx(self.conn), range_key="all")
        self.assertIn("metrics", result)

    def test_get_recovery_impact_returns_dict_with_impacts(self):
        result = get_recovery_impact(_fake_ctx(self.conn), range_key="all")
        self.assertIn("impacts", result)

    def test_assess_session_quality_wires_pace_zones_into_pace_execution(self):
        """VDOT 配速區間確實從資料庫接到配速執行度維度（本工具的接線點）。"""
        self.conn.execute(
            "UPDATE athlete_profile SET max_hr_bpm = 200, resting_hr_bpm = 50 WHERE id = ?",
            (self.athlete_id,),
        )
        # 一場 10K 比賽成績讓 VDOT 推得出來，另一場 easy 作為被評估的對象。
        self.conn.execute(
            """
            INSERT INTO activities
                (athlete_id, activity_type, started_at, distance_km, duration_sec,
                 avg_hr_bpm, avg_pace_sec_per_km, workout_type, source, fetched_at)
            VALUES (?, 'running', ?, 10.0, 3000, 175, 300, 'race', 'fit_manual', ?)
            """,
            (self.athlete_id, f"{START.isoformat()}T07:00:00", f"{START.isoformat()}T07:00:00"),
        )
        easy_date = START + datetime.timedelta(days=7)
        cursor = self.conn.execute(
            """
            INSERT INTO activities
                (athlete_id, activity_type, started_at, distance_km, duration_sec,
                 avg_hr_bpm, avg_pace_sec_per_km, workout_type, source, fetched_at)
            VALUES (?, 'running', ?, 8.0, 3200, 140, 400, 'easy', 'fit_manual', ?)
            """,
            (self.athlete_id, f"{easy_date.isoformat()}T07:00:00", f"{easy_date.isoformat()}T07:00:00"),
        )
        self.conn.commit()

        result = assess_session_quality(_fake_ctx(self.conn), session_id=cursor.lastrowid)

        assessed = {d["dimension"] for d in result["dimensions"]}
        self.assertIn("pace_execution", assessed)
        pace_execution = next(
            d for d in result["dimensions"] if d["dimension"] == "pace_execution"
        )
        self.assertEqual(pace_execution["actual_pace_sec_per_km"], 400)

    def test_get_readiness_status_returns_serialized_dates(self):
        _insert_activity(self.conn, self.athlete_id, START)
        self.conn.commit()

        result = get_readiness_status(
            _fake_ctx(self.conn),
            start_date="2026-08-01",
            end_date="2026-08-03",
        )
        self.assertEqual(len(result), 3)
        for day in result:
            self.assertIsInstance(day["date"], str)
            datetime.date.fromisoformat(day["date"])  # 不拋例外即代表格式正確

    def test_get_readiness_status_no_athlete_returns_empty_list(self):
        conn = get_connection(":memory:")
        result = get_readiness_status(
            _fake_ctx(conn), start_date="2026-08-01", end_date="2026-08-01"
        )
        self.assertEqual(result, [])

    def test_get_active_training_plan_empty_when_no_plan(self):
        result = get_active_training_plan(_fake_ctx(self.conn))
        self.assertEqual(result, [])

    def test_get_today_returns_expected_sections(self):
        """今日卡聚合：回傳結構含全部區塊，且日期已序列化為 ISO 字串。"""
        result = get_today(_fake_ctx(self.conn))
        for key in (
            "date",
            "freshness",
            "planned",
            "readiness",
            "load",
            "hrv",
            "consecutive_training_days",
            "last_activity",
        ):
            self.assertIn(key, result)
        self.assertIsInstance(result["date"], str)

    def test_get_today_no_athlete_returns_empty_sections(self):
        """查無學員時不得拋錯，各區塊回 None——None 是「無法評估」，
        呼叫端據此顯示「未評」而非 0。"""
        conn = get_connection(":memory:")
        result = get_today(_fake_ctx(conn))
        self.assertIsNone(result["readiness"])
        self.assertIsNone(result["load"])


class TestGenerateTrainingPlanTool(unittest.TestCase):
    def test_returns_unavailable_when_no_vdot_candidate(self):
        conn = get_connection(":memory:")
        _build_athlete(conn)
        conn.commit()

        result = generate_training_plan(
            _fake_ctx(conn),
            start_date="2026-09-01",
            total_weeks=16,
            days_per_week=4,
        )
        self.assertFalse(result["available"])
        self.assertIn("reason", result)

    def test_no_athlete_returns_unavailable_without_error(self):
        conn = get_connection(":memory:")
        result = generate_training_plan(
            _fake_ctx(conn),
            start_date="2026-09-01",
            total_weeks=16,
            days_per_week=4,
        )
        self.assertFalse(result["available"])

    def test_success_writes_plan_and_serializes_schedule_dates(self):
        conn = get_connection(":memory:")
        athlete_id = _build_athlete(conn, max_hr_bpm=195, resting_hr_bpm=55)
        recent_race_date = datetime.date.today() - datetime.timedelta(days=10)
        conn.execute(
            """
            INSERT INTO activities
                (athlete_id, activity_type, started_at, distance_km, duration_sec,
                 avg_pace_sec_per_km, workout_type, source, fetched_at)
            VALUES (?, 'running', ?, 10.0, 3000, 300, 'race', 'fit_manual', ?)
            """,
            (
                athlete_id,
                f"{recent_race_date.isoformat()}T07:00:00",
                f"{recent_race_date.isoformat()}T07:00:00",
            ),
        )
        conn.commit()

        result = generate_training_plan(
            _fake_ctx(conn),
            start_date="2026-09-01",
            total_weeks=16,
            days_per_week=4,
        )
        self.assertTrue(result["available"])
        self.assertGreater(len(result["schedule"]), 0)
        for day in result["schedule"]:
            self.assertIsInstance(day["date"], str)
            datetime.date.fromisoformat(day["date"])

        active = get_active_training_plan(_fake_ctx(conn))
        self.assertEqual(len(active), len(result["training_plan_ids"]))


class CoachPromptTest(unittest.TestCase):
    """行為約束層（教練人設）的內容測試。

    這些斷言的用途是**防止約束被無聲刪除**——每一條都對應 TODO.md Phase 3
    「必須寫進約束的項目」，若未來重寫 prompt 時漏掉任一條，測試會失敗。
    測的是「這條約束有被寫進去」，不是「措辭長什麼樣」。
    """

    def setUp(self):
        self.text = coach()

    def test_confidence_low_must_be_disclosed(self):
        self.assertIn("confidence.level", self.text)
        self.assertIn("low", self.text)
        self.assertIn("biases", self.text)

    def test_unassessable_dimensions_must_not_be_concluded(self):
        self.assertIn("not_assessed", self.text)
        self.assertIn("assessability.blocked", self.text)
        self.assertIn("workout_type", self.text)

    def test_forbids_inventing_thresholds(self):
        self.assertIn("不得自行發明閾值", self.text)

    def test_hr_drift_5pct_threshold_explicitly_rejected(self):
        """心率漂移不可套用 5% 門檻——本專案算的非 Pa:HR。"""
        self.assertIn("Pa:HR", self.text)
        self.assertIn("5%", self.text)
        self.assertIn("6.2%", self.text)

    def test_acwr_ratio_thresholds_rejected(self):
        self.assertIn("acwrFactorPercent", self.text)
        self.assertIn("1.3", self.text)

    def test_must_restate_facts_used(self):
        self.assertIn("事實清單", self.text)

    def test_no_medical_advice(self):
        self.assertIn("醫療", self.text)

    def test_never_modifies_plan_on_its_own(self):
        self.assertIn("不主動改課表", self.text)
        self.assertIn("generate_training_plan", self.text)

    def test_no_speculation_without_data(self):
        self.assertIn("臆測", self.text)

    def test_style_layer_kept_as_separate_block(self):
        """風格層擴充點：約束與其他區塊須分開組裝，不可混寫成一段。

        承 TODO.md「組裝方式須預留風格層擴充點」——這裡斷言 coach() 是由
        獨立常數組裝而成，未來加 _STYLE_LAYER 不需改寫行為約束層。
        """
        self.assertIn(_BEHAVIOR_CONSTRAINTS, self.text)
        self.assertIn(_ROLE, self.text)
        # 行為約束層可獨立抽換：把它從組裝結果拿掉後，其餘區塊仍完整存在，
        # 代表約束沒有跟角色/輸出格式混寫在同一段裡。
        without = self.text.replace(_BEHAVIOR_CONSTRAINTS, "")
        self.assertIn(_ROLE, without)
        self.assertNotIn("不得自行發明閾值", without)


if __name__ == "__main__":
    unittest.main()


class TestSaveExternalWorkoutsTool(unittest.TestCase):
    """N-2：外部課表寫入型 tool 的薄封裝行為。"""

    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)
        self.conn.commit()

    def test_saves_and_reports_count(self):
        result = save_external_workouts(
            _fake_ctx(self.conn),
            workouts=[
                {"planned_date": "2026-09-10", "workout_type": "tempo", "planned_distance_km": 8.0},
                {"planned_date": "2026-09-13", "workout_type": "lsd", "planned_distance_km": 18.0},
            ],
        )
        self.assertTrue(result["saved"])
        self.assertEqual(result["count"], 2)

        active = get_active_training_plan(_fake_ctx(self.conn))
        self.assertEqual(len(active), 2)
        self.assertTrue(all(row["plan_source"] == "external" for row in active))

    def test_empty_list_is_rejected_without_writing(self):
        result = save_external_workouts(_fake_ctx(self.conn), workouts=[])
        self.assertFalse(result["saved"])
        self.assertEqual(get_active_training_plan(_fake_ctx(self.conn)), [])

    def test_invalid_workout_type_returns_reason_not_exception(self):
        """課種非法時回傳可讀原因，讓 AI 能轉述給學員並改問，而不是拋例外。"""
        result = save_external_workouts(
            _fake_ctx(self.conn),
            workouts=[{"planned_date": "2026-09-10", "workout_type": "配速跑"}],
        )
        self.assertFalse(result["saved"])
        self.assertIn("workout_type", result["reason"])
        self.assertEqual(get_active_training_plan(_fake_ctx(self.conn)), [])

    def test_no_athlete_returns_reason(self):
        conn = get_connection(":memory:")
        result = save_external_workouts(
            _fake_ctx(conn), workouts=[{"planned_date": "2026-09-10", "workout_type": "easy"}]
        )
        self.assertFalse(result["saved"])


class TestContextNoteTools(unittest.TestCase):
    """§7.5 軟性輸入的 MCP 介面。"""

    def setUp(self):
        self.conn = get_connection(":memory:")
        self.athlete_id = _build_athlete(self.conn)
        self.conn.commit()

    def test_add_then_get_roundtrip(self):
        result = add_context_note(
            _fake_ctx(self.conn), note="這週出差，睡不好", start_date="2026-09-01",
            end_date="2026-09-05",
        )
        self.assertTrue(result["saved"])

        notes = get_context_notes(
            _fake_ctx(self.conn), start_date="2026-09-03", end_date="2026-09-03"
        )
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["note"], "這週出差，睡不好")

    def test_empty_note_returns_reason(self):
        result = add_context_note(_fake_ctx(self.conn), note="  ", start_date="2026-09-01")
        self.assertFalse(result["saved"])

    def test_get_returns_empty_when_no_athlete(self):
        conn = get_connection(":memory:")
        self.assertEqual(
            get_context_notes(_fake_ctx(conn), start_date="2026-09-01", end_date="2026-09-05"), []
        )


class TestCoachPromptMentionsNewTools(unittest.TestCase):
    """行為約束須涵蓋新增的寫入型 tool——約束若沒跟上，等於描述了一個
    與實際 tool 清單不符的世界（迴歸：先前寫著「唯一的寫入型 tool」）。"""

    def test_constraints_cover_external_plan_and_context_notes(self):
        text = _BEHAVIOR_CONSTRAINTS
        self.assertIn("save_external_workouts", text)
        self.assertIn("add_context_note", text)
        self.assertNotIn("唯一的寫入型 tool", text)
