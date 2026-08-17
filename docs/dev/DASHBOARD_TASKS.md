# Dashboard 實作任務分配

> 依據 `docs/dev/PLAN.md`、`docs/dev/TODO.md` 與已核准的 dashboard 計畫。
> **這份文件是 Task A、B 的共同契約**——API 回傳格式與資料表結構已在此定死，照著做才能整合。
> 建立 2026-08-17。同日 Task C 完成後，依真實實測結果與後續資料庫變更（UDSFile 補值、運動類型可設定化）全面更新過一次——現在的內容已是最終版，Task A/B 可直接照做，不需再回頭確認。

## 分工總覽

| Task | 負責範圍 | 狀態 |
|---|---|---|
| **Task C** | FIT 解析：每公里分圈 + 逐秒降頻 + 訓練類型標記 | ✅ **已完成**（詳見下方「Task C 完成紀錄」，schema 已在真實資料庫上驗證）|
| **Task A** | 後端：查詢層 + FastAPI + 單元測試 | 🔵 未開始，可立即開始（schema 已就緒）|
| **Task B** | 前端：HTML/CSS/JS + RWD + 手繪 SVG 圖表 | 🔵 未開始，可立即開始（照 contract 用假資料開發，不需等 A）|
| **整合驗證** | 主 session 執行（跑 server、瀏覽器與手機實機測試）| 需 A、B 都完成 |

Task A、B 現在可以平行進行——contract（含 schema）已經是實測過的最終版，不會再變動。

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

## Task C 完成紀錄（僅供 Task A/B 理解資料由來，不需要再做任何事）

FIT 解析（每公里分圈＋逐秒降頻＋訓練類型自動分類）已完成並在真實資料庫驗證過。程式碼：
[`fit_parser.py`](../../src/main/python/services/fit_parser.py)（純解析）、
[`workout_classifier.py`](../../src/main/python/services/workout_classifier.py)（分類）、
[`fit_import_runner.py`](../../src/main/python/services/fit_import_runner.py)（orchestration + CLI）。

**最終實測結果**（2026-08-17，非預估）：掃描 20,778 個 FIT，成功匯入 **243/266 場跑步（91%）**，分圈 2,381 筆、逐秒降頻 68,884 筆，對應失敗 1 場、錯誤 0，耗時約 12 分鐘。訓練類型分布：easy 87、tempo 50、unknown 45（~18%，分圈數不足 3 圈無法判斷）、lsd 39、interval 22。

**⚠️ Task A 寫查詢時必須知道的兩個關鍵事實**：

1. **FIT 檔名的 ID 與 `activities.external_id` 是兩套不同編號，完全對不上**（12 位數 upload ID vs 11 位數 activity ID，實測 0 筆吻合）。已用 UTC 時間＋距離交叉比對解決，這是 `fit_import_runner.match_activity()` 內部的事，**Task A 查詢時不需要處理此邏輯**，直接讀 `activity_laps`／`activity_records` 即可——只是要知道為什麼有 9% 的活動（23 場）沒有 FIT 資料：`unmatched`（1 場）或該次活動根本沒錄到 FIT（其餘）。

2. **運動類型不寫死**：`fit_import_runner.py` 的 `import_sports`／`classifiable_sports` 兩參數（CLI `--sports`／`--classifiable-sports`）控制要匯入與分類哪些運動，**目前預設兩者都只有 `running`**。這代表：
   - `activity_laps`／`activity_records` 目前只有 running 活動有資料，其他 activity_type（strength_training/walking/cycling 等）沒有，Task A 的 `/api/sessions/{id}` 對這些活動應回傳 `laps.available: false`／`records.available: false`，不是 bug
   - `workout_type` 目前只有 running 活動會被分類，其他活動的 `workout_type`／`workout_type_source` 恆為 `NULL`——`/api/sessions` 的 `workout_type` 欄位本來就允許 `null`，前端已設計為容錯，維持現狀即可

### Schema 最終狀態（已在真實資料庫驗證，Task A 依此撰寫查詢，不需再改 schema.sql）

除原有 `athlete_profile`／`activities`／`daily_wellness`／`training_plan` 外，累計新增：

```sql
-- 來源帳號識別 → 內部 athlete_id（見 PLAN.md 決策記錄）
athlete_source_identity(id, athlete_id, source, external_ref, created_at)

-- 各指標實際可用起訖日（見 PLAN.md 決策記錄）
metric_coverage(id, athlete_id, metric_name, source, earliest_date, latest_date, updated_at)

-- 每公里分圈，來源為 FIT 的 lap 訊息
activity_laps(id, activity_id, lap_index, distance_km, duration_sec, pace_sec_per_km, avg_hr_bpm, max_hr_bpm)

-- 逐秒資料降頻後保存（預設每 10 秒一筆）
activity_records(id, activity_id, elapsed_sec, distance_km, hr_bpm, pace_sec_per_km, cadence_spm, altitude_m)
```

