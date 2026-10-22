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
    -- 個人化恢復閾值（Issue #15/#16）：連續訓練達此天數視為高風險，觸發
    -- readiness 判斷邏輯的警訊。NULL 代表尚未人工設定，判斷邏輯需降級為
    -- 使用預設閾值（預設值由判斷邏輯本體決定，不在 schema 層硬編碼）。
    high_risk_consecutive_training_days  INTEGER,
    -- 手錶上設定的心率區間邊界（%HRR，Garmin「Based On %HRR」），JSON 陣列
    -- 6 個數字：Z1 下界、Z1/Z2、Z2/Z3、Z3/Z4、Z4/Z5、Z5 上界，例如
    -- [0.54, 0.59, 0.74, 0.83, 0.88, 1.0]。只用於把區間換算成 bpm 顯示；
    -- 每場的區間停留時間是手錶自己算好的（hrTimeInZone_N），不依賴此欄。
    -- NULL 代表未設定，顯示端不換算 bpm、也不套任何預設值。
    hr_zone_hrr_bounds_json TEXT,
    -- 性別（MALE／FEMALE），取自 Garmin 個人資料。只用於查 VO2max 的
    -- Cooper Institute 分級表（Garmin 手冊同一份表，依年齡與性別分欄）。
    -- NULL 代表未取得，顯示端不給分級。
    sex             TEXT,
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
    -- 無氧訓練效果（Anaerobic Training Effect，0–5）。與既有的 aerobic_te
    -- （有氧訓練效果）成對：只看有氧等於只看到訓練刺激的一半，間歇課的
    -- 品質主要反映在無氧側。Garmin（Firstbeat）衍生值，取用須帶
    -- `garmin_blackbox` 標記。
    anaerobic_te            REAL,
    -- 單場運動負荷（Activity Training Load）。Garmin 對這一場的負荷量化值，
    -- 是累加成 acute load（短期訓練負荷）的來源。單位為 Garmin 自訂尺度，
    -- 不可與本專案的 Banister TRIMP 負荷混用或相加。
    activity_training_load  REAL,
    -- 訓練效果標籤（如 AEROBIC_BASE／TEMPO／VO2MAX）。Garmin 對本場訓練
    -- 性質的分類，可與 workout_type（本專案分類器的結果）交叉驗證。
    training_effect_label   TEXT,
    -- 坡度校正配速（Grade Adjusted Pace，秒/公里）。把上下坡換算成等效
    -- 平地配速，讓不同路線的配速可比。來源為 API 的 avgGradeAdjustedSpeed
    -- （公尺/秒），換算方式與 avg_pace_sec_per_km 一致。
    avg_grade_adj_pace_sec_per_km INTEGER,
    -- --- 跑步動態（Running Dynamics）：需錶端支援，實測覆蓋率約 72% ---
    -- ⚠️ 左右平衡（GCT Balance）在 Forerunner 965 單錶（無 HRM-Pro／RD Pod）
    -- 下不產生，實測 271 場覆蓋率 0%，故不建欄位。
    -- 觸地時間（Ground Contact Time，毫秒）
    avg_ground_contact_time_ms  REAL,
    -- 垂直振幅（Vertical Oscillation，公分）
    avg_vertical_oscillation_cm REAL,
    -- 垂直步幅比（Vertical Ratio，%）＝垂直振幅 ÷ 步幅，跑姿經濟性指標
    avg_vertical_ratio_pct      REAL,
    -- 平均步幅（Stride Length，公分）
    avg_stride_length_cm        REAL,
    -- --- 跑步功率（Running Power，瓦特）---
    avg_power_watts         REAL,
    max_power_watts         REAL,
    -- 標準化功率（Normalized Power）：加權後的功率，反映變動強度的生理成本
    norm_power_watts        REAL,
    -- --- 環境溫度（Temperature）欄位 ---
    -- ⚠️ 不可當實際氣溫使用：這是手錶的**手腕體溫**讀數，非環境溫度感測器。
    -- 依 N-3 查證（2026-09-02），四年份資料中此欄從未低於 21°C，且一月台北
    -- 的紀錄卻出現 max 29°C——與該月實際氣溫明顯不符，屬手錶貼腕造成的
    -- 體溫污染值。存原始值可以（作為手錶量測的原始記錄），但**不可用它
    -- 判讀配速受氣溫影響的程度**，也不可包裝成「當天氣溫」呈現給使用者。
    min_temperature_c       REAL,
    max_temperature_c       REAL,
    -- 心率區間停留時間（hrTimeInZone_1..5，秒）。存 JSON 而非五個欄位：
    -- 它本質是一組陣列，攤成五欄會讓表變寬且無法隨區間定義調整。
    -- 格式：{"1": 203.994, "2": 5197.439, ...}
    hr_time_in_zone_json    TEXT,
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
    -- 比賽／測驗的官方成績（秒），僅 workout_type='race' 的場次需要時才人工填入。
    -- 手錶的 duration_sec 常與實際成績不同（忘了按停、起跑前就按開始），
    -- 但 duration_sec 是 Garmin 的原始紀錄、會被重新同步覆寫，且必須與該場
    -- 的配速／心率序列一致，故不改它，另存這一欄。對應距離視為該場的標準
    -- 比賽距離（3K／5K／10K／半馬／全馬）。最佳成績與 VDOT 有此值時優先採用。
    -- 同步程式不寫入此欄。
    race_official_time_sec  INTEGER,
    -- 比賽的官方距離（公里），僅在手錶距離偏離到歸錯距離類別時才填
    -- （例：2019 年舊錶 GPS 把半馬記成 22.30km，超出半馬容忍範圍）。
    -- 距離類別判斷優先用此值；與上一欄同樣不由同步程式寫入。
    race_official_distance_km REAL,
    -- NULL distance_km bypasses this UNIQUE constraint (SQL: NULL != NULL) — fine today since
    -- every known activity_type populates distance_km (0 for strength), but a future GPS-less
    -- type with a NULL distance would silently skip dedup.
    --
    -- ⚠️ 這條約束不足以擋掉跨來源的重複：API 回傳完整浮點距離
    -- （10.1585703125），官方匯出包存四捨五入值（10.1586），distance_km
    -- 不相等就撞不到，同一場活動會寫成兩列（2026-09-01 實測發生兩例）。
    -- 真正的去重鍵是下方的 external_id 唯一索引，本約束保留為次要防線
    -- （匯出包早期資料可能無 external_id 時仍有作用）。
    UNIQUE (athlete_id, started_at, activity_type, distance_km)
);

