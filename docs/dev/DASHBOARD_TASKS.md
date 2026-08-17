# Dashboard 實作任務分配

> 依據 `docs/dev/PLAN.md`、`docs/dev/TODO.md` 與已核准的 dashboard 計畫。
> **這份文件是三個 Task 的共同契約**——API 回傳格式與資料表結構已在此定死，照著做才能整合。
> 建立 2026-08-17，同日依 FIT 實測結果重新編排。

## 分工總覽

| Task | 負責範圍 | 依賴 |
|---|---|---|
| **Task C** | FIT 解析：每公里分圈 + 逐秒降頻 + 訓練類型標記 | 無，可立即開始 |
| **Task A** | 後端：查詢層 + FastAPI + 單元測試 | 無（照 contract 寫，C 的表先建好即可） |
| **Task B** | 前端：HTML/CSS/JS + RWD + 手繪 SVG 圖表 | 無（照 contract 用假資料開發） |
| **整合驗證** | 主 session 執行（跑 server、瀏覽器與手機實測） | 需 A、B、C 都完成 |

三個 Task 皆可平行——contract 已定死。Task A 需要 Task C 的資料表存在才能查詢，故 **schema 變更由 Task C 先做**（見下方 schema 章節），A 只需照欄位定義寫查詢。

---

## 執行環境（重要）

**專用 conda 環境 `rc`（Python 3.12）**。不可用裸 `python`（指向無關的 venv，沒有這些套件）。一律用絕對路徑：

```bash
RC="C:/Users/cFu/anaconda3/envs/rc/python.exe"
"$RC" -m unittest discover -s src/test/unit -p "test_*.py" -v
```

已裝套件見 `requirements.txt`：`fastapi`、`uvicorn`、`fitparse`。詳見 `AGENTS.md` 的 Python Environment 章節。

---

## 全專案共同規範（所有 Task 都必須遵守）

1. **絕不 hardcode 個人識別資訊**：不可出現特定 email、Garmin userProfileId、姓名。`athlete_id` 一律從參數/API 取得
2. **個人資料不進版控**：`output/`、`input/` 已被 gitignore。測試一律用合成假資料，**不可把真實資料寫進測試檔或前端**
3. **不可 hardcode 應可設定的值**（AGENTS.md）：DB 路徑、host、port 都要能從外部傳入
4. **不重複造輪子**（AGENTS.md）：能重用既有函式就重用，不複製貼上
5. **先讀再改**：修改既有檔案前先完整讀過
6. **驗證要實際執行**：邏輯性變更必須真的跑過，不能只靠閱讀程式碼判斷
7. 註解與文件用**繁體中文**，程式碼識別字用英文
8. 測試用 `unittest`（**pytest 未安裝**），風格比照既有的 `src/test/unit/`

---

## Task C：FIT 解析（每公里分圈 + 逐秒降頻 + 訓練類型）

### 為什麼需要（實測依據）

Garmin 匯出 JSON 裡的 `splits` 是**手動按錶的不規則分段**（實測：10km 只有 2~5 段、長度 3.42/2.97/3.76km、含 0km 空圈），且 266 場中僅 94 場有 >1 圈。**FIT 檔內則有整齊的每公里分圈**（實測 21km 那場：22 圈，每圈精準 1000.0m，含每圈平均心率）。使用者要的「單場訓練成效分析」必須靠 FIT。

### 實測已確認的事實（不要重新摸索）

| 項目 | 實測結果 |
|---|---|
| 解析套件 | `fitparse` 1.2.0（已裝）|
| 解析速度 | 21km 的檔約 **2 秒** |
| 逐秒資料 | `record` 訊息，21km 那場有 9037 筆，欄位：`timestamp`、`heart_rate`、`distance`(m)、`enhanced_speed`(m/s)、`cadence`(rpm)、`position_lat/long`(semicircles)、`altitude` |
| 每公里分圈 | `lap` 訊息，欄位：`total_distance`(m)、`total_elapsed_time`(s)、`avg_heart_rate`、`max_heart_rate` |
| 整場摘要 | `session` 訊息，欄位：`start_time`(**UTC**)、`total_distance`(m)、`sport`、`sub_sport` |
| FIT 檔位置 | `input/garmin_export/*/DI_CONNECT/DI-Connect-Uploaded-Files/UploadedFiles_0-_Part{1,2}.zip`，共 20,778 個 |
| 可直接從 zip 讀 | ✅ `zipfile.ZipFile(...).read(name)` → `fitparse.FitFile(io.BytesIO(data))`，**無需解壓到磁碟** |

