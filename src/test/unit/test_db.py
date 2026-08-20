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
    """
    conn = sqlite3.connect(db_path)
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
        """既有資料庫遷移前的舊資料，is_active 應被回填為 1（生效中），而非 NULL。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            _build_legacy_training_plan_db(db_path)

            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT is_active FROM training_plan WHERE plan_source = 'ai_coach'"
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

    def test_legacy_database_retains_old_plan_source_values_after_migration(self):
        """既有資料庫的舊 plan_source 值（ai_coach）遷移後仍可讀取，不強制轉換舊資料。

        （ALTER TABLE 無法修改既有 CHECK 約束，故既有資料庫的 CHECK 仍是舊版；
        本測試只確認既有資料不會因遷移而遺失或損毀，不驗證 CHECK 本身是否更新。）
        """
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            _build_legacy_training_plan_db(db_path)

            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT plan_source FROM training_plan"
                ).fetchone()
                self.assertEqual(row["plan_source"], "ai_coach")
            finally:
                conn.close()


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