-- Garmin 的 activityId 是跨來源穩定的唯一鍵，為活動去重的主要依據。
-- 允許 NULL（手動建立、或未來其他來源無此 id 的活動不受此約束限制）。
CREATE UNIQUE INDEX IF NOT EXISTS idx_activities_external_id
    ON activities (athlete_id, external_id);

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
    -- ⚠️ 欄位名易誤導：此處存的是 Garmin 的 `acwrFactorPercent`
    -- （0–100 的 readiness 貢獻度分數），**不是**古典的急慢性負荷比。
    -- 真正的 ACWR（Acute:Chronic Workload Ratio，急慢性負荷比，0.8–1.3）
    -- 存於 training_status.acwr_ratio。兩者不可混用。
    acwr                       REAL,
    -- --- HRV 個人基準線（Heart Rate Variability，心率變異度，單位 ms）---
    -- 單一 HRV 讀數沒有意義，必須對照個人基準線才知道是否偏離。
    -- 三個門檻由 Garmin 依個人約 3 週歷史推算：
    --   低於 low_upper           → 偏低（Low）
    --   balanced_low ~ upper 之間 → 平衡（Balanced）
    hrv_baseline_low_upper       REAL,
    hrv_baseline_balanced_low    REAL,
    hrv_baseline_balanced_upper  REAL,
    -- HRV 狀態評級（BALANCED／UNBALANCED／LOW）。Garmin 黑盒衍生值。
    hrv_status                   TEXT,
    -- 夜間 5 分鐘最高 HRV 讀數
    hrv_last_night_5min_high     REAL,
    -- 短期訓練負荷（Acute Load）：近 7 日的訓練負荷累加。
    -- 與 training_status.acute_load 同源（皆為 Garmin 值），此欄取自
    -- training_readiness 端點，方便與同日 readiness 分數一起查詢。
    acute_load                   REAL,
    -- 近 7 日平均靜止心率（bpm），RHR 異常偵測的基準線。
    rhr_7day_avg_bpm             INTEGER,
    -- --- 睡眠階段拆解（秒）---
    sleep_deep_sec               INTEGER,
    sleep_light_sec              INTEGER,
    sleep_rem_sec                INTEGER,
    sleep_awake_sec              INTEGER,
    -- 起床時的身體電池（Body Battery，0–100）。Garmin 黑盒衍生值。
    body_battery_at_wake         INTEGER,
    -- --- Training Readiness 快照的時點資訊 ---
    -- ⚠️ 該端點每日回傳多筆快照，分數在一天內可劇烈變動（實測 2026-08-31：
    -- 起床 60 分、睡前 75 分）。本專案取「起床那筆」（AFTER_WAKEUP_RESET），
    -- 因為課表決策要問的是「今天能不能練」而非「恢復得如何」。
    -- 以下兩欄記錄實際採用的是哪一筆，讓下游知道分數代表的時點。
    readiness_input_context      TEXT,
    readiness_timestamp_local    TEXT,
    -- --- 2026-10-22：Garmin 網頁卡片所需欄位（皆取自既有端點，零額外請求）---
    -- Training Readiness 等級（LOW／MODERATE／HIGH／PRIME 等）與回饋代碼。
    -- Garmin 黑盒衍生值，僅供顯示。
    readiness_level              TEXT,
    readiness_feedback_short     TEXT,
    -- 主睡眠的入睡／起床時間（當地時間 ISO 字串）
    sleep_start_local            TEXT,
    sleep_end_local              TEXT,
    -- 睡眠期間平均呼吸率（brpm）；respiration_rate 是清醒平均
    respiration_sleep_avg        REAL,
    respiration_low              REAL,
    respiration_high             REAL,
    spo2_lowest                  REAL,
    spo2_latest                  REAL,
    -- 身體能量：當日最新值、充電量、消耗量
    body_battery_latest          INTEGER,
    body_battery_charged         INTEGER,
    body_battery_drained         INTEGER,
    -- 熱量（kcal）：活躍與靜態（基礎代謝）
    calories_active              INTEGER,
    calories_resting             INTEGER,
    -- 熱血時間（分鐘）：當日中等／高強度；目標為 Garmin 的每週目標
    intensity_minutes_moderate   INTEGER,
    intensity_minutes_vigorous   INTEGER,
    intensity_minutes_weekly_goal INTEGER,
    floors_ascended              REAL,
    floors_goal                  INTEGER,
    steps_goal                   INTEGER,
    source               TEXT NOT NULL CHECK (source IN ('fit_manual', 'garmin_export', 'garmin_mcp', 'strava')),
    source_version        TEXT,
    fetched_at            TEXT NOT NULL,
    UNIQUE (athlete_id, date, source)
);