### ⚠️ 檔案對應方式（這是最容易做錯的地方）

**FIT 檔名的 ID 與資料庫的 `activities.external_id` 是兩套不同的編號，不能直接對應：**
- FIT 檔名：`<email>_152627722646.fit` → 12 位數的 **upload ID**
- DB `external_id`：`23997807612` → 11 位數的 **activity ID**
- 實測用 external_id 比對：**266 場全部對不上（0 筆）**

**正確做法**：用 FIT `session.start_time`（UTC）轉當地時間後比對 `activities.started_at`，並用距離交叉驗證。

實測驗證通過的例子：
- FIT `2021-12-18 23:03:36` UTC + 8h → DB `2021-12-19T07:03:36`，距離 21163.94m ↔ 21.1639km ✅
- FIT `2024-02-29 09:15:28` UTC + 8h → DB `2024-02-29T17:15:28`，距離 5114.68m ↔ 5.1147km ✅

**時區偏移不可 hardcode 成 +8**（他人可用原則）。建議做法：以 UTC 時間為基準，在 DB 中搜尋「時間差在 ±14 小時內且距離差 < 1%」的活動，取距離最接近者。時區偏移由資料自然推導，不預設任何地區。

**必須處理的例外**：部分 FIT 沒有 `session` 訊息（實測遇到 `no session`），要跳過並記錄，不可整批中斷。

### 只解析需要的檔案

20,778 個檔中多數是每日監測，不是跑步。**只需解析能對應到 `activities` 中跑步活動的檔案**（266 場）。做法：先讀每個 FIT 的 `session` 判斷 `sport == 'running'`，再對應到 DB。預估耗時約 9 分鐘，屬正常，用進度輸出讓使用者知道還在跑。

### Schema 變更（Task C 負責，其他 Task 依此撰寫）

修改 `src/main/python/models/schema.sql`，新增兩張表並修改一張：

```sql
-- 每公里（或每圈）分段。資料量小，完整保存。
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

-- 逐秒資料降頻後保存（預設每 10 秒一筆），供配速/心率曲線與心率漂移分析。
-- 不存完整逐秒：240 萬筆對畫圖無實益，降頻後約 1/10 且足夠。
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

CREATE INDEX IF NOT EXISTS idx_activity_laps_activity ON activity_laps (activity_id);
CREATE INDEX IF NOT EXISTS idx_activity_records_activity ON activity_records (activity_id, elapsed_sec);
```

`activities` 表新增兩個欄位（使用者要求「實際訓練類型」與「計畫類型」兩者都要）：
```sql
workout_type        TEXT CHECK (workout_type IN ('easy','tempo','interval','lsd','race','recovery','unknown'))
workout_type_source TEXT CHECK (workout_type_source IN ('auto','manual'))
```
- `activities.workout_type` = **實際這次練了什麼**（匯入時用規則自動推測，可事後手動修正）
- 既有的 `training_plan.workout_type` = **原本計畫練什麼**，透過 `linked_activity_id` 連到實際活動
- 兩者比對即可看出課表遵從度

⚠️ schema.sql 目前用 `CREATE TABLE IF NOT EXISTS`，**對已存在的 `activities` 表加欄位不會生效**。需要處理既有資料庫的欄位新增（`ALTER TABLE ... ADD COLUMN`，並容許重複執行不報錯），不可要求使用者砍掉重建。

### 訓練類型自動判定規則（v1 從簡）

以每公里分圈的配速變異度為主要依據：
- 分圈配速標準差大、且有明顯快慢交替 → `interval`
- 距離長（≥ 個人近期平均的 1.5 倍）且配速平穩偏慢 → `lsd`
- 配速持續偏快且平穩 → `tempo`
- 其餘 → `easy`
- 無足夠分圈資料 → `unknown`

**這是啟發式猜測，不是精確分類**。`workout_type_source='auto'` 標記來源，讓使用者能手動覆寫。閾值要放在模組頂端的常數，方便調整，不可散落在邏輯中。

