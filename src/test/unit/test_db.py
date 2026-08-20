"""db.py 的 _apply_column_migrations() 單元測試——聚焦 training_plan 的
plan_source 命名修正與版本化欄位（is_active/superseded_by）遷移行為，
以及 athlete_profile 的個人化恢復閾值欄位（Issue #16）遷移行為。

全部使用合成測試資料，不含真實個人資料。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.main.python.models.db import get_connection


def _build_legacy_training_plan_db(db_path: Path) -> None:
    """建立一個模擬「Ticket A 之前」既有資料庫的最小 training_plan 表：
    舊版 plan_source CHECK（ai_coach/running_club），無 is_active/superseded_by。

    也建立最小的 athlete_profile 表並插入 id=1 的一筆——training_plan.athlete_id
    有 REFERENCES athlete_profile(id) 外鍵，重建表格遷移（_migrate_training_plan_check_
    constraint）在 PRAGMA foreign_keys=ON 下重新插入資料時會檢查這個外鍵，
    fixture 若不建對應的 athlete_profile 列會造成遷移本身誤判為外鍵違反。
    """
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE athlete_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO athlete_profile (id, name, updated_at) VALUES (1, '測試學員', '2026-01-01T00:00:00')"
    )
    conn.execute(
        """
        CREATE TABLE training_plan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            athlete_id INTEGER NOT NULL,
            planned_date TEXT NOT NULL,
            workout_type TEXT NOT NULL,
            planned_distance_km REAL,
            planned_duration_sec INTEGER,
            planned_pace_sec_per_km INTEGER,
            notes TEXT,
            plan_source TEXT NOT NULL CHECK (plan_source IN ('ai_coach', 'running_club')),
            linked_activity_id INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )
    # 插入一筆舊資料，用舊版合法值，確認遷移不會破壞既有資料。
    conn.execute(
        """
        INSERT INTO training_plan
            (athlete_id, planned_date, workout_type, plan_source, created_at)
        VALUES (1, '2026-01-01', 'easy', 'ai_coach', '2026-01-01T00:00:00')
        """
    )
    conn.commit()
    conn.close()