-- 每日的日內序列（睡眠階段時間軸、壓力、身體能量、心率），來源為
-- get_sleep_data 的 sleepLevels、get_stress_data、get_heart_rates。
--
-- 為何獨立建表而非併入 daily_wellness：一天數百個點、形狀與每日摘要
-- 完全不同，放進 daily_wellness 會讓每列膨脹十倍以上，而絕大多數查詢
-- （趨勢、規則引擎）根本用不到。只有 Garmin 卡片的日內圖會讀。
--
-- points_json 格式依 kind：
--   sleep_levels：[[開始, 結束, 階段], ...]，時間為當地 "HH:MM"，
--                 階段 0=深眠 1=淺眠 2=REM 3=清醒（Garmin activityLevel）
--   stress／body_battery／heart_rate：[[當日第幾分鐘, 數值], ...]，當地時間；
--                 Garmin 以負值表示「無資料／活動中」的點，存成 null
-- 僅供顯示，規則引擎與 AI 教練不讀。
CREATE TABLE IF NOT EXISTS wellness_intraday (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id          INTEGER NOT NULL REFERENCES athlete_profile(id),
    date                TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('sleep_levels', 'stress', 'body_battery', 'heart_rate')),
    points_json         TEXT NOT NULL,
    source               TEXT NOT NULL CHECK (source IN ('fit_manual', 'garmin_export', 'garmin_mcp', 'strava')),
    source_version        TEXT,
    fetched_at            TEXT NOT NULL,
    UNIQUE (athlete_id, date, kind, source)
);

