PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS athlete_profile (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    birth_year      INTEGER,
    height_cm       REAL,
    weight_kg       REAL,
    body_fat_pct    REAL,
    max_hr_bpm      INTEGER,
    max_hr_source   TEXT CHECK (max_hr_source IN ('watch_display', 'measured', 'age_formula', 'observed_from_data')),
    resting_hr_bpm  INTEGER,
    updated_at      TEXT NOT NULL
);

-- 匯入來源的帳號識別對應到內部 athlete_id，讓 parser 重複匯入同一人資料時能正確歸戶，
-- 而不是每次都新建一筆 athlete_profile。一位 athlete 可能同時有多個來源身分（Garmin + Strava）。
CREATE TABLE IF NOT EXISTS athlete_source_identity (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id      INTEGER NOT NULL REFERENCES athlete_profile(id),
    source          TEXT NOT NULL CHECK (source IN ('fit_manual', 'garmin_export', 'garmin_mcp', 'strava')),
    external_ref    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (source, external_ref)
);

-- 每位 athlete、每個指標的實際可用起訖日——不同指標的歷史回溯長度差異很大
-- （例如同一支錶，活動紀錄可以有 7 年，但 SpO2/HRV 這類較新功能可能只有幾個月），
-- 規則引擎判斷前應先查這張表，而不是查了 daily_wellness 才發現該區間是空的。
CREATE TABLE IF NOT EXISTS metric_coverage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id      INTEGER NOT NULL REFERENCES athlete_profile(id),
    metric_name     TEXT NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('fit_manual', 'garmin_export', 'garmin_mcp', 'strava')),
    earliest_date   TEXT NOT NULL,
    latest_date     TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (athlete_id, metric_name, source)
);

CREATE TABLE IF NOT EXISTS activities (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id              INTEGER NOT NULL REFERENCES athlete_profile(id),
    external_id             TEXT,
    activity_type           TEXT NOT NULL,
    title                   TEXT,
    started_at              TEXT NOT NULL,
    distance_km             REAL,
    duration_sec            INTEGER,
    moving_time_sec         INTEGER,
    avg_hr_bpm              INTEGER,
    max_hr_bpm              INTEGER,
    aerobic_te              REAL,
    avg_cadence_spm         INTEGER,
    max_cadence_spm         INTEGER,
    avg_pace_sec_per_km     INTEGER,
    best_pace_sec_per_km    INTEGER,
    elevation_gain_m        REAL,
    elevation_loss_m        REAL,
    calories                INTEGER,
    reps                    INTEGER,
    sets                    INTEGER,
    source                  TEXT NOT NULL CHECK (source IN ('fit_manual', 'garmin_export', 'garmin_mcp', 'strava')),
    source_version          TEXT,
    fetched_at              TEXT NOT NULL,
    has_wellness_data       INTEGER NOT NULL DEFAULT 0 CHECK (has_wellness_data IN (0, 1)),
    raw_data_json           TEXT,
    -- 實際練了哪種課表（由分圈配速結構自動推測，見 workout_classifier.py）。
    -- 與 training_plan.workout_type（原本「計畫」練什麼）分開，兩者比對即可看出課表遵從度。
    workout_type            TEXT CHECK (workout_type IN ('easy', 'tempo', 'interval', 'lsd', 'race', 'recovery', 'unknown')),
    workout_type_source     TEXT CHECK (workout_type_source IN ('auto', 'manual')),
    -- NULL distance_km bypasses this UNIQUE constraint (SQL: NULL != NULL) — fine today since
    -- every known activity_type populates distance_km (0 for strength), but a future GPS-less
    -- type with a NULL distance would silently skip dedup.
    UNIQUE (athlete_id, started_at, activity_type, distance_km)
);