### 要建立/修改的檔案
| 檔案 | 說明 |
|---|---|
| `src/main/python/services/fit_parser.py` | 新增。純解析函式：讀 FIT → 回傳 laps/records/session dict，**不碰資料庫** |
| `src/main/python/services/fit_import_runner.py` | 新增。從 zip 逐一讀取、對應 activity、降頻、寫入 DB、CLI 入口 |
| `src/main/python/services/workout_classifier.py` | 新增。訓練類型判定（純函式，吃 laps 回傳類型）|
| `src/main/python/models/schema.sql` | 修改。加上述兩表與 activities 欄位 |
| `src/test/unit/test_fit_parser.py` | 新增。用合成 FIT 或 mock 測解析邏輯 |
| `src/test/unit/test_workout_classifier.py` | 新增。用合成分圈資料測分類規則 |

### CLI 介面
```bash
"$RC" -m src.main.python.services.fit_import_runner \
    --input-dir input/garmin_export --db-path output/running_coach.db [--sample-every-sec 10]
```

---

## API Contract（已定死，Task A 與 B 共同依據）

所有回應皆 `application/json`。`range` 合法值：`7d`/`30d`/`90d`/`1y`/`all`，預設 `30d`。
所有端點接受可選 `athlete_id`；未指定時用資料庫第一位。

### `GET /api/meta`
```json
{
  "athlete": { "id": 1, "name": "Fu" },
  "metric_coverage": {
    "hrv_ms": { "earliest_date": "2025-10-25", "latest_date": "2026-08-16" },
    "activities": { "earliest_date": "2019-12-14", "latest_date": "2026-08-16" }
  },
  "notice": "此服務僅供區網存取且無身分驗證，切勿對外網開放。"
}
```

### `GET /api/sessions?range=30d&athlete_id=1`
只回傳跑步類活動，依 `started_at` **由新到舊**排序。
```json
{
  "range": "30d", "start_date": "2026-07-18", "end_date": "2026-08-16",
  "sessions": [
    {
      "id": 512, "started_at": "2026-08-16T18:43:31", "date": "2026-08-16",
      "title": "傍晚跑步", "activity_type": "running",
      "workout_type": "easy", "workout_type_source": "auto",
      "distance_km": 6.02, "duration_sec": 2210, "avg_pace_sec_per_km": 367,
      "avg_hr_bpm": 152, "max_hr_bpm": 168, "avg_cadence_spm": 171, "aerobic_te": 3.4
    }
  ]
}
```
任何欄位可能為 `null`（舊資料未必齊全），前端須容錯。

### `GET /api/sessions/{id}?athlete_id=1`
```json
{
  "id": 512, "started_at": "2026-08-16T18:43:31", "title": "傍晚跑步",
  "activity_type": "running", "workout_type": "easy", "workout_type_source": "auto",
  "planned": { "workout_type": "lsd", "planned_distance_km": 12.0 },
  "summary": {
    "distance_km": 6.02, "duration_sec": 2210, "avg_pace_sec_per_km": 367,
    "avg_hr_bpm": 152, "max_hr_bpm": 168, "avg_cadence_spm": 171,
    "aerobic_te": 3.4, "elevation_gain_m": 12.0, "calories": 430
  },
  "hr_zones": {
    "available": true,
    "zones": [ { "zone": 0, "seconds": 92 }, { "zone": 1, "seconds": 53 } ]
  },
  "laps": {
    "available": true,
    "source": "fit",
    "laps": [ { "lap": 1, "distance_km": 1.0, "duration_sec": 430.3, "pace_sec_per_km": 430, "avg_hr_bpm": 150 } ]
  },
  "records": {
    "available": true,
    "sample_every_sec": 10,
    "points": [ { "elapsed_sec": 0, "distance_km": 0.0, "hr_bpm": 96, "pace_sec_per_km": null, "cadence_spm": 0 } ]
  },
  "hr_drift": { "available": true, "first_half_avg_hr": 148, "second_half_avg_hr": 157, "drift_pct": 6.1 }
}
```

**資料不存在時（不可省略，前端必須顯示說明而非空圖）：**
```json
"hr_zones": { "available": false, "reason": "此活動無心率區間資料" }
"laps":     { "available": false, "reason": "此活動無分圈資料" }
"records":  { "available": false, "reason": "此活動尚未解析 FIT 逐秒資料" }
"hr_drift": { "available": false, "reason": "需要逐秒資料才能計算心率漂移" }
"planned":  null
```