-- 每日訓練狀態與訓練負荷，來源為 Garmin 的 `get_training_status` 端點。
--
-- 為何獨立建表而非併入 daily_wellness（2026-09-02 決策）：
-- 1. daily_wellness 的語意是「身體／恢復數據」，本表是**訓練面**的衍生量，
--    來自不同端點、且原始結構帶 device 維度。
-- 2. 併入會讓 daily_wellness 的每日 upsert 必須處理兩組互不相干的空值模式。
-- 3. 最具體的理由：daily_wellness.acwr 已存了語意不同的東西（貢獻度分數），
--    同表再放一個 acwr_ratio 極易誤用。
--
-- ⚠️ 來源 payload 的 latestTrainingStatusData 與 metricsTrainingLoadBalanceDTOMap
-- 以 **deviceId 字串**為 key（非固定名稱），解析時須挑 primaryTrainingDevice=True
-- 的那筆，不可寫死 device id 或直接取第一個 key（換錶／多錶並用會取錯）。
CREATE TABLE IF NOT EXISTS training_status (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id          INTEGER NOT NULL REFERENCES athlete_profile(id),
    date                TEXT NOT NULL,
    -- 訓練狀態（Training Status）：Garmin 對整體訓練狀態的分類。
    -- code 為數值代碼，phrase 為文字（如 MAINTAINING_2＝維持、
    -- PRODUCTIVE＝高效、OVERREACHING＝過度訓練、STRAINED＝疲勞）。
    training_status_code    INTEGER,
    training_status_phrase  TEXT,
    -- Garmin 判定目前訓練狀態的起始日（sinceDate）；與逐日狀態回推的結果不同
    status_since_date       TEXT,
    -- 短期訓練負荷（Acute Load）：近 7 日負荷累加，反映當前疲勞累積。
    acute_load              REAL,
    -- 長期訓練負荷（Chronic Load）：近 4 週負荷累加，反映體能基礎。
    chronic_load            REAL,
    -- 急慢性負荷比（ACWR, Acute:Chronic Workload Ratio）＝短期負荷 ÷ 長期負荷。
    -- 古典區間 0.8–1.3 為「甜蜜點」，>1.5 常被視為受傷風險升高。
    -- ⚠️ 這才是真正的 ACWR 比值；daily_wellness.acwr 存的是另一回事。
    acwr_ratio              REAL,
    -- 長期負荷的最佳範圍下／上限，用於判斷「該加量還是減量」。
    chronic_load_optimal_min REAL,
    chronic_load_optimal_max REAL,
    -- 負荷比狀態（如 OPTIMAL＝最佳、LOW＝偏低、HIGH＝偏高）
    acwr_status             TEXT,
    -- 最大攝氧量（VO2 Max，ml/kg/min）精確值。整數值對訓練期的緩慢變化
    -- 缺乏解析度，故存 Garmin 提供的小數點版本。
    vo2max_precise          REAL,
    -- VO2Max 的實際量測日（可能早於本列的 date——Garmin 只在有足夠品質的
    -- 跑步後才更新，未更新的日子沿用舊值）。
    vo2max_date             TEXT,
    -- 熱適應（Heat Acclimation）百分比與趨勢（ACCLIMATIZING／DEACCLIMATIZING）。
    -- 台北夏季對配速預期有實質影響。
    heat_acclimation_pct    INTEGER,
    heat_trend              TEXT,
    -- 高海拔適應（Altitude Acclimation）百分比。平地訓練恆為 0，
    -- 僅出國移地訓練時有意義。
    altitude_acclimation_pct INTEGER,
    -- 體能趨勢（Fitness Trend）數值代碼
    fitness_trend           INTEGER,
    -- --- 月負荷組成（Training Load Balance）---
    -- 有氧低強度／有氧高強度／無氧三者的月累加負荷，判斷訓練是否偏食。
    monthly_load_aerobic_low  REAL,
    monthly_load_aerobic_high REAL,
    monthly_load_anaerobic    REAL,
    -- 三類月負荷各自的目標範圍（Garmin「負荷焦點」長條圖上的目標區間）
    monthly_load_aerobic_low_target_min  REAL,
    monthly_load_aerobic_low_target_max  REAL,
    monthly_load_aerobic_high_target_min REAL,
    monthly_load_aerobic_high_target_max REAL,
    monthly_load_anaerobic_target_min    REAL,
    monthly_load_anaerobic_target_max    REAL,
    -- 負荷平衡回饋（如 AEROBIC_HIGH_SHORTAGE＝缺乏高強度有氧）
    training_balance_feedback TEXT,
    source               TEXT NOT NULL CHECK (source IN ('fit_manual', 'garmin_export', 'garmin_mcp', 'strava')),
    source_version        TEXT,
    fetched_at            TEXT NOT NULL,
    UNIQUE (athlete_id, date, source)
);

