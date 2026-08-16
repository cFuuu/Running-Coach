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
    sleep_duration_sec  INTEGER,
    sleep_quality       TEXT,
    stress_avg          INTEGER,
    body_battery_max    INTEGER,
    body_battery_min    INTEGER,
    steps               INTEGER,
    source               TEXT NOT NULL CHECK (source IN ('fit_manual', 'garmin_export', 'garmin_mcp', 'strava')),
    source_version        TEXT,
    fetched_at            TEXT NOT NULL,
    UNIQUE (athlete_id, date, source)
);

CREATE TABLE IF NOT EXISTS training_plan (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id                  INTEGER NOT NULL REFERENCES athlete_profile(id),
    planned_date                TEXT NOT NULL,
    workout_type                TEXT NOT NULL CHECK (workout_type IN ('easy', 'tempo', 'interval', 'lsd', 'race', 'rest', 'strength', 'cross_training')),
    planned_distance_km         REAL,
    planned_duration_sec        INTEGER,
    planned_pace_sec_per_km     INTEGER,
    notes                       TEXT,
    plan_source                 TEXT NOT NULL CHECK (plan_source IN ('ai_coach', 'running_club')),
    linked_activity_id          INTEGER REFERENCES activities(id),
    created_at                  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_activities_athlete_date ON activities (athlete_id, started_at);
CREATE INDEX IF NOT EXISTS idx_daily_wellness_athlete_date ON daily_wellness (athlete_id, date);
CREATE INDEX IF NOT EXISTS idx_training_plan_athlete_date ON training_plan (athlete_id, planned_date);