`hr_drift` 定義：前半段與後半段（依 `elapsed_sec` 中位切分）的平均心率，`drift_pct = (後半 - 前半) / 前半 * 100`。**只呈現數值，不做文字解讀**（解讀留給之後的 AI coach）。

### `GET /api/wellness-trend?range=30d&athlete_id=1`
```json
{
  "range": "30d", "start_date": "2026-07-18", "end_date": "2026-08-16",
  "metrics": {
    "hrv_ms": {
      "available": true, "clipped": false,
      "coverage": { "earliest_date": "2025-10-25", "latest_date": "2026-08-16" },
      "points": [ { "date": "2026-08-16", "value": 47.0 } ]
    },
    "resting_hr_bpm": { "...": "同上結構" },
    "spo2_pct": { "...": "同上" },
    "sleep_score": { "...": "同上" },
    "training_readiness_score": { "...": "同上" },
    "stress_avg": { "...": "同上" }
  }
}
```
- **`clipped: true`** = 所選 range 早於該指標 `metric_coverage.earliest_date`。前端須顯示「此日期前無資料」，**不可畫成 0 或誤導的平線**
- `points` 只含**有值**的日期，缺值直接不出現（前端斷線，不補 0）
- **不可包含 `body_battery_max`／`body_battery_min`**（實測 100% NULL）

### `GET /api/training-days?range=30d&athlete_id=1`
供前端把訓練日標記疊到 wellness 圖上（＝「訓練×身體關聯」）。
```json
{ "range": "30d", "training_days": ["2026-08-12", "2026-08-14", "2026-08-16"] }
```

---

## Task A：後端查詢層 + FastAPI

### 要建立/修改的檔案
| 檔案 | 說明 |
|---|---|
| `src/main/python/services/dashboard_queries.py` | 新增。純查詢函式，**不可 import fastapi** |
| `src/main/python/api/app.py` | 新增。FastAPI app、掛載靜態檔、CLI 入口 |
| `src/main/python/api/routes_dashboard.py` | 新增。路由層，只做參數解析與呼叫查詢層 |
| `src/main/python/api/__init__.py` | 新增空檔 |
| `src/main/python/services/garmin_export_parser.py` | 修改。見下方「重用既有程式碼」 |
| `src/test/unit/test_dashboard_queries.py` | 新增 |
| `src/test/unit/fixtures.py` | 擴充。加入建立測試用 SQLite 資料的 helper |

### 重用既有程式碼（不可重寫）
`garmin_export_parser.py` 已有經真實資料驗證的單位換算函式，目前為私有：
`_cm_to_km`、`_cm_to_m`、`_ms_to_sec`、`_kj_to_kcal`、`_speed_cm_per_ms_to_pace_sec_per_km`

**改為公開**（去底線）並更新該檔內部呼叫處與既有測試，供其他模組 import。**嚴禁重寫一份換算邏輯。**
另可重用 `src/main/python/models/db.py` 的 `get_connection(db_path)`。

### 資料來源優先序（分圈）
單場的 `laps` 優先取 Task C 寫入的 `activity_laps`（FIT 每公里分圈，`source: "fit"`）。若該活動尚未解析 FIT，退回 `raw_data_json` 的手動分圈（`source: "garmin_manual_lap"`，只取 `type == 17`，濾掉 `type==3` 整場總計與 `type==18/22` 雜訊；濾完 ≤1 圈則 `available: false`）。

### 其他要求
- **`athlete_id` 從單一入口取得**（如 `resolve_default_athlete_id(conn)`），不散落各處——上雲後改成從登入 session 取時只需改一處
- **SQL 只用標準語法**，不用 SQLite 專有寫法
- `app.py` CLI：`--db-path`（必填）、`--host`（預設 `127.0.0.1`）、`--port`（預設 `8000`）
- `app.py` docstring 要寫明：**綁 0.0.0.0 時同區網任何裝置都能存取這些個人健康資料且無身分驗證，絕不可對外網開放**——刻意取捨，非疏漏
- 靜態檔掛載到 `/`，指向 `src/main/resources/assets/dashboard/`

### 測試涵蓋
range 篩選邊界、`clipped` 標記正確性、laps 來源優先序與退回、`hr_drift` 計算、缺值不補 0、`available:false` 各情境。既有 15 項測試必須仍全過。