-- 體能表現指標快照。與 training_status 不同，這些端點**不吃日期參數**，
-- 只回傳「當前值」，故每次同步寫入一筆以 date 為鍵的快照，
-- 累積起來才形成時間序列（而非逐日重建歷史）。
CREATE TABLE IF NOT EXISTS performance_metrics (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id          INTEGER NOT NULL REFERENCES athlete_profile(id),
    date                TEXT NOT NULL,
    -- --- 賽事完賽預測（Race Predictions，秒）---
    -- Garmin 依當前體能推估的各距離完賽時間，可作為課表目標配速的
    -- 獨立校準源（與本專案 VDOT 引擎的推算互為對照）。
    race_predict_5k_sec         INTEGER,
    race_predict_10k_sec        INTEGER,
    race_predict_half_sec       INTEGER,
    race_predict_marathon_sec   INTEGER,
    -- --- 乳酸閾值（Lactate Threshold）---
    -- 乳酸開始快速堆積的臨界點。閾值心率直接決定 tempo（節奏跑）課的
    -- 心率上限，是 Phase 2 規則引擎的直接輸入。
    lactate_threshold_hr_bpm        INTEGER,
    -- 閾值配速（秒/公里），由 API 的 speed（公尺/秒）換算
    lactate_threshold_pace_sec_per_km INTEGER,
    -- 功能性閾值功率（FTP, Functional Threshold Power，瓦特）
    functional_threshold_power      INTEGER,
    -- --- 耐力分數（Endurance Score）---
    -- Garmin 綜合長期訓練量與強度的耐力評分。
    endurance_score             INTEGER,
    -- 分級（0–6，對應 Novice～Elite 等七級）
    endurance_classification    INTEGER,
    -- 耐力分數的來源貢獻分佈（各運動類型佔比 %）。存 JSON：
    -- contributors 是變長陣列，且 activityTypeId 的集合會隨運動習慣改變。
    -- 格式：[{"activity_type_id": 13, "group": null, "contribution": 9.84}, ...]
    endurance_contributors_json TEXT,
    -- --- 體能年齡（Fitness Age）---
    fitness_age                 REAL,
    chronological_age           INTEGER,
    -- 可達成的最佳體能年齡（Garmin 依可改善項目推算）
    achievable_fitness_age      REAL,
    -- 爬坡分數（Hill Score）
    hill_score                  INTEGER,
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

-- 活動當下的環境條件，逐小時序列。來源為 Open-Meteo（見 PLAN.md §1
-- 2026-09-05 決策）。用途是**歸因**——讓「這場掉速」能分辨成因是體能退步、
-- 疲勞，還是高溫高濕，否則 AI Coach 的建議會建立在未校正環境因素的
-- 配速趨勢上。
--
-- 為何存逐小時序列而非單一快照（2026-09-05 決策）：
-- 一場跑步橫跨 1–2 小時，而長跑後半段（通常也正是掉速的那段）與起跑時
-- 可能差 3–4°C。只存起跑快照會讓最需要解釋的那一段沒有對應的環境資料。
-- 代價是每場多存 1–3 列；若日後證實太吃資源，可退回只存代表值
-- （is_representative=1 的那列），不需改表結構。
--
-- ⚠️ 與 activities.min/max_temperature_c 完全不同，兩者不可混用：
-- 那兩欄是**手錶的手腕體溫讀數**（實測四年從未低於 21°C，一月台北卻有
-- max 29°C），本表才是真實環境氣溫。
--
-- ⚠️ 已知偏誤（依 §7.3 三欄位契約，下游取用時必須帶著）：
-- 1. 數值為 ERA5 約 11km 網格的**內插值，非測站實測**，都市熱島與盆地
--    地形效應會被抹平。
-- 2. WBGT 為本專案自行計算的簡化式（非 Liljegren），公式版本記於
--    wbgt_formula 欄位，跨版本比較前須確認該欄一致。
CREATE TABLE IF NOT EXISTS activity_weather (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id         INTEGER NOT NULL REFERENCES activities(id),
    -- 這列對應的整點時刻（ISO 8601 當地時間，與 Open-Meteo 的 timezone
    -- 參數一致）。一場活動會有 1~3 列，視跨越幾個整點而定。
    hour_local          TEXT NOT NULL,
    -- 是否為本場的代表值（起跑那一小時）。讓「只要一個數字」的查詢
    -- （列表、趨勢圖）不必每次都自己挑，也是日後若要退回單快照時的保留列。
    -- 每場恰有一列為 1。
    is_representative   INTEGER NOT NULL DEFAULT 0 CHECK (is_representative IN (0, 1)),
    -- --- Open-Meteo 原始欄位（單位與 API 回傳一致，不做轉換）---
    temperature_c       REAL,   -- temperature_2m (°C)
    relative_humidity_pct REAL, -- relative_humidity_2m (%)
    dew_point_c         REAL,   -- dew_point_2m (°C)
    -- 短波輻射（GHI，W/m²）。**這是 WBGT 戶外式的必要輸入**，也是選用
    -- Open-Meteo 而非 OpenWeather 的決定性理由（後者標準回傳不含輻射）。
    shortwave_radiation_wm2 REAL,
    direct_radiation_wm2    REAL,
    wind_speed_kmh      REAL,   -- wind_speed_10m (km/h)
    -- --- 本專案計算的衍生值 ---
    -- 濕球黑球溫度（WBGT, Wet Bulb Globe Temperature，°C），熱壓力指標。
    -- ⚠️ 必為**戶外版** 0.7·Tw + 0.2·Tg + 0.1·Ta（Tg 由輻射與風速推得）。
    -- 嚴禁存入遮蔭版 0.7·Tw + 0.3·Ta——該式忽略輻射與風速，只適用室內或
    -- 良好遮蔭，對戶外跑步是系統性低估，且正好低估在大太陽天。
    wbgt_c              REAL,
    -- 計算 wbgt_c 所用的公式版本識別字（如 'simplified_tg_v1'）。
    -- 公式日後若調整，舊列不重算也能被正確解讀，避免跨版本混比。
    wbgt_formula        TEXT,
    -- 資料來源與取得時點。source 用 'open_meteo_archive'（ERA5，有 5 天延遲）
    -- 或 'open_meteo_forecast'（past_days，供近 5 天用）區分——兩者模型不同，
    -- 同一場活動日後若由 forecast 補成 archive，需知道現值是哪一種。
    source              TEXT NOT NULL CHECK (source IN ('open_meteo_archive', 'open_meteo_forecast')),
    fetched_at          TEXT NOT NULL,
    UNIQUE (activity_id, hour_local)
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

-- context_notes：學員的軟性輸入（出差、睡眠不足、工作壓力、心情、
-- 「腳有點緊」等規則算不出的情境）。承 PLAN.md §7.5（2026-08-25 grill 定案）。
--
-- ⚠️ 硬邊界：**軟性輸入永不進事實層，僅作判斷層（AI 教練）的補充輸入。**
-- 它無法驗證（學員自述睡不好，系統無從查證），若讓它參與規則計算，等於在
-- 事實層開一個沒有信賴度可標的洞，直接違反 §7.3 三欄位契約的硬規則。
-- 故本表刻意**不被** readiness.py／training_load.py／session_quality.py 讀取。
--
-- ⚠️ 刻意**不設**類型（type）與嚴重度（severity）等結構化欄位：現階段尚不
-- 知「嚴重度」該如何分級，硬訂 schema 是在假設上建構（與「外部課表本輪僅
-- 預留欄位」同一個拒絕理由）。自由文字讓 AI 讀得懂、日期範圍讓查詢層能與
-- activities／daily_wellness 對齊；累積數十筆後回看實際內容，再決定是否結構化。
--
-- 日期範圍：start_date 為必要，end_date 可為 NULL（表示單日，或影響仍在
-- 持續、結束日未知）。查詢時 COALESCE(end_date, start_date) 即為涵蓋區間末日。
CREATE TABLE IF NOT EXISTS context_notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id      INTEGER NOT NULL REFERENCES athlete_profile(id),
    start_date      TEXT NOT NULL,
    end_date        TEXT,
    note            TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    CHECK (end_date IS NULL OR end_date >= start_date)
);

-- coach_conversations / coach_messages：AI 教練面板（dashboard 右側）的聊天紀錄。
-- 承 DASHBOARD_TASKS.md D.0 決策 3（2026-09-08 Fu 拍板）。
--
-- ⚠️ 硬邊界：**這兩張表只存原始對話，永不進事實層、規則引擎不讀。**
-- 它不是 TODO.md N-8 的「跨對話結構化記憶」，只是其日後的原料——
-- 現在就把對話結構化（抽出「學員說他膝蓋痛」之類的欄位）等於在還不知道
-- 該記什麼的時候先訂 schema，與 context_notes 拒絕加類型／嚴重度欄位是同一個理由。
--
-- content_json 存 Anthropic content blocks 的 JSON 陣列**原文**（含 thinking
-- 區塊與其 signature、tool_use、tool_result）。刻意不拆成結構化欄位：
-- 存下來的序列**就是**下一回合要送回 API 的 messages，任何改寫都會讓重播失真
-- （signature 被動過的 thinking 區塊會被 API 拒絕）。
--
-- seq 由呼叫端在同一交易內指定（0 起算），UNIQUE (conversation_id, seq)
-- 確保同一對話不會出現重號。一回合（user 提問 → assistant tool_use →
-- user tool_result → assistant 回覆）的所有列必須同一交易寫入：**絕不落地
-- 沒有對應 tool_result 的 tool_use**，否則下一回合送 API 會 400。
CREATE TABLE IF NOT EXISTS coach_conversations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id      INTEGER NOT NULL REFERENCES athlete_profile(id),
    title           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coach_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES coach_conversations(id),
    seq             INTEGER NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content_json    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (conversation_id, seq)
);