class TestTrainingPlanSchemaMigration(unittest.TestCase):
    def test_new_database_allows_generated_and_external(self):
        """全新資料庫（走 schema.sql）應允許新的 plan_source 合法值。"""
        conn = get_connection(":memory:")
        conn.execute(
            """
            INSERT INTO athlete_profile (name, updated_at)
            VALUES ('測試學員', '2026-01-01T00:00:00')
            """
        )
        athlete_id = conn.execute("SELECT id FROM athlete_profile").fetchone()["id"]

        conn.execute(
            """
            INSERT INTO training_plan
                (athlete_id, planned_date, workout_type, plan_source, created_at)
            VALUES (?, '2026-01-01', 'easy', 'generated', '2026-01-01T00:00:00')
            """,
            (athlete_id,),
        )
        conn.execute(
            """
            INSERT INTO training_plan
                (athlete_id, planned_date, workout_type, plan_source, created_at)
            VALUES (?, '2026-01-02', 'easy', 'external', '2026-01-02T00:00:00')
            """,
            (athlete_id,),
        )
        conn.commit()

        rows = conn.execute("SELECT plan_source FROM training_plan ORDER BY planned_date").fetchall()
        self.assertEqual([r["plan_source"] for r in rows], ["generated", "external"])

    def test_new_database_rejects_legacy_plan_source_values(self):
        """全新資料庫不應再允許舊的 ai_coach/running_club 值。"""
        conn = get_connection(":memory:")
        conn.execute(
            """
            INSERT INTO athlete_profile (name, updated_at)
            VALUES ('測試學員', '2026-01-01T00:00:00')
            """
        )
        athlete_id = conn.execute("SELECT id FROM athlete_profile").fetchone()["id"]

        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO training_plan
                    (athlete_id, planned_date, workout_type, plan_source, created_at)
                VALUES (?, '2026-01-01', 'easy', 'ai_coach', '2026-01-01T00:00:00')
                """,
                (athlete_id,),
            )

    def test_new_database_defaults_is_active_to_one(self):
        conn = get_connection(":memory:")
        conn.execute(
            """
            INSERT INTO athlete_profile (name, updated_at)
            VALUES ('測試學員', '2026-01-01T00:00:00')
            """
        )
        athlete_id = conn.execute("SELECT id FROM athlete_profile").fetchone()["id"]

        conn.execute(
            """
            INSERT INTO training_plan
                (athlete_id, planned_date, workout_type, plan_source, created_at)
            VALUES (?, '2026-01-01', 'easy', 'generated', '2026-01-01T00:00:00')
            """,
            (athlete_id,),
        )
        conn.commit()

        row = conn.execute("SELECT is_active FROM training_plan").fetchone()
        self.assertEqual(row["is_active"], 1)

    def test_superseded_by_references_another_row(self):
        conn = get_connection(":memory:")
        conn.execute(
            """
            INSERT INTO athlete_profile (name, updated_at)
            VALUES ('測試學員', '2026-01-01T00:00:00')
            """
        )
        athlete_id = conn.execute("SELECT id FROM athlete_profile").fetchone()["id"]

        conn.execute(
            """
            INSERT INTO training_plan
                (athlete_id, planned_date, workout_type, plan_source, is_active, created_at)
            VALUES (?, '2026-01-01', 'easy', 'generated', 0, '2026-01-01T00:00:00')
            """,
            (athlete_id,),
        )
        old_id = conn.execute("SELECT id FROM training_plan").fetchone()["id"]

        conn.execute(
            """
            INSERT INTO training_plan
                (athlete_id, planned_date, workout_type, plan_source, is_active, created_at)
            VALUES (?, '2026-01-01', 'tempo', 'generated', 1, '2026-01-02T00:00:00')
            """,
            (athlete_id,),
        )
        new_id = conn.execute(
            "SELECT id FROM training_plan WHERE workout_type = 'tempo'"
        ).fetchone()["id"]

        conn.execute(
            "UPDATE training_plan SET superseded_by = ? WHERE id = ?", (new_id, old_id)
        )
        conn.commit()

        row = conn.execute(
            "SELECT superseded_by FROM training_plan WHERE id = ?", (old_id,)
        ).fetchone()
        self.assertEqual(row["superseded_by"], new_id)

    def test_legacy_database_gains_new_columns_after_migration(self):
        """既有資料庫（舊版 schema，無 is_active/superseded_by）套用遷移後可正常讀寫新欄位。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            _build_legacy_training_plan_db(db_path)

            conn = get_connection(db_path)
            try:
                existing_columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(training_plan)")
                }
                self.assertIn("is_active", existing_columns)
                self.assertIn("superseded_by", existing_columns)
            finally:
                conn.close()

    def test_legacy_database_existing_rows_backfilled_to_active(self):
        """既有資料庫遷移前的舊資料，is_active 應被回填為 1（生效中），而非 NULL。

        遷移後 plan_source 已從 'ai_coach' 映射為 'generated'（見上方 CHECK
        重建遷移），故用 planned_date 而非舊 plan_source 值來定位這筆舊資料。
        """
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            _build_legacy_training_plan_db(db_path)

            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT is_active FROM training_plan WHERE planned_date = '2026-01-01'"
                ).fetchone()
                self.assertEqual(row["is_active"], 1)
            finally:
                conn.close()

    def test_migration_is_idempotent(self):
        """遷移機制可重複執行不報錯（比照既有 _COLUMN_MIGRATIONS 冪等性慣例）。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            _build_legacy_training_plan_db(db_path)

            first_conn = get_connection(db_path)
            first_conn.close()
            # 第二次呼叫不應報錯（欄位已存在、回填已完成）。
            conn = get_connection(db_path)
            try:
                existing_columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(training_plan)")
                }
                self.assertIn("is_active", existing_columns)
            finally:
                conn.close()

    def test_legacy_database_old_plan_source_value_is_mapped_to_generated(self):
        """既有資料庫的舊 plan_source 值（ai_coach）遷移後對應到新語意的 'generated'。

        2026-08-20 發現：純 ALTER TABLE ADD COLUMN 無法修改既有 CHECK 約束，
        導致舊資料庫即使補上 is_active/superseded_by，plan_source 仍卡在舊
        CHECK（'ai_coach'/'running_club'），連合法新值 'generated' 都會被
        擋下——見 _migrate_training_plan_check_constraint()：本函式改為重建
        整張表，並把舊值一律映射為 'generated'（舊語意的 AI 教練／跑團課表
        皆非本輪才新增的「外部課表協調」語意，故不臆測哪些舊列其實是 external）。
        """
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            _build_legacy_training_plan_db(db_path)

            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT plan_source FROM training_plan"
                ).fetchone()
                self.assertEqual(row["plan_source"], "generated")
            finally:
                conn.close()

    def test_legacy_database_check_constraint_accepts_new_values_after_migration(self):
        """遷移後的 CHECK 約束應是新版，能正確接受 'generated'/'external'，
        且正確拒絕已被取代的舊值——這是本次真實資料庫實際踩到的問題
        （旁修 is_active/superseded_by 沒有解決 CHECK 本身，見上方測試說明）。
        """
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            _build_legacy_training_plan_db(db_path)

            conn = get_connection(db_path)
            try:
                # 新語意的合法值應可成功寫入，不再被舊 CHECK 擋下。
                conn.execute(
                    """
                    INSERT INTO training_plan
                        (athlete_id, planned_date, workout_type, plan_source, created_at)
                    VALUES (1, '2026-09-01', 'easy', 'external', '2026-09-01T00:00:00')
                    """
                )
                conn.commit()

                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        """
                        INSERT INTO training_plan
                            (athlete_id, planned_date, workout_type, plan_source, created_at)
                        VALUES (1, '2026-09-02', 'easy', 'ai_coach', '2026-09-02T00:00:00')
                        """
                    )
            finally:
                conn.close()

    def test_legacy_database_row_ids_and_columns_preserved_after_rebuild(self):
        """重建表格後，既有列的 id 與其餘欄位值應保持不變（供 linked_activity_id
        等外鍵／既有引用不失效）。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            _build_legacy_training_plan_db(db_path)

            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT id, planned_date, workout_type FROM training_plan"
                ).fetchone()
                self.assertEqual(row["id"], 1)
                self.assertEqual(row["planned_date"], "2026-01-01")
                self.assertEqual(row["workout_type"], "easy")
            finally:
                conn.close()

    def test_rebuild_migration_is_idempotent(self):
        """CHECK 重建遷移可重複執行不報錯（第二次呼叫時 CHECK 已是新版，no-op）。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            _build_legacy_training_plan_db(db_path)

            first_conn = get_connection(db_path)
            first_conn.close()

            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT plan_source FROM training_plan"
                ).fetchone()
                self.assertEqual(row["plan_source"], "generated")
                count = conn.execute("SELECT COUNT(*) AS c FROM training_plan").fetchone()["c"]
                self.assertEqual(count, 1)  # 沒有因重複遷移而重複插入或遺失資料
            finally:
                conn.close()

    def test_new_database_is_unaffected_by_rebuild_migration(self):
        """全新資料庫（走 schema.sql，CHECK 本來就是新版）不會觸發重建邏輯。"""
        conn = get_connection(":memory:")
        conn.execute(
            "INSERT INTO athlete_profile (name, updated_at) VALUES ('測試學員', '2026-01-01T00:00:00')"
        )
        athlete_id = conn.execute("SELECT id FROM athlete_profile").fetchone()["id"]
        conn.execute(
            """
            INSERT INTO training_plan
                (athlete_id, planned_date, workout_type, plan_source, created_at)
            VALUES (?, '2026-01-01', 'easy', 'generated', '2026-01-01T00:00:00')
            """,
            (athlete_id,),
        )
        conn.commit()
        row = conn.execute("SELECT plan_source FROM training_plan").fetchone()
        self.assertEqual(row["plan_source"], "generated")


def _build_legacy_athlete_profile_db(db_path: Path) -> None:
    """建立一個模擬「Issue #16 之前」既有資料庫的最小 athlete_profile 表：
    無 high_risk_consecutive_training_days 欄位。
    """
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE athlete_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            max_hr_bpm INTEGER,
            resting_hr_bpm INTEGER,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO athlete_profile (name, updated_at)
        VALUES ('測試學員', '2026-01-01T00:00:00')
        """
    )
    conn.commit()
    conn.close()