CREATE TABLE IF NOT EXISTS daily_wellness (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id          INTEGER NOT NULL REFERENCES athlete_profile(id),
    date                TEXT NOT NULL,
    resting_hr_bpm      INTEGER,
    hrv_ms              REAL,
    hrv_weekly_avg_ms   REAL,
    spo2_pct            REAL,
    skin_temp_c         REAL,
    respiration_rate    REAL,
    sleep_duration_sec  INTEGER,
    sleep_quality       TEXT,
    sleep_score         INTEGER,
    stress_avg          INTEGER,
    -- stress_avg 來自睡眠期間平均壓力（sleepData.avgSleepStress）；all_day_stress_avg
    -- 來自 UDSFile 的全天平均壓力（allDayStress.aggregatorList[type=TOTAL]），
    -- 兩者範疇不同，不可互相取代，故分開存欄位。
    all_day_stress_avg INTEGER,
    body_battery_max    INTEGER,
    body_battery_min    INTEGER,
    steps               INTEGER,
    training_readiness_score   INTEGER,
    recovery_time_hours        REAL,
    acwr                       REAL,
    source               TEXT NOT NULL CHECK (source IN ('fit_manual', 'garmin_export', 'garmin_mcp', 'strava')),
    source_version        TEXT,
    fetched_at            TEXT NOT NULL,
    UNIQUE (athlete_id, date, source)
);

-- 每公里（或每圈）分段，來源為 FIT 檔的 lap 訊息。
-- 注意：這與 activities.raw_data_json 裡的 splits 不同——後者是使用者手動按錶的
-- 不規則分段（實測 266 場中僅 94 場有多於一段），FIT 的 lap 才是整齊的每公里分圈。
CREATE TABLE IF NOT EXISTS activity_laps (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id         INTEGER NOT NULL REFERENCES activities(id),
    lap_index           INTEGER NOT NULL,
    distance_km         REAL,
    duration_sec        REAL,
    pace_sec_per_km     INTEGER,
    avg_hr_bpm          INTEGER,
    max_hr_bpm          INTEGER,
    UNIQUE (activity_id, lap_index)
);

-- 逐秒紀錄降頻後保存（預設每 10 秒一筆），供配速/心率曲線與心率漂移分析。
-- 不存完整逐秒：266 場 × 約 9000 筆 ≈ 240 萬列，對畫圖與漂移計算並無實益。
CREATE TABLE IF NOT EXISTS activity_records (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id         INTEGER NOT NULL REFERENCES activities(id),
    elapsed_sec         INTEGER NOT NULL,
    distance_km         REAL,
    hr_bpm              INTEGER,
    pace_sec_per_km     INTEGER,
    cadence_spm         INTEGER,
    altitude_m          REAL,
    UNIQUE (activity_id, elapsed_sec)
);

-- plan_source：'generated'＝系統排程器產生，'external'＝外部來源（如跟團課表），
-- 排程器產生課表時遇到已標記 external 的日期會直接跳過，不覆蓋。
--
-- 版本歷史保留（2026-08-18 grill 決策）：每次產生或調整課表都是新的一批列，
-- 不覆蓋刪除舊列。is_active 標示這筆列是否為目前生效版本；superseded_by
-- 指向取代此列的新版本列，讓「原計畫 vs 調整後計畫 vs 實際執行」的偏差
-- 比對（呼應 Phase 3 評估回饋迴圈）能看到版本演進過程。
CREATE TABLE IF NOT EXISTS training_plan (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id                  INTEGER NOT NULL REFERENCES athlete_profile(id),
    planned_date                TEXT NOT NULL,
    workout_type                TEXT NOT NULL CHECK (workout_type IN ('easy', 'tempo', 'interval', 'lsd', 'race', 'rest', 'strength', 'cross_training')),
    planned_distance_km         REAL,
    planned_duration_sec        INTEGER,
    planned_pace_sec_per_km     INTEGER,
    notes                       TEXT,
    plan_source                 TEXT NOT NULL CHECK (plan_source IN ('generated', 'external')),
    linked_activity_id          INTEGER REFERENCES activities(id),
    is_active                   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    superseded_by               INTEGER REFERENCES training_plan(id),
    created_at                  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_activity_laps_activity ON activity_laps (activity_id);
CREATE INDEX IF NOT EXISTS idx_activity_records_activity ON activity_records (activity_id, elapsed_sec);
CREATE INDEX IF NOT EXISTS idx_activities_athlete_date ON activities (athlete_id, started_at);
CREATE INDEX IF NOT EXISTS idx_daily_wellness_athlete_date ON daily_wellness (athlete_id, date);
CREATE INDEX IF NOT EXISTS idx_training_plan_athlete_date ON training_plan (athlete_id, planned_date);
CREATE INDEX IF NOT EXISTS idx_athlete_source_identity_athlete ON athlete_source_identity (athlete_id);
CREATE INDEX IF NOT EXISTS idx_metric_coverage_athlete ON metric_coverage (athlete_id, metric_name);