---

## Task B：前端 Dashboard

### 要建立的檔案
`src/main/resources/assets/dashboard/` 下：`index.html`、`styles.css`、`app.js`（狀態與資料抓取）、`charts.js`（純畫圖函式）

### 技術限制（硬性）
- **純 HTML/CSS/JS，零建置步驟**（不用 npm/webpack/TypeScript）
- **不可引用任何 CDN**——離線與區網要能運作，且延續「個人資料不外流」原則
- **不裝任何 JS 圖表庫**，手繪 SVG
- **圖表只呈現數據與趨勢線，不做文字解讀**（使用者明確要求，解讀留給之後的 AI coach）

### 版面
**本次以電腦版為主，但手機版架構要先考慮進去，避免之後大改。**

- 桌機（≥768px）：左右兩欄。左＝單場分析（上）＋跨場趨勢（下）；右＝每日身體狀況
- 手機（<768px）：單欄堆疊，順序 單場分析 → 跨場趨勢 → 每日身體狀況（依使用者優先序，不做 tab）

三個區塊：
1. **單場分析**（預設最近一場，可切換）：摘要數字卡、**每公里分圈配速長條圖**、**配速/心率逐秒曲線圖**、**心率漂移數值**、心率區間分布長條圖、訓練類型標記（含 auto/manual 來源與計畫類型對照）
2. **跨場次趨勢**：距離／配速／平均心率／TE 折線圖、週跑量長條圖
3. **每日身體狀況**：六指標折線圖，**訓練日在 X 軸加標記**

### RWD 硬性要求（使用者明確要求「不跑版」）
- `<meta name="viewport" content="width=device-width, initial-scale=1">`
- SVG 用 `viewBox` + `preserveAspectRatio`，**不寫死像素寬**
- X 軸刻度**依容器寬度動態決定顯示幾個**（30 天標籤在 390px 手機上會擠成一團）
- 訓練列表手機版**改卡片式堆疊**，非多欄表格橫向捲動
- 觸控目標 ≥ 44px
- flex/grid + `max-width: 100%`，斷點 768px
- 個別圖表可自己 `overflow-x: auto`，**但整頁 body 絕不可橫向捲動**

### 架構要求（為將來的滑動互動預留）
`app.js`（狀態）與 `charts.js`（畫圖）**必須解耦**：`charts.js` 只放純函式（吃資料 → 繪 SVG，不碰 fetch/全域狀態）；`app.js` 管 range 狀態、fetch、呼叫重繪。未來加滑動手勢只需接 `app.js` 既有的「設定 range → 重抓 → 重繪」路徑。

### 必須處理的狀態
`available:false` → 顯示 `reason`，不畫空圖；`clipped:true` → 顯示「此日期前無資料」；欄位 `null` → 跳過該點不當 0；**`body_battery` 完全不可出現**；載入中／API 失敗要有明確提示。

Task A 完成前，依上方 contract 自建**虛構**假資料開發。

---

## 整合驗證（主 session 執行）

```bash
RC="C:/Users/cFu/anaconda3/envs/rc/python.exe"
```
1. `"$RC" -m unittest discover -s src/test/unit -p "test_*.py" -v` — 既有 15 項 + 新增全過
2. FIT 匯入：`"$RC" -m src.main.python.services.fit_import_runner --input-dir input/garmin_export --db-path output/running_coach.db`，確認 266 場跑步的分圈與逐秒資料入庫，抽驗 21km 那場應有 22 圈、每圈約 1000m
3. 啟動 API：`"$RC" -m src.main.python.api.app --db-path output/running_coach.db --host 0.0.0.0 --port 8000`
4. curl 各端點，與已知真實數字交叉比對（2025-11-23 那場 10K：配速 5:12/km、avgHR 148、maxHR 159、10.1089km；HRV coverage 起日 2025-10-25）
5. 桌機瀏覽器：range 切換、場次切換、`all` 時 HRV 的 clipped 提示、逐秒曲線與心率漂移顯示
6. 手機實機：**連得到** + **不跑版**（無橫向捲動、字級可讀、標籤不重疊、列表為卡片、按鈕好點）
7. `grep -ri body_battery src/main/resources/assets/` 應無結果
8. `git status` 確認無個人資料落入版控