-- analysis_reports：總覽「AI 分析」按鈕產出的分析報告（2026-10-14 Fu 決定保存）。
-- 由 Claude Code 無互動 session（services/coach_analysis.py）產生。
--
-- ⚠️ 與 coach_messages 同一條硬邊界：**只存原始報告，永不進事實層、規則引擎不讀。**
-- 它是 TODO.md N-8「跨對話記憶」日後的原料，不是記憶本身。
-- tools_json 記錄這次分析呼叫了哪些工具（名稱、參數、是否出錯），讓報告裡的
-- 每個結論都能回頭查它依據的是哪些查詢（D.1／§7.3 的可追溯原則）。
CREATE TABLE IF NOT EXISTS analysis_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id      INTEGER NOT NULL REFERENCES athlete_profile(id),
    created_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL CHECK (status IN ('running', 'done', 'error')),
    prompt          TEXT NOT NULL,
    report_text     TEXT,
    tools_json      TEXT,
    error           TEXT,
    num_turns       INTEGER,
    duration_ms     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_analysis_reports_athlete
    ON analysis_reports (athlete_id, created_at);

CREATE INDEX IF NOT EXISTS idx_activity_weather_activity
    ON activity_weather (activity_id, hour_local);
CREATE INDEX IF NOT EXISTS idx_activity_laps_activity ON activity_laps (activity_id);
CREATE INDEX IF NOT EXISTS idx_activity_records_activity ON activity_records (activity_id, elapsed_sec);
CREATE INDEX IF NOT EXISTS idx_activities_athlete_date ON activities (athlete_id, started_at);
CREATE INDEX IF NOT EXISTS idx_daily_wellness_athlete_date ON daily_wellness (athlete_id, date);
CREATE INDEX IF NOT EXISTS idx_training_status_athlete_date ON training_status (athlete_id, date);
CREATE INDEX IF NOT EXISTS idx_performance_metrics_athlete_date ON performance_metrics (athlete_id, date);
CREATE INDEX IF NOT EXISTS idx_training_plan_athlete_date ON training_plan (athlete_id, planned_date);
CREATE INDEX IF NOT EXISTS idx_athlete_source_identity_athlete ON athlete_source_identity (athlete_id);
CREATE INDEX IF NOT EXISTS idx_metric_coverage_athlete ON metric_coverage (athlete_id, metric_name);
CREATE INDEX IF NOT EXISTS idx_context_notes_athlete_date
    ON context_notes (athlete_id, start_date);
CREATE INDEX IF NOT EXISTS idx_coach_messages_conversation
    ON coach_messages (conversation_id, seq);