class TestAthleteProfileRecoveryThresholdMigration(unittest.TestCase):
    def test_new_database_has_recovery_threshold_column(self):
        """全新資料庫（走 schema.sql）應含新增的恢復閾值欄位。"""
        conn = get_connection(":memory:")
        existing_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(athlete_profile)")
        }
        self.assertIn("high_risk_consecutive_training_days", existing_columns)

    def test_new_database_column_defaults_to_null(self):
        conn = get_connection(":memory:")
        conn.execute(
            """
            INSERT INTO athlete_profile (name, updated_at)
            VALUES ('測試學員', '2026-01-01T00:00:00')
            """
        )
        conn.commit()
        row = conn.execute(
            "SELECT high_risk_consecutive_training_days FROM athlete_profile"
        ).fetchone()
        self.assertIsNone(row["high_risk_consecutive_training_days"])

    def test_new_database_can_write_and_read_threshold_value(self):
        conn = get_connection(":memory:")
        conn.execute(
            """
            INSERT INTO athlete_profile
                (name, high_risk_consecutive_training_days, updated_at)
            VALUES ('測試學員', 6, '2026-01-01T00:00:00')
            """
        )
        conn.commit()
        row = conn.execute(
            "SELECT high_risk_consecutive_training_days FROM athlete_profile"
        ).fetchone()
        self.assertEqual(row["high_risk_consecutive_training_days"], 6)

    def test_legacy_database_gains_new_column_after_migration(self):
        """既有資料庫（無此欄位）套用遷移後可正常讀寫新欄位。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy_profile.db"
            _build_legacy_athlete_profile_db(db_path)

            conn = get_connection(db_path)
            try:
                existing_columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(athlete_profile)")
                }
                self.assertIn("high_risk_consecutive_training_days", existing_columns)

                row = conn.execute(
                    "SELECT high_risk_consecutive_training_days FROM athlete_profile"
                ).fetchone()
                self.assertIsNone(row["high_risk_consecutive_training_days"])

                conn.execute(
                    "UPDATE athlete_profile SET high_risk_consecutive_training_days = 5"
                )
                conn.commit()
                row = conn.execute(
                    "SELECT high_risk_consecutive_training_days FROM athlete_profile"
                ).fetchone()
                self.assertEqual(row["high_risk_consecutive_training_days"], 5)
            finally:
                conn.close()

    def test_migration_is_idempotent(self):
        """遷移機制可重複執行不報錯。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy_profile.db"
            _build_legacy_athlete_profile_db(db_path)

            first_conn = get_connection(db_path)
            first_conn.close()
            conn = get_connection(db_path)
            try:
                existing_columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(athlete_profile)")
                }
                self.assertIn("high_risk_consecutive_training_days", existing_columns)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