`activities` 表累計新增欄位：
```sql
workout_type            TEXT  -- 'easy'/'tempo'/'interval'/'lsd'/'race'/'recovery'/'unknown'/NULL
workout_type_source     TEXT  -- 'auto'/'manual'/NULL
```

`daily_wellness` 表累計新增欄位（**原 contract 遺漏，Task A 寫 `/api/wellness-trend` 時必須含入**）：
```sql
all_day_stress_avg      INTEGER  -- 全天平均壓力（UDSFile），與既有 stress_avg（僅睡眠期間）分開存
steps                    INTEGER  -- 全天步數，來源 UDSFile，涵蓋近 6 年
```
（`hrv_weekly_avg_ms`、`spo2_pct`、`skin_temp_c`、`respiration_rate`、`sleep_score`、`training_readiness_score`、`recovery_time_hours`、`acwr` 等欄位原 contract 已包含，未變動）

`ALTER TABLE` 遷移機制已在 [`db.py`](../../src/main/python/models/db.py) 實作並驗證（可重複執行不報錯），Task A 若需再加欄位，比照 `_COLUMN_MIGRATIONS` 的模式加一行即可，不需自己另外設計遷移機制。

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
    "stress_avg": { "...": "同上（睡眠期間平均壓力）" },
    "all_day_stress_avg": { "...": "同上結構（全天平均壓力，2026-08-17 新增，與 stress_avg 分開顯示）" },
    "steps": { "...": "同上結構（2026-08-17 新增，coverage 約近 6 年）" }
  }
}
```
- **`clipped: true`** = 所選 range 早於該指標 `metric_coverage.earliest_date`。前端須顯示「此日期前無資料」，**不可畫成 0 或誤導的平線**
- `points` 只含**有值**的日期，缺值直接不出現（前端斷線，不補 0）
- **不可包含 `body_battery_max`／`body_battery_min`**（實測 100% NULL）
- `stress_avg` 與 `all_day_stress_avg` 是兩個不同範疇的指標（睡眠期間 vs 全天），**不可合併成一條線**，前端應分開標示或並排顯示

### `GET /api/training-days?range=30d&athlete_id=1`
供前端把訓練日標記疊到 wellness 圖上（＝「訓練×身體關聯」的基礎版本）。
```json
{ "range": "30d", "training_days": ["2026-08-12", "2026-08-14", "2026-08-16"] }
```

### `GET /api/recovery-impact?range=30d&athlete_id=1`

**訓練後恢復關聯**——回答「練完隔天身體狀況怎麼變化」，這是使用者確認的設計決策：**不新增資料表**，查詢時用 `activities` JOIN `daily_wellness`（隔天日期）即時計算，理由見 [PLAN.md §1 決策記錄](PLAN.md#1-決策記錄decisions-log)。

```json
{
  "range": "30d",
  "impacts": [
    {
      "activity_id": 512,
      "training_date": "2026-08-16",
      "workout_type": "easy",
      "distance_km": 10.16,
      "next_day": {
        "date": "2026-08-17",
        "available": true,
        "hrv_delta": -2.0,
        "hrv_delta_pct": -4.1,
        "resting_hr_delta": 1,
        "training_readiness_delta": -8
      }
    }
  ]
}
```
- `next_day.available: false`（附 `reason`）：隔天沒有 `daily_wellness` 資料時，不可用 0 或省略欄位表示
- delta 一律是「隔天數值 − 訓練當天數值」，**只回傳數字，不做「這樣好不好」的判斷**（呼應使用者「只呈現數據，不做解讀」的要求）
- 定義只看**隔天**（不做兩天/週均），這是 v1 的簡化選擇；之後若要改定義，只需改這支查詢函式，不涉及資料遷移

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
單場的 `laps` 優先取 `activity_laps`（FIT 每公里分圈，`source: "fit"`，91% 跑步活動已有）。該活動沒有 FIT 分圈時（`activity_laps` 查無資料，含 9% 未對應成功的跑步、以及 running 以外的所有運動類型），退回 `raw_data_json` 的手動分圈（`source: "garmin_manual_lap"`，只取 `type == 17`，濾掉 `type==3` 整場總計與 `type==18/22` 雜訊；濾完 ≤1 圈則 `available: false`）。兩種來源都沒有時才是真正的 `available: false`。

### 恢復關聯查詢
`/api/recovery-impact` 的實作見上方 API contract 章節，查詢邏輯：對每筆 `activities`（依 range 篩選），取 `date(started_at)+1 day` 對應的 `daily_wellness` 列，計算 HRV／RHR／Training Readiness 的差值。SQL 用標準 JOIN 或子查詢即可，不需要窗口函數等 SQLite 特有語法（呼應下方「不用 SQLite 專有寫法」的要求）。

### 其他要求
- **`athlete_id` 從單一入口取得**（如 `resolve_default_athlete_id(conn)`），不散落各處——上雲後改成從登入 session 取時只需改一處
- **SQL 只用標準語法**，不用 SQLite 專有寫法
- `app.py` CLI：`--db-path`（必填）、`--host`（預設 `127.0.0.1`）、`--port`（預設 `8000`）
- `app.py` docstring 要寫明：**綁 0.0.0.0 時同區網任何裝置都能存取這些個人健康資料且無身分驗證，絕不可對外網開放**——刻意取捨，非疏漏
- 靜態檔掛載到 `/`，指向 `src/main/resources/assets/dashboard/`

### 測試涵蓋
range 篩選邊界、`clipped` 標記正確性、laps 來源優先序與退回、`hr_drift` 計算、`recovery-impact` 的 delta 計算與 `next_day.available:false` 情境、缺值不補 0、`available:false` 各情境。既有 44 項測試必須仍全過。

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
1. **單場分析**（預設最近一場，可切換）：摘要數字卡、**每公里分圈配速長條圖**、**配速/心率逐秒曲線圖**、**心率漂移數值**、心率區間分布長條圖、訓練類型標記（含 auto/manual 來源與計畫類型對照）、**該場的隔天恢復影響**（呼叫 `/api/recovery-impact` 找出這場對應的 `next_day` 物件，顯示 HRV/RHR/Readiness delta 數字，`available:false` 時顯示原因，不畫圖只顯示數字卡即可）
2. **跨場次趨勢**：距離／配速／平均心率／TE 折線圖、週跑量長條圖
3. **每日身體狀況**：八指標折線圖（含新增的 `steps`、`all_day_stress_avg`，且 `stress_avg` 與 `all_day_stress_avg` 分開顯示），**訓練日在 X 軸加標記**（`/api/training-days`）

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
`available:false`（含 `hr_zones`／`laps`／`records`／`hr_drift`／`recovery-impact` 的 `next_day`）→ 顯示 `reason`，不畫空圖；`clipped:true` → 顯示「此日期前無資料」；欄位 `null` → 跳過該點不當 0；**`body_battery` 完全不可出現**；載入中／API 失敗要有明確提示。

Task A 完成前，依上方 contract 自建**虛構**假資料開發。

---

## 整合驗證（主 session 執行，Task A/B 完成後）

```bash
RC="C:/Users/cFu/anaconda3/envs/rc/python.exe"
```
1. `"$RC" -m unittest discover -s src/test/unit -p "test_*.py" -v` — 既有 44 項（Task C 完成時的數字，Task A/B 會再增加）全過
2. 資料已在 `output/running_coach.db`（520 活動、1651 daily_wellness、243 場有分圈/逐秒），**不需重新匯入**，除非要驗證匯入本身的冪等性
3. 啟動 API：`"$RC" -m src.main.python.api.app --db-path output/running_coach.db --host 0.0.0.0 --port 8000`
4. curl 各端點，與已知真實數字交叉比對：
   - 2025-11-23 那場 10K：配速 5:12/km、avgHR 148、maxHR 159、10.1089km
   - HRV coverage 起日 2025-10-25，`range=all` 時應回 `clipped:true`
   - 21km 那場（2021-12-19）應有 22 圈分圈資料，每圈約 1.0km
   - `/api/recovery-impact` 抽一筆手動驗算 HRV delta 是否等於隔天減當天的值
5. 桌機瀏覽器：range 切換、場次切換、`all` 時 HRV 的 clipped 提示、逐秒曲線與心率漂移顯示、恢復關聯區塊
6. 手機實機：**連得到** + **不跑版**（無橫向捲動、字級可讀、標籤不重疊、列表為卡片、按鈕好點）
7. `grep -ri body_battery src/main/resources/assets/` 應無結果
8. `git status` 確認無個人資料落入版控
9. 確認非 running 活動類型（如 strength_training）的 `/api/sessions/{id}` 正確回傳 `laps.available:false`／`records.available:false`，且不因此拋錯
