# Dashboard 實作任務分配

> 依據 `docs/dev/PLAN.md`、`docs/dev/TODO.md` 與已核准的 dashboard 計畫。
> **這份文件是 Task A、B 的共同契約**——API 回傳格式與資料表結構已在此定死，照著做才能整合。
> 建立 2026-08-17。同日 Task C 完成後，依真實實測結果與後續資料庫變更（UDSFile 補值、運動類型可設定化）全面更新過一次——現在的內容已是最終版，Task A/B 可直接照做，不需再回頭確認。
> **2026-09-08 新增「Task D：App 殼與 AI 教練面板」章節**（取代「版面」一節，為 S1～S4 實作 session 的共同契約）；Task A/B 的 API contract 與各項硬性條款仍然有效。

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
      "avg_hr_bpm": 152, "max_hr_bpm": 168, "avg_cadence_spm": 171, "aerobic_te": 3.4,
      "lap_strip": [
        { "pace_sec_per_km": 372, "avg_hr_bpm": 146, "zone": 2 },
        { "pace_sec_per_km": 365, "avg_hr_bpm": 153, "zone": 2 },
        { "pace_sec_per_km": 361, "avg_hr_bpm": 160, "zone": 3 }
      ]
    }
  ]
}
```
任何欄位可能為 `null`（舊資料未必齊全），前端須容錯。

**`lap_strip`（N-29 B1，訓練紀錄清單的分圈小圖用）**：
- 來源只有 FIT 分圈（`activity_laps`），依 `lap_index` 排序；不退回手動按錶分段（單場詳細的 `laps.source = garmin_manual_lap` 那種）。
- 沒有 FIT 分圈 → **空陣列 `[]`**（不是 `null`），前端直接不畫。
- `zone`：該圈平均心率依 `_hr_zone_bounds()` 的手錶 %HRR 邊界換算，與首頁 `hr_zone_bounds` 顯示的 bpm 同一份。下界含、上界不含（相鄰兩區共用的邊界值歸較高區）；低於 Z1 下界為 `0`（與 `hr_zones.below_zone_1` 同義）；高於 HRmax 仍為 `5`。
- `zone: null`：學員未設定手錶區間或心率參數（**不套任何預設區間**），或該圈沒有心率（`avg_hr_bpm: null`）。`pace_sec_per_km` 也可能為 `null`。
- ⚠️ 已知偏誤：用**整圈平均心率**判區間，間歇課若一圈內含快跑與恢復會被平均掉，結果與單場詳細的逐秒 `hr_zones` 分布不完全相同。純呈現用，不進規則引擎。
- 只有 dashboard 的 `/api/sessions` 有這個欄位；MCP 的 `list_running_sessions` 共用同一查詢但**不含** `lap_strip`（避免灌進 AI 教練的 context）。

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

### `GET /api/overview?range=90d&today=YYYY-MM-DD&athlete_id=1`（2026-10-14 新增）

總覽頁（`#/overview`）用。ATL/CTL/TSB **不在這裡**，前端沿用 `/api/training-load`；
「本週課表」區塊用 `/api/calendar`（本 ISO 週）；「最後更新時間」在 `/api/today` 的 `freshness`（側邊列底部常駐）。

| 欄位 | 內容 | 錨點 |
|---|---|---|
| `sessions` | `total`／`total_km`／`total_sec`／`effective`＋`criteria`（≥10 分鐘、≥1 km、有心率） | range |
| `this_week` | `done_km`、`planned_km`（無課表為 null）、`planned_without_distance`、`zone_seconds`（Z1~Z5 秒數）、`sessions`／`zone_sessions_counted` | 今天（ISO 週） |
| `recent_volume` | 近 7 天 `km` 與前 7 天 `previous_km`（`days`／起訖日） | 今天 |
| `weekly_intensity` | `tiers`（low=Z0–2／mid=Z3／high=Z4–5）、`weeks[]`（各強度 km、`partial`） | range |
| `hr_zones` | Z1~Z5 秒數與佔比（分母不含 Z0）、`sessions_counted` | range |
| `hr_zone_bounds` | 五區名稱（中英）與 bpm 範圍；來自 `athlete_profile.hr_zone_hrr_bounds_json`，未設定時 `available:false` 且不套預設 | — |
| `vdot` | `race`（fact＋`basis`，只用比賽成績）、`garmin_reference`（`for_display_only`）、`history[]`（歷年比賽 VDOT） | 今天 |
| `personal_bests` | 固定 3K／5K／10K／半馬／全馬五格，每格全部比賽由快到慢；有官方成績優先，配速＝時間÷標準距離 | 生涯 |

- **有效訓練**＝資料品質篩選，**不是**「練得好不好」
- **強度分層**用手錶的 Garmin %HRR 五區，**不可**套 Daniels 的區名；沒有心率區間的場次整場算 `unknown_km`
- **VDOT** 為 Daniels 原版公式（實際距離直接代入）；不走心率換算（次最大推估，系統性偏高）
- **AI 分析**不在此 API，另見下方 `/api/analysis`

### `/api/analysis*`（2026-10-15 新增：總覽「AI 分析」按鈕）

按鈕 → `POST /api/analysis` → 後端在背景啟動 **Claude Code 無互動 session**（`claude -p`，`services/coach_analysis.py`），
用本機已登入的 Claude Code，**不需要 `ANTHROPIC_API_KEY`**；以 SSE 串流 `start → tool_use／tool_result／text／denied … → done｜error`，
報告存進 `analysis_reports`（只存原文，不進事實層、規則引擎不讀）。`GET /api/analysis`（清單）、`GET /api/analysis/{id}`（單筆）、`GET /api/analysis/status`。

- **安全邊界**：`--tools ""`（關掉全部內建工具）、`--allowedTools` 只有 `READ_ONLY_TOOL_NAMES`、`--disallowedTools` 列出寫入型工具、`--permission-mode dontAsk`、`--strict-mcp-config`。2026-10-14 實測要求它寫入 `add_context_note`，被拒且資料庫未變動
- **分析問題原文在後端**（`routes_analysis.ANALYSIS_PROMPT`），前端不帶 prompt；系統提示＝`coach()` 行為約束層原文＋分析介面補充
- 只接受本機（放行私有網段同樣看 `RC_CHAT_ALLOW_PRIVATE_NETWORK`）；同時只跑一個（第二個回 409）；server 重啟時把未收尾的 `running` 標成 `error`
- 結果顯示在 AI 面板的「分析報告」畫面（D.1：解讀只出現在面板，2026-10-14 Fu 確認），可下拉回看過去的報告；分析無法在面板追問，追問請用 Claude Code
- 設定（皆可不設）：`RC_ANALYSIS_CLAUDE_BIN`、`RC_ANALYSIS_MCP_CONFIG`、`RC_ANALYSIS_TIMEOUT_SEC`（預設 300）、`RC_ANALYSIS_MODEL`

### `PUT /api/plan/{plan_id}/link?athlete_id=1`（2026-10-24 新增：行事曆「這場是課表」，N-29 B2）

學員確認課表配對，只寫 `training_plan.linked_activity_id` 這一欄（決策見 PLAN §1）。body：`{"activity_id": 123}`，`null`＝取消連結；**必須明確帶 `activity_id`**，漏帶回 422（不當成取消）。

- 回傳：`{"plan_id", "linked_activity_id", "plan_match"}`。`plan_match` 是該活動寫入後在 `/api/calendar` 會得到的 `{state, method, reason}`，連結成功時為 `{"state":"exact","method":"linked_activity_id","reason":null}`；取消時為 `null`。寫入後同一天競爭的另一場不再是 `ambiguous`（課表已確認給別場，變成 `none`）。
- 404：課表或活動不存在，或不屬於這位學員。
- 422：課表已被取代（`is_active=0`）、活動日期 ≠ `planned_date`、活動是已併入主課的暖身（請連主課）、運動種類不相容（`plan_reconciliation.is_compatible`）、該活動已連到另一份生效課表。`detail` 是可直接顯示的中文說明。
- 寫入後重算當天生效課種（`resolve_for_dates`），回應附 `reclassified`：有變動的場次 `[{activity_id, date, old, new, basis, detected}]`。
- 來源限制比照 `/api/daily-feel`：區網放行，不另設開關（2026-10-24 Fu 決定，手機上要能按「這場是課表」）。
- 已知缺口：課表被 `superseded_by` 取代時，新的一列不帶入連結（N-7 處理）。寫入後**不**重算活動的生效課種（N-28 `workout_type`，只看外部課表）；已匯入的活動要等該日外部課表重寫或跑 `resolve_for_dates` 回填才會更新。

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

> **⚠️ 本節是 2026-08 的原始實作紀錄，已被下方「Task D」（2026-09-08 定案）取代**：版面改為側邊列分頁 App 殼＋右側 AI 教練面板，
> 手機改用底部分頁列（原「不做 tab」是三區塊時代的堆疊策略，Fu 於 2026-09-08 改採分頁列）。
> 以下保留作為三個原始區塊內容的定義；文末「呈現層設計原則」一節亦已決策。

**本次以電腦版為主，但手機版架構要先考慮進去，避免之後大改。**

- ~~桌機（≥768px）：左右兩欄。左＝單場分析（上）＋跨場趨勢（下）；右＝每日身體狀況~~ → 見 Task D.2
- ~~手機（<768px）：單欄堆疊，順序 單場分析 → 跨場趨勢 → 每日身體狀況（依使用者優先序，不做 tab）~~ → 見 Task D.2

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


---

## Task D：App 殼與 AI 教練面板（2026-09-08 定案規格）

> **本節取代上方「版面」一節**（2026-08 的三區塊單頁、以及 2026-09-06 N-11 的五區塊＋錨點導覽列）。
> Fu 實際使用 N-11 版本後的回饋：錨點只是捲到同一頁下面，仍然混亂；要的是左側邊列、真正的分頁、
> 以及右側常駐的 AI 教練對話區（一邊看數據一邊問）。決策背景與分階段進度見 [TODO.md](TODO.md) N-17，
> 決策記錄見 [PLAN.md §1](PLAN.md#1-決策記錄decisions-log) 2026-09-08 該列。
> **本節是 S1～S4 四個實作 session（可在雲端）的共同契約**；上方 Task A/B 的 API contract 與 RWD／架構／狀態處理條款全部仍然有效。

### D.0 已定案的決策（2026-09-08，Fu 拍板）

| # | 決策 | 含義 |
|---|---|---|
| 1 | AI 教練面板是**真正的聊天**：後端呼叫 Anthropic API | 推翻 PLAN.md §1 2026-09-01「不自己呼叫 Anthropic API」的部分；「不自建 App」的精神仍在——行為約束層 `coach()` 與 MCP 工具定義**一字不改、直接重用**，Claude Code／Desktop 的 MCP 路徑照舊保留 |
| 2 | 手機用**底部分頁列** | 取代「手機單欄堆疊不做 tab」（那是三區塊時代的堆疊策略） |
| 3 | 對話**存 SQLite** | 只存原始對話，不進事實層、規則引擎不讀；不是 N-8 的結構化記憶，只是其日後的原料 |
| 4 | 聊天端點防護：**預設只接受本機**，環境變數可放行私有網段 | 瀏覽器端零密鑰。未來要在外面用：把驗證放在 app 前面（Tailscale 為首選、Cloudflare Tunnel＋Access 次之），**不在 app 內做帳號系統** |

### D.1 硬約束（承襲，不因改版放寬）

| 約束 | 本次的含義 |
|---|---|
| 圖表／卡片只呈現數據，不做文字解讀 | 解讀**只**出現在 AI 面板的對話裡。**不做**「87/100 準備度」「速度評分」「今日建議」那類卡片——沒有信賴度的黑盒分數與結論句違反 §7.3 |
| §7.3 三欄位契約 | 面板不在前端拼數據餵模型；模型自己呼叫工具拿含信賴度的事實 |
| 呈現不等於決策、頁面無**決策型**寫入路徑 | 聊天**只開放唯讀工具**；`sync_garmin_data`／`generate_training_plan`／`add_context_note`／`save_external_workouts` 不進聊天。⚠️ **2026-09-10 修訂**：原文為「頁面無寫入路徑……無『同步 Garmin』按鈕」，經 Fu 決定放行**資料同步這一個動作**（`POST /api/sync`，見 N-19 §7）。理由：本約束要防的是「頁面出現沒有信賴度的判讀與決策」（§7.3），而同步不產生任何判斷——它只把 Garmin 既有資料搬進來，冪等可重複。**決策型寫入仍不放行**（產課表／記事／外部課表），聊天面板**仍只開放唯讀工具**——同步是使用者按的按鈕，不是模型能呼叫的工具 |
| 區網無驗證紅線（`app.py` docstring） | `/api/chat/*` 另加來源限制（D.3）；不對外網開放的紅線不變 |
| 憑證規範（`CLAUDE.md`） | `ANTHROPIC_API_KEY` 只從 `os.environ` 讀；`.env.example` 只放 key；log／錯誤訊息／回傳不得含 key |
| `app.js`（狀態）／`charts.js`（純畫圖）解耦 | **`charts.js` 零改動**。聊天面板獨立成 `coach.js`，只透過 `window.DashboardApp.getViewContext()` 讀 app 狀態，不碰 `state` |
| 零建置、無 CDN | Markdown 渲染自寫極簡版（段落／清單／粗體／行內碼／標題），先 escape 再轉 |
| RWD | body 不橫向捲動、觸控 ≥44px；側邊列、分頁列、面板各自捲動 |
| 驗證 | 每階段 Playwright 三寬度（1280／768／390）＋ Fu 親自瀏覽器確認後才 commit |

### D.2 App 殼（S1）

**桌機 ≥1280px：三欄** `240px | minmax(0,1fr) | 360px`

```
┌────────────┬────────────────────────────────────┬──────────────────┐
│ 跑步儀表板  │ 今日                    [7d 30d 90d ▾]│ AI 教練     [—][×]│
│ ● 今日      │ ┌ 今日卡（既有 #panel-today）─────┐ │ ▾ 對話：09/08 今天…│
│ ○ 行事曆    │ └────────────────────────────────┘ │ 你：今天該練嗎      │
│ ○ 訓練紀錄  │ ┌ 本週（既有 #week-strip）────────┐ │ 教練：…            │
│ ○ 訓練負荷  │ └────────────────────────────────┘ │  ▸ 查了 get_today  │
│ ○ 身體狀況  │                                    │ [今天照課表練？]    │
│ ─────────  │                                    │ [最近一場如何？]    │
│ 最後同步 09/07│                                   │ ┌──────────┐[送出]│
│ ☀ 🌙 🖥     │                                    │                  │
└────────────┴────────────────────────────────────┴──────────────────┘
```

- 側邊列 `position: sticky; top: 0; height: 100vh`，內容區自己捲。
- AI 面板可收合成 48px 直條；偏好經既有 `Theme.saveSettings({coachPanel: 'open'|'collapsed'})` 存。
- **range 切換與自訂區間搬到內容區頂列**，只在 訓練紀錄／訓練負荷／身體狀況 顯示。主題切換、`#meta-info`／`#notice-bar` 警語、mock 徽章搬到側邊列底部。
- 側邊列底部「最後同步」取自 `/api/today` 的 `freshness`。

**平板 768–1279px**：側邊列縮成 72px 圖示欄（圖示＋兩字標籤直排）；AI 面板改成右側 overlay 抽屜（360px），內容區頂列多一顆「AI 教練」開關。

**手機 <768px**：側邊列消失，改**底部分頁列**（5 格等寬、高 56px、圖示＋文字、`position: fixed; bottom: 0`，內容區留 `padding-bottom`）。AI 教練用右下浮動按鈕（56px，分頁列上方）開成全螢幕 sheet（頂部有返回）。訓練紀錄 view 改「清單 → 點進明細（頂部返回鍵）」兩段式。

**五個 view 與路由**（既有容器 id **全部保留**，只是搬進對應 view）

| 路由 | 側邊列 | 內容 | range 列 |
|---|---|---|---|
| `#/today` | 今日 | `#panel-today`＋`#week-strip`（本週條，從行事曆面板搬來）＋「查看課表」連結 | 隱藏 |
| `#/calendar`、`#/calendar/2026-09`、`#/calendar/plan` | 行事曆 | `#calendar-body`（**預設展開**，刪收合鈕）＋`#calendar-legend`；子模式 chip「月曆｜課表清單」（課表清單見 D.5） | 隱藏 |
| `#/sessions`、`#/sessions/1083` | 訓練紀錄 | 左 `#session-list`（從負荷面板搬來）；右 `#session-picker`＋`#session-body`（既有六個子容器） | 顯示 |
| `#/load` | 訓練負荷 | `#load-chart`＋`#trend-charts`；**`#panel-load` 保留為它們的祖先**（同步游標群組 `SYNC_GROUPS` 以此 id 查找） | 顯示 |
| `#/wellness` | 身體狀況 | `#wellness-charts`＋圖例 | 顯示 |

**路由機制**（`app.js`）
- `hashchange`＋首次載入解析 `#/view[/arg]`；無 hash → `#/today`。每個 view 是 `<section class="view" id="view-*" hidden>`，切換只動 `hidden`。
- **render-on-activate**：`widthOf()` 對 `display:none` 容器回 0，**不可在隱藏 view 內畫圖**。`state.dirty = {sessions, load, wellness}`：資料載入後只標 dirty＋渲染目前 view；切到 view 時若 dirty 才渲染；`bindResize()` 只重繪目前 view、其餘標 dirty。資料載入策略不變（`init()` 一次抓齊），只是渲染變懶。
- `selectSession(id)` 改為 `navigate('sessions', id)`；既有三處 `scrollToSection('panel-session')` 全改走路由。`bindSectionNav()`、`scrollToSection()`、`.section-nav` 標記整段刪除。
- `setRange()` 的 `history.replaceState` 目前只寫 `?range=`，**必須保留 hash**（`pathname + search + location.hash`）。
- `document.title` 隨 view 更新；側邊列／分頁列 `aria-current="page"`。
- 暴露 `window.DashboardApp.getViewContext()` → `{view, range, sessionId, month, date}` 供 `coach.js` 使用。

**要動的檔案**：`index.html`（重寫 body 骨架：`<aside class="sidebar">`、`<main class="content">`（頂列＋5 個 view）、`<aside class="coach-panel">`（S1 先放空殼）、`<nav class="tabbar">`、FAB；圖示用 inline SVG；新增 `<script src="coach.js">` 於 `app.js` 之後）、`app.js`、`styles.css`（殼的三欄／兩欄／單欄 grid、側邊列、分頁列、FAB、面板與抽屜；**新 token 一律以 `var()` 引用既有 token**，深色模式自動成立；刪 `.section-nav*`）。`charts.js`、`theme.js` 不動。

**S1 驗證**：三寬度截圖＋深色；每個 view 的圖表寬度正確（不為 0）；同步游標仍作用；深連結 `#/sessions/<id>` 直開；`?range=` 與 hash 並存；body 無橫向捲動；console 零錯誤；`grep -ri body_battery src/main/resources/assets/` 無結果。

### D.3 聊天後端（S2）

**檔案**

| 檔案 | 內容 |
|---|---|
| `src/main/python/mcp/server.py`（改） | 只新增 `READ_ONLY_TOOL_NAMES`／`WRITE_TOOL_NAMES` 兩個 frozenset（工具分類的**單一真實來源**）；工具與 prompt 常數**零改動** |
| `src/main/python/services/coach_chat.py`（新） | 對話迴圈（下述） |
| `src/main/python/services/coach_chat_store.py`（新） | 兩張表的 CRUD、一回合一交易 |
| `src/main/python/models/schema.sql`（改） | 兩張新表（下述） |
| `src/main/python/api/routes_chat.py`（新） | `/api/chat/*` 路由＋來源限制 |
| `src/main/python/api/app.py`（改） | 掛 `routes_chat.router`；啟動時 `get_connection(db_path).close()` 一次**確保新表存在**（FastAPI 進程原本從不執行 `schema.sql`）；讀環境變數組 `ChatConfig`；未設 key 仍啟動，`/api/chat/*` 回 503 |
| `requirements.txt`（改） | 新增 `anthropic` |
| `.env.example`（改） | 只加 key（見設定表） |

**對話迴圈（`coach_chat.py`）**
- 系統提示 = `src.main.python.mcp.server.coach()` 原文 ＋ `_PANEL_CONTEXT_NOTE`（接在 `_OUTPUT_SHAPE` 之後，比照 PLAN.md §7.6 風格層擴充點，**不碰約束層**）。`_PANEL_CONTEXT_NOTE` 內容：〔檢視脈絡〕行由 dashboard 介面自動附加；**本面板沒有寫入工具**——學員要同步、產課表、記錄軟性輸入時，明說請改用 Claude Code（因 `coach()` 約束 5／7 提到的 `add_context_note`／`generate_training_plan` 在這裡不存在）。
- 工具 schema：`await mcp.list_tools()` 過濾 `READ_ONLY_TOOL_NAMES`，`{name, description, input_schema}` 直接餵 Anthropic（SDK 已排除 `ctx` 參數，輸出即純 JSON Schema）。系統提示與工具表加 `cache_control`。
- 工具執行：在同一 process 內 `await mcp.call_tool(name, args, context=Context(mcp_server=mcp, request_context=ServerRequestContext(session=None, lifespan_context=AppContext(db=conn), protocol_version="in-process", method="tools/call")))`。SDK 用 `anyio.to_thread.run_sync` 跑同步工具，連線須 `check_same_thread=False`。**回傳格式不一致**：`-> dict` 得一個 TextContent、`-> list[dict]` 每項一個區塊、空值得 `content=[]`——一律合併所有文字區塊，空的送 `"null"`。`call_tool` 不接例外，須接住 `ToolError`（含參數驗證失敗）轉成 `tool_result is_error=True`。名稱不在允許清單 → 不執行、回 `is_error=True` 並說明。
- 迴圈：`stop_reason == "tool_use"` → 執行**全部** tool_use、所有 tool_result 放在**同一則** user 訊息、續問；`end_turn` → 結束；`max_tokens` → 發 `error` 事件（回覆被長度上限截斷）。回覆內容（含 `thinking` 區塊與 signature）**原樣保留、原樣重播**，不可改寫。
- LLM 端抽成極小介面 `LLMBackend.astream(system, tools, messages)`（產生事件＋最終 content blocks）；`AnthropicBackend` 用 `AsyncAnthropic().messages.stream()`；測試用 `FakeBackend`。**Anthropic 串流事件的形狀要讀已安裝 SDK 的原始碼確認，不可憑印象寫**；地端會對真實 API 做煙霧測試才算數。

**歷史與工具結果的體積控制**（真實 DB 上 `get_session_detail` 單次回傳最多約 13 萬字元、中位數約 4 萬；`get_wellness_trend 90d` 約 4.7 萬）
- 落地：原樣存，不裁。
- 送模型時：① 單一 tool_result 超過 `RC_COACH_TOOL_RESULT_MAX_CHARS` 就截尾＋附「（已截斷）」；② 最近兩個 assistant 回合以外的 tool_result 內容改成短佔位「（前次查詢結果已省略）」——tool_use／tool_result 成對結構保留、只換內容；③ 整體歷史以字元估算裁到 `RC_COACH_HISTORY_MAX_CHARS`，**只能在純文字 user 回合處切**。
- 刻意不在聊天層對特定工具的結構（如 `records.points`）降採樣，避免聊天層認得工具內部格式；實用上若教練因截斷失準再議。

**落地（`coach_chat_store.py`）**
- 訊息以 Anthropic content blocks JSON 原樣存；存下來的 `messages` 序列**就是**下次送 API 的 `messages`（user 文字訊息、assistant（含 tool_use）、user（只含 tool_result）、assistant 最終回覆，各為一列）。
- **一回合一交易**：user 訊息與該回合所有中間訊息在迴圈成功結束後一次 commit；失敗或中途斷線 rollback——**絕不落地沒有對應 tool_result 的 tool_use**（否則下一回合 API 會 400）。
- 標題取首則 user 訊息前 40 字。刪對話時**明確刪 messages**（`PRAGMA foreign_keys` 只在 `models.db.get_connection()` 設，不可依賴 CASCADE）。

**Schema**（主鍵、時間欄位、索引寫法比照既有 `context_notes`）
```sql
coach_conversations(id, athlete_id INTEGER NOT NULL REFERENCES athlete_profile(id),
                    title TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)
coach_messages(id, conversation_id INTEGER NOT NULL REFERENCES coach_conversations(id),
               seq INTEGER NOT NULL, role TEXT NOT NULL CHECK (role IN ('user','assistant')),
               content_json TEXT NOT NULL, created_at TEXT NOT NULL,
               UNIQUE (conversation_id, seq))
-- 索引：coach_messages(conversation_id, seq)
```

**API contract**（所有端點先過來源限制；`athlete_id` 慣例同 Task A）
```
GET  /api/chat/status
     → {"enabled": true, "reason": null, "model": "claude-sonnet-5", "client_allowed": true}
       enabled=false 時 reason 為「未設定 ANTHROPIC_API_KEY」；client_allowed=false 時本端點仍回 200（讓面板能解釋原因），其餘端點回 403
GET  /api/chat/conversations
     → {"conversations": [{"id": 1, "title": "今天該練嗎", "created_at": "...", "updated_at": "..."}]}   （updated_at 由新到舊）
POST /api/chat/conversations            body {}          → 201 {"id": 2, "title": null, ...}
GET  /api/chat/conversations/{id}
     → {"id": 2, "title": "...", "messages": [{"id": 7, "seq": 0, "role": "user", "content": [ ...content blocks... ]}, ...]}
DELETE /api/chat/conversations/{id}     → 204
POST /api/chat/conversations/{id}/messages   body {"text": "今天該練嗎？\n〔檢視脈絡〕view=today"}
     → 200, Content-Type: text/event-stream
```
串流事件（每則 `event:` ＋ `data:` JSON 一行，空行結尾）：
```
event: text         data: {"delta": "今天"}
event: tool_use     data: {"id": "toolu_x", "name": "get_today", "input": {}}
event: tool_result  data: {"tool_use_id": "toolu_x", "name": "get_today", "is_error": false, "content": "<模型實際看到的文字，可能已截斷>"}
event: done         data: {"message_id": 12, "stop_reason": "end_turn"}
event: error        data: {"message": "..."}      ← 之後串流結束；該回合不落地
```
狀態碼：未設 key → 503；來源不允許 → 403；對話不存在 → 404；`text` 空白 → 422。

**來源限制（`require_chat_client`）**：`ipaddress.ip_address(request.client.host)`——解析失敗一律 403（fail closed；`TestClient` 預設 host 是字串 `"testclient"`，測試須傳 `client=("127.0.0.1", 1)`）；`is_loopback` 放行（涵蓋 `::1`）；否則僅當 `RC_CHAT_ALLOW_PRIVATE_NETWORK=1` 且 `is_private` **或屬 `100.64.0.0/10`**（Tailscale；Python 3.12 對此段 `is_private` 為 False，須明列）才放行。uvicorn 只信任來自 127.0.0.1 的 `X-Forwarded-For`，區網裝置無法偽造。

**設定**（全部走 `os.environ`，沿用 `garmin_auth.py` 讀 `GARMINTOKENS` 的做法；專案沒有 dotenv 載入器，由使用者 shell 設定）

| 變數 | 預設 | 說明 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 無 → 聊天停用（503） | 唯一的祕密 |
| `RC_COACH_MODEL` | `claude-sonnet-5` | 可改 `claude-opus-5` |
| `RC_COACH_MAX_TOKENS` | `8192` | Sonnet 5 預設會思考，`max_tokens` 含思考量，太低會被截 |
| `RC_COACH_HISTORY_MAX_CHARS` | `120000` | 送模型的歷史上限 |
| `RC_COACH_TOOL_RESULT_MAX_CHARS` | `60000` | 單一工具結果上限 |
| `RC_CHAT_ALLOW_PRIVATE_NETWORK` | 關 | 見來源限制 |

**測試**（`unittest`，比照既有風格；fixture 用 `fixtures.build_fixture_db()`）
- `test_mcp_tool_classification.py`：`await mcp.list_tools()` 名稱集合 == `READ_ONLY ∪ WRITE` 且兩者不交集（防新工具漏分類）。
- `test_coach_chat.py`（`FakeBackend`）：系統提示前段 == `coach()` 原文；唯讀工具經 in-process MCP 真的查到 fixture DB 的資料（context 建法可比照 `test_mcp_server.py` 既有的 stand-in）；寫入工具名稱 → 拒絕且不執行；tool_use→tool_result 迴圈與並行 tool_use 合併；`ToolError` → `is_error`；`max_tokens` → error 事件；截斷／佔位／切點規則。
- `test_coach_chat_store.py`：建立／追加／讀回／一回合一交易的 rollback／刪除連 messages。
- `test_routes_chat.py`（`TestClient`）：SSE 框架格式；無 key → 503；非本機 → 403；開 `RC_CHAT_ALLOW_PRIVATE_NETWORK` 後 `192.168.x`／`100.64.x` 放行；無法解析的 host → 403。

**S2 驗證（地端才能做）**：對真實 DB 起 server、`curl -N` 打 SSE，確認模型真的呼叫 `get_today` 並在回覆末尾列事實清單；試打寫入工具名稱被拒；從另一台區網裝置打 → 403；問一場含 `records` 的 session 確認截斷不炸。

### D.4 聊天面板前端（S3，`coach.js`）

- 狀態：`{conversations[], activeId, messages[], streaming, panelState}`。
- 啟動先打 `/api/chat/status`：`enabled=false` → 面板顯示「伺服器未設定 ANTHROPIC_API_KEY」；`client_allowed=false` → 「AI 教練僅限本機使用，見 RC_CHAT_ALLOW_PRIVATE_NETWORK」；兩者都不允許輸入。
- 訊息渲染：使用者氣泡／教練氣泡（極簡 Markdown）；**工具呼叫顯示為可展開 chip**「▸ 查了 `get_session_detail`(id=1083)」，展開可見回傳 JSON（摺疊、等寬字）——把行為約束 4「回述事實清單」變成介面可見的東西。`thinking` 區塊不顯示。
- 串流：`fetch(POST)`＋`ReadableStream` 逐行解析 `event:`／`data:`（EventSource 不能 POST）。`error` 事件 → 保留已收到文字＋重試鈕（該回合未落地，重試即重送）。
- **「問這一頁」chips**（依 `getViewContext()` 動態產生；送出時在訊息末尾附一行 `〔檢視脈絡〕view=sessions session_id=1083 range=30d`，讓模型呼叫對的工具；**不在前端貼任何數據**）：今日「今天照課表練還是調整？」「最近一場練得如何？」；行事曆「這週課表跟實際對得上嗎？」；訓練紀錄（有 id）「解讀這場訓練的品質」「心率漂移的數值代表什麼？」；訓練負荷「目前負荷該增量、維持還是降量？」；身體狀況「近期身體數據有什麼要注意？」。
- 對話管理：下拉選既有對話、「新對話」、刪除（有確認）。
- mock 模式：`mock-data.js` 補 `/api/chat/status`／`/api/chat/conversations*` 的假資料；串流在 `coach.js` 內以本地假產生器模擬逐字輸出（含一個假 tool_use），UI 可離線開發。

### D.5 行事曆補完（S4）

- `GET /api/plan?athlete_id=`（`routes_dashboard.py`＋`dashboard_queries.get_plan()`）：包 `training_plan_store.get_active_schedule()`，純呈現、無新規則：
  ```json
  { "plan_source": "generated", "created_at": "2026-08-20T13:28:58+00:00",
    "weeks": [ { "week_index": 1, "start": "2026-08-24",
                 "days": [ { "date": "2026-08-24", "workout_type": "easy", "planned_distance_km": 5.0, "planned_pace_sec_per_km": 367, "notes": null } ] } ] }
  ```
  無生效課表 → `{"plan_source": null, "created_at": null, "weeks": []}`。
- 行事曆 view：月曆預設展開、月份切換寫進 hash、「課表清單」模式（逐週表，標示課表版本 `created_at`）。
- 訓練紀錄 view 手機兩段式。
- `mock-data.js` 補 `/api/plan`。

### D.6 雲端／地端分工

前提：① 本機未推的 commit 先 push（由 Fu 決定）；② 雲端**沒有也不得有** `output/running_coach.db` 與 `ANTHROPIC_API_KEY`，只能用 `fixtures.build_fixture_db()`＋`?mock=1`；③ 雲端環境需能 `pip install -r requirements.txt`，若要自檢版面需有 Playwright＋chromium，否則只做靜態檢查、視覺交給地端。

| 階段 | 雲端可做 | 必須地端 | 依賴 |
|---|---|---|---|
| S1 App 殼 | 全部程式碼；mock 模式下 Playwright 三寬度（若環境有） | 真實 DB 下三寬度截圖、同步游標、Fu 親看 | — |
| S2 聊天後端 | 全部程式碼＋單元測試；`pip install anthropic` | 真實 API 煙霧測試（唯一有 key 的地方）、區網 403 測試、**Anthropic 串流事件解析須地端驗過才算數** | 與 S1 檔案不重疊，可平行 |
| S3 聊天面板 | 全部程式碼（mock 串流） | 真實串流逐字、重新整理後對話仍在、Fu 實際問答 | S1＋S2 合併 |
| S4 行事曆補完 | 全部 | 真實 DB 視覺確認 | S1 |
| S5 文件收尾 | 全部 | — | 全部 |

流程：每個雲端 session 產出一條分支／PR → 地端 `git pull` → 地端跑真實 DB 與真實 API 驗證 → Fu 瀏覽器確認 → 合併。

### D.7 不在範圍

- 沒有信賴度的合成分數卡／雷達圖／結論句（解讀進面板）。
- 聊天開放寫入工具（同步、產課表、記事）——仍走 Claude Code；N-7 課表調整粒度未 grill 前不碰。
  ⚠️ 2026-09-10：`POST /api/sync` 已放行為**使用者按鈕**（N-19 §7），但**沒有**成為聊天可呼叫的工具——模型不該有能力自己去打 Garmin 的 API。產課表／記事／外部課表三者仍完全不放行。
- N-8 結構化記憶（聊天表只是原料）。
- app 內建帳號系統／HTTPS（D.0 第 4 條：放在 app 外面）。
- 風格層 N-5、環境配速 N-10、N-12 曲線倒掛。

---

## Task E：行事曆／趨勢／訓練紀錄改版（2026-10-24 定案，待執行）

> **緣由**：Fu 對照首頁後的回饋——行事曆只有顏色看不出內容、趨勢頁各區塊無邏輯分配（約 30 張圖平鋪，Garmin 卡與首頁重複）、訓練紀錄頁不直覺。
> 經三輪預覽討論定案。預覽稿存於 `output/preview/redesign-2026-10/`（個人資料目錄，不進版控）：`v4_cal4.png`／`v4_icons.png`（行事曆與圖示）、`v2_tr.png`（趨勢）、`v2_s1.png`（訓練紀錄）。
> **D.1 硬約束全部沿用**（零 CDN、本頁只呈現不解讀、三寬度不跑版、深淺色模式）。
> 對應 TODO：**N-29**。

### E.1 行事曆（大部分定案；圖示與外框細則待定）

**已定案**
- 維持**當月月曆**。月曆**從週日開始**排列；課表與完成率的計算週期**仍是週一～週日**，兩者互不影響。
- **每筆活動是一顆膠囊（圖示＋數值）**，放在當天的格子裡。不寫課表文字，點日期才看。
  - 有距離的活動顯示公里數（一位小數）。
  - 健身等沒有距離的活動顯示時間：未滿 1 小時為 `分:秒`（`52:30`），1 小時以上為小時、一位小數（`1.1h`）。
- **顏色只代表運動種類**，不代表課種。每種顏色在 `styles.css` 定義成 token，並有深色模式版本：

  | 種類 | 顏色 | `activity_type` |
  |---|---|---|
  | 跑步 | 藍 | `running`、`treadmill_running`、`track_running`、`trail_running`（`activity_types.RUNNING_ACTIVITY_TYPES`） |
  | 健身 | 橘 | `strength_training` |
  | 健走 | 粉 | `walking` |
  | 登山 | 綠 | `hiking` |
  | 自行車 | 黃 | `cycling` 等 |
  | 游泳 | 淺藍 | `lap_swimming` 等 |
  | 賽事 | 紫 | `workout_type='race'` 的活動（優先於上列）；未來賽事取自學員檔賽程（`race_names`） |
  | 其他 | 灰 | 以上皆非（例：`kiteboarding_v2`、`indoor_cardio`） |

- **暖身併入主課**：沿用 N-25 寫入的 `warmup_for_activity_id` 關聯，前端**不另寫判定規則**。膠囊顯示主課＋暖身的合計距離，點開後的詳情分開列出。
  - 會被關聯的條件（`warmup_links.py`）：同一天的跑步、暖身時長 5–15 分鐘、主課在暖身開始後 **70 分鐘內**開始、主課比暖身長。
  - 不符合條件的（例如間隔超過 70 分鐘）維持兩顆獨立膠囊。
- **圖例收進 ⓘ 提示**：放在月份標題旁，沿用首頁的 ⓘ SVG 元件。不在頁面底部放圖例。
- **用膠囊外框表示課表狀態**（初版，細則見下方待定）：

  | 情況 | 呈現 |
  |---|---|
  | 有課表，已執行 | 膠囊加實線外框 |
  | 沒排課的額外活動 | 膠囊無外框 |
  | 有課表，日期還沒到 | 空的虛線膠囊 |
  | 有課表，沒有執行 | 空的紅色虛線膠囊 |
  | 賽事，日期還沒到 | 紫色虛線膠囊＋賽事名稱 |

- **頂部三個數字**：本月跑量、本月活動次數、**連續訓練 N 週**。
  - 以週一～週日為一週，該週有任何一種活動就延續。
  - 本週還沒有活動時不算中斷，從上週往回算。
- **底部**：當月各運動種類合計（跑步、健走、自行車顯示公里；健身顯示次數＋總時間）。
- **不放週小計，也不放每週打勾欄。**
- **點日期**：沿用現有行為，在月曆下方展開當天的課表、實際內容，以及「看單場」連結。
- **手機**：格子結構和桌機相同，膠囊縮小。空的課表膠囊不寫文字，只保留外框。

**外框細則（2026-10-24 第二輪定案）**
- 一天有多筆活動時，**只框配對到的那一筆**。
- **運動種類不符**（例：課表是跑步，當天只做了健身）：健身膠囊**不加外框**，另外放一顆**紅色虛線膠囊**，表示課表沒有執行。
- **不猜測，用邏輯判定**。配對依序走 [`plan_reconciliation`](../../src/main/python/services/plan_reconciliation.py) 的規則：
  1. 有明確的 `linked_activity_id` 就用它。
  2. 同一天，而且運動種類相容。
  3. 已經併入主課的暖身不算第二個候選。
- **以上規則都判斷不出來時**（`ambiguous`），標成「**待確認**」：用另一種外框樣式，點開當天後顯示候選活動，等 Fu 確認。
  - **2026-10-24 Fu 同意開放寫入**（決策見 [PLAN.md §1](PLAN.md#1-決策記錄decisions-log)）。當日面板列出候選活動，各附一個「這場是課表」按鈕，點了就把 `training_plan.linked_activity_id` 寫成該活動；也要能取消。
  - **後端**：新增寫入端點，例如 `PUT /api/plan/{plan_id}/link`，body 為 `{activity_id | null}`。要檢查兩件事：活動與課表同一天、運動種類相容。來源限制沿用 `client_access`。只寫這一欄，不改課表內容。
  - **測試**：寫入、取消、拒絕不相容的組合；寫入後 `plan_reconciliation` 回傳 `exact`。
  - 2026-10-24 用真實資料檢查：有效課表（10/12 起共 9 天）中，同一天有多筆活動只有 10/20 一次（跑步＋健走），靠運動種類就能分開，**目前還沒出現過真正的歧義**。

**一天超過三筆活動**（預覽 `v5_ovf.png`）
- 格子裡只顯示前 3 筆，其餘收合成一顆中性灰的「還有 N 筆」。點那一天，下方會列出當天全部活動，包含時間和摘要。
- 排序：有配對到課表的那筆排第一，其餘依開始時間排。
- **手機**：最多 2 筆，其餘收成「+N」（2026-10-24 定案）。

**配色（2026-10-24 定案）**
- **淺色模式**：淡色底加深色字。
- **深色模式**：採方案 ①「飽和實心」，文字依底色亮度自動選黑或白，對比 ≥4.5:1（見 `v5_dark.png`）。黃、淺藍、粉、橘配黑字，其餘配白字。
- 已執行外框在深色模式是白框，中間隔一圈背景色。

**圖示（2026-10-24 定案）**
- 採 A3 雙色調：線條加 22% 同色填面（見 `v5_ic.png`）。全站共用一套，寫成 SVG 常數，不引用外部圖示庫。
- **人形不要火柴人**。跑步、健走、游泳三個圖示的候選（見 `v6_fig.png`）：
  - P1 剪影雙色
  - P2 奧運式粗筆畫（遠側手腳淡色）
  - P3 不畫人，改用跑鞋／腳印／泳鏡
- **人形定案（2026-10-24）**：跑步、健走用 **P2 原版**；游泳用**修正版**，頭和手臂之間留出空隙，手臂從肩膀劃一道弧線到頭頂前方（見 `v7_p2.png`）。
- **最終 SVG**：八個圖示都以 `currentColor` 上色，`viewBox` 為 24×24，淡色部分用 `stroke-opacity` 或 `fill-opacity` 做出雙色調。實作時直接搬進一個共用模組（例如 `js/core/sport-icons.js`），不要另外再畫一份：

  ```js
  const SPORT_ICONS = {
    run: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="3.1"><circle cx="16" cy="3.9" r="2.4" fill="currentColor" stroke="none"/><path d="M13.6 8.1 10 8.7 8.1 11.3M11.2 12.6 8.8 16.6 4.7 17.5" stroke-opacity=".4"/><path d="M14.2 7.3 11.2 12.6l3.4 2.8-1.2 5.4M13.9 8l2.9 2.7 2.8-.5"/></svg>',
    strength: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4.6" y="6.5" width="3.2" height="11" rx="1.2" fill="currentColor" fill-opacity=".22"/><rect x="16.2" y="6.5" width="3.2" height="11" rx="1.2" fill="currentColor" fill-opacity=".22"/><path d="M2.6 9.5v5M21.4 9.5v5M7.8 12h8.4"/></svg>',
    walk: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="3.1"><circle cx="12.8" cy="3.9" r="2.4" fill="currentColor" stroke="none"/><path d="M12.3 8.2 10 11.2l-.6 2.6M11.6 13.2 10 17.1l-1.2 3.9" stroke-opacity=".4"/><path d="M12.3 7.5 11.6 13.2l2 3.8 1 4M12.4 8.3l2 3 1.8 1.4"/></svg>',
    hike: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 19.5 8.6 9.6l3.3 5.1 2.6-3.6 7 8.4z" fill="currentColor" fill-opacity=".22"/><path d="M7.1 12.1 8.6 13.2l1.4-1.1"/><circle cx="17.5" cy="5" r="1.8" fill="currentColor"/></svg>',
    bike: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="5.6" cy="16.2" r="3.6" fill="currentColor" fill-opacity=".22"/><circle cx="18.4" cy="16.2" r="3.6" fill="currentColor" fill-opacity=".22"/><path d="M5.6 16.2 9.4 9.4h5.9l3.1 6.8"/><path d="M9.4 9.4 12 16.2h1.2"/><path d="M8 6.8h3M14.3 6.2h2.3l-1.3 3.2"/></svg>',
    swim: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path stroke-width="2.9" d="M3.4 13.4 12.6 12.4"/><circle cx="16.6" cy="12.2" r="2" fill="currentColor" stroke="none"/><path stroke-width="2.9" d="M11.2 12.2Q12.4 5 18.6 7.2"/><path stroke-width="1.8"  d="M2 16.8c1.7 0 1.7-1.3 3.3-1.3S7 16.8 8.6 16.8s1.7-1.3 3.4-1.3 1.7 1.3 3.4 1.3 1.6-1.3 3.3-1.3 1.7 1.3 3.3 1.3"/><path stroke-width="1.8" stroke-opacity=".4" d="M2 20.4c1.7 0 1.7-1.3 3.3-1.3S7 20.4 8.6 20.4s1.7-1.3 3.4-1.3 1.7 1.3 3.4 1.3 1.6-1.3 3.3-1.3 1.7 1.3 3.3 1.3"/></svg>',
    race: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8.2 2.8 10.6 9M15.8 2.8 13.4 9"/><circle cx="12" cy="14.8" r="5.6" fill="currentColor" fill-opacity=".22"/><path d="m12 12.2.9 1.8 2 .3-1.4 1.4.3 2-1.8-.9-1.8.9.3-2-1.4-1.4 2-.3z" fill="currentColor" stroke-width="1"/></svg>',
    other: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 12.5h3.8l2.3-5.5 4 11 2.4-5.5h6.5"/></svg>',
  };
  ```
- 健身、登山、自行車、賽事（獎牌）、其他（心跳線）使用 A3。

### E.2 趨勢（定案）

**版面**
- 區間選擇器維持現狀。
- 頁面分五區，前四區的順序與配色沿用首頁的分類 token：**訓練負荷 → 恢復與身體 → 跑力表現 → 訓練量與強度**，最後是獨立的 **Garmin 數據**區。
- 前四區的結構都是「**4 張數字卡**（數值＋與前一個等長區間比較的 ▲▼＋小走勢線）**→ 一張大折線圖**」。
  - 大圖上方是開關列，這一類**現有的每個指標都要有開關**，一個都不能少。
  - 數字卡要用到前一期的資料。目前 API 只回傳選定區間，實作時要確認是由前端多抓一段，還是由後端補欄位；若改後端，要另寫進 TODO 和 PLAN。

**各區的開關**（★ 為預設開啟）

| 區 | 開關 |
|---|---|
| 訓練負荷 | ★CTL、★ATL、★TSB（以 0 為中線的柱狀，綠＝新鮮、紅＝疲勞）、Garmin 急性負荷、Garmin 負荷比、恢復時間 |
| 恢復與身體 | ★HRV 夜間均值、★HRV 週均、靜止心率、睡眠分數、睡眠時長、血氧、壓力（睡眠）、壓力（全天）、步數、訓練準備度；另有「標出訓練日」選項，取代原本的粉紅底 |
| 跑力表現 | ★每場平均配速、★每場平均心率（從原「強度分布」移過來）、有氧 TE、VDOT |
| 訓練量與強度 | **心率區間分布（保留原設計）** ＋ ★每週跑量（依強度堆疊）、每場距離、次數 |
| Garmin 數據 | 見下方 |

**Garmin 數據區**（三層，由上到下）
1. 大折線圖，可以自己勾要畫哪些 Garmin 指標。
2. 小格子分數一覽。
3. **首頁式的 Garmin 圖卡**：直接共用 `garmin.js` 現有元件，不另寫一份。

**一張圖畫不同單位的線**
- 同單位共用一個 Y 軸，最多左、右兩個軸。
- 開到第三種單位時，自動改成每條線各用自己的刻度、只比形狀，實際數值看游標提示。

**其他**
- 開關狀態存在 `rc-dashboard-settings`（`theme.js` 是唯一的讀寫入口）。
- 每區的 4 張數字卡依預覽稿先做，Fu 之後可以再換。

### E.3 訓練紀錄（定案：C1 日誌列；展開內容之後再調）

**清單**
- 按週分組（週一～週日，與課表一致）。每週有標題列，顯示場數、距離、時間。
- 每一列的內容：
  - 課種色條
  - 日期和星期
  - 課種標籤＋活動標題
  - 距離、時間、配速（三欄對齊）
  - 分圈小圖：每根柱子是一圈，高度＝配速，顏色＝該圈平均心率落在哪個區間
  - 展開箭頭
- **後端需求**：分圈小圖要用到每圈的配速和心率，但目前 `/api/sessions` 清單沒有分圈資料（`activity_laps` 已有 `pace_sec_per_km`、`avg_hr_bpm`）。
  - 做法一：清單 API 加精簡的分圈摘要。
  - 做法二：前端展開時才另外抓。
  - 屬於 API 變更，要寫進 TODO 和 PLAN 給 Fu 確認後再做。

**點擊展開**
- 在原地展開，不換頁。
- 內容：大數字列，加上分頁「圖表／分圈／心率區間／隔天恢復」，再加「開啟完整單場」連結。
- 初版直接沿用現有單場分析的元件，之後再慢慢調整。

**手機**
- 每列只留日期、課種、距離、配速，分圈小圖移到第二行。

### E.4 實作順序與驗收
- **順序**：依 E.5 的子工作流程進行（取代原本「趨勢 → 訓練紀錄 → 行事曆」的單線順序）。
- **驗收**：
  - mock 模式和真實 DB 都要用 Playwright 測 1280／768／390 三種寬度，深色、淺色都看，不能有橫向捲動，console 不能有錯誤。
  - Fu 在本機瀏覽器親自確認。
  - 離線單檔也要能正常開啟，見 [OFFLINE_EXPORT.md](OFFLINE_EXPORT.md)。

### E.5 實作計畫：子工作流程（2026-10-24 建立）

**2026-10-24 盤點現有程式後的三個前提**
- **行事曆幾乎不用改後端**：`/api/calendar` 每筆活動已經帶以下資料，`activities` 也包含非跑步活動：
  - `activity_type`、`distance_km`、`duration_sec`、`title`
  - `warmup_for_activity_id`
  - `plan_match.state`（`exact`／`inferred`／`ambiguous`／`none`）
- **趨勢數字卡的「與前一期比較」不用改後端**：所有趨勢端點都接受 `range=custom:YYYY-MM-DD:YYYY-MM-DD`（`dashboard_queries.parse_range`），前端用前一期的起訖日再抓一次即可。選「全部」時沒有前一期，▲▼ 不顯示。
- **後端只有兩處要改**：B1 訓練紀錄的分圈摘要、B2 課表配對寫入。

**依賴關係**

```
W0 共用基礎 ─┬─> W1 趨勢 ──────────┐
             ├─> W2 訓練紀錄 <─ B1 ├─> W4b 離線模式收尾 ─> W4c iPhone 實機
             └─> W3 行事曆   <─ B2 ┘
B1、B2、W4a 可以和 W0 同時開工
```

**檔案衝突規則**（讓 W1／W2／W3 可以平行，各自開分支）
- `styles.css`：
  - 色彩 token 只由 W0 修改。
  - W1／W2／W3 各自在檔尾新增一段，用 `/* === E.x 頁面名 === */` 標出區塊，不改其他區塊。
- `charts.js`：新的圖表函式只由 W0 新增。W1／W2／W3 只呼叫，不修改。如果需要新的參數，回到 W0 補。
- `mock-data.js`、`core/state.js`：各流程只新增自己用到的 key，不改別人的。
- 合併順序依完成先後；合併前先 rebase 到最新的 main，並跑完整測試。

| 流程 | 內容 | 主要檔案 | 依賴 | 可在雲端做 | 必須在本機做 |
|---|---|---|---|---|---|
| **W0 共用基礎** | ① `js/core/sport-icons.js`：E.1 的八個 SVG，搭配 `activity_type → 運動種類` 對照，沿用 `activity_types.py` 的分類。② 運動種類色彩 token：淺色是淡底加深字，深色是方案 ① 的飽和實心加自動黑白字，並擴充 `test_dashboard_theme_contrast.py` 驗證對比 ≥4.5:1。③ `charts.js` 新增 `multiLineChart`：多條線、同單位共用軸、最多左右兩軸、超過兩種單位自動改成各自刻度、TSB 中線柱、游標提示。④ `charts.js` 新增 `lapStrip`：每圈一根柱，高度是配速，顏色是區間。⑤ ⓘ 提示元件確認可以重用 | `core/sport-icons.js`（新增）、`styles.css`（token）、`charts.js`、`core/state.js`、測試 | — | 全部，含 mock 模式截圖 | 真實 DB 下深色、淺色各看一次 |
| **B1 分圈摘要 API** | `/api/sessions` 每場加 `lap_strip: [{pace_sec_per_km, avg_hr_bpm, zone}]`。`zone` 用與 `hr_zones` 相同的 %HRR 區間換算；沒有心率時 `zone` 為 `null`，沒有分圈時 `lap_strip` 為空陣列，低於 Z1 為 `0`。同步更新契約文件與 mock。✅ 2026-10-24 Fu 同意，決策見 PLAN §1 | `dashboard_queries.list_sessions()`、本檔 API 契約、`mock-data.js`、`test_dashboard_queries.py` | — | 全部（fixture DB） | 真實 DB 檢查回應大小與區間是否正確 |
| **B2 課表配對寫入** | 新增 `PUT /api/plan/{plan_id}/link`，body 為 `{activity_id \| null}`。檢查同一天、運動種類相容，只寫 `linked_activity_id`，來源限制沿用 `client_access`。測試：寫入、取消、拒絕不相容的組合，寫入後 `plan_reconciliation` 回傳 `exact`。決策已記入 PLAN §1 | `routes_dashboard.py`、`training_plan_store.py` 或新的小函式、測試 | — | 全部 | 對真實 DB 寫一筆再取消 |
| **W1 趨勢** | E.2 全部：四區各有數字卡（含前期比較）＋大折線圖＋開關列；訓練量區保留心率區間分布；Garmin 區分三層，第三層直接共用 `garmin.js`。開關狀態存進 `rc-dashboard-settings`。重寫 `views/trends.js` 的版面組裝，`load.js`／`wellness.js`／`overview.js` 只保留資料轉換的函式，不能刪掉現有指標 | `views/trends.js`、`load.js`、`overview.js`、`wellness.js`、`styles.css`（E.2 區塊） | W0 | 全部（mock） | 真實 DB 下三種寬度、深淺色、Fu 親自確認 |
| **W2 訓練紀錄** | E.3 C1 日誌列：按週分組並附合計、對齊的數字欄位、`lapStrip`、點擊在原地展開（初版沿用現有單場分析元件）、手機兩行版面 | `views/sessions.js`、`styles.css`（E.3 區塊）、`mock-data.js` | W0、B1 | 全部（mock） | 同上 |
| **W3 行事曆** | E.1 全部：膠囊（圖示＋公里或時間格式）、暖身合併、外框狀態、待確認狀態與「這場是課表」按鈕（呼叫 B2）、一天超過 3 筆收合（手機 2 筆）、ⓘ 圖例、頂部三數字（含連續訓練週數）、底部各運動合計 | `views/calendar.js`、`styles.css`（E.1 區塊）、`mock-data.js` | W0、B2 | 全部（mock） | 同上；並用真實 DB 檢查 10/20（跑步＋健走）、10/22（暖身合併） |
| **W4a 匯出腳本** | [OFFLINE_EXPORT.md](OFFLINE_EXPORT.md) §2：`scripts/build_dashboard_offline.py`，包含 gzip、`--detail-months`、`--copy-to`／`RC_OFFLINE_EXPORT_DIR`、資安掃描、單元測試。要匯出的 URL 清單集中定義在一處，W1～W3 若新增請求就補進去 | `scripts/build_dashboard_offline.py`（新增）、`.env.example`、測試 | — | 全部（fixture DB） | 對真實 DB 產生一次，確認約 1.3 MB |
| **W4b 離線模式收尾** | 離線時隱藏線上功能（同步、分析、AI 教練、今日體感、自訂區間、配對確認按鈕）；頁首顯示「資料截至」；「今天」改用匯出日期；URL 清單補上 W1～W3 的新請求，包含前期比較用的自訂區間 | `shell/*`、`core/api.js`、W4a 腳本 | W1、W2、W3、W4a | 程式碼 | Playwright 以 `file://` 開啟測試 |
| **W4c iPhone 實機** | Fu 開啟 iCloud 雲碟、修改捷徑（OFFLINE_EXPORT.md §3、§4），用真機開啟 | — | W4b | — | **只有 Fu 能做** |

**建議的開工批次**
1. **第一批（平行）**：W0、B1、B2、W4a。
2. **第二批（平行）**：W1、W2、W3，每個流程等它依賴的項目合併後才開始。
3. **第三批**：W4b，最後是 W4c。

**每個流程的完成條件**
- 單元測試全部通過。
- mock 模式三種寬度、深淺色截圖無誤，沒有橫向捲動，console 沒有錯誤。
- 本機用真實 DB 驗證。
- Fu 在瀏覽器親自確認後才合併。
- 每個流程獨立 commit，訊息依全域規範。

**執行方式（2026-10-24 Fu 決定）**：全部在本機做。每個流程開一個平行 session，各自在獨立的 git worktree 分支上實作，完成後自行跑測試和 mock 截圖、commit。由主 session 檢查後併回 main，並用真實 DB 驗證。全部工項完成後，再對 Task E 做一次總核對。

---

## 呈現層設計原則：依「問題」分頁（2026-09-05 記錄，2026-09-08 已決策）

> **本節為呈現層的設計論述。2026-09-08 已決策，定案規格見上方 Task D**：
> 採納「依問題分頁」與側邊列導覽；**不採納**分數卡／雷達圖／結論句（違反 §7.3）；
> AI 教練做成受行為約束層約束、只用唯讀 MCP 工具的對話面板。
> 以下保留作為決策背景。對應 [TODO.md](TODO.md) 的 N-11。
> 動機是介面好用，Fu 才會願意每天打開、資訊才查得方便。

### 核心問題：現行版面依「資料粒度」分，而非依「使用者要回答的問題」分

我們現行三區塊為 單場分析 → 跨場趨勢 → 每日身體狀況，這是**資料的組織方式**。
應改為每頁對應一個使用者實際會問的問題：

| 分頁 | 該頁要回答的問題 |
|---|---|
| 總覽 | 最近狀態怎樣 |
| **準備度** | **今天該照課表、降量，還是休息** |
| 訓練負荷 | 訓練不足、穩定累積，還是該降量 |
| 心率與配速 | 有氧效率有沒有變 |

值得想的是：打開頁面時腦中的問題通常是「今天要不要跑、怎麼跑」，而不是「我想看跨場趨勢」。

### 三項具體做法

**① 今日決策卡** — 一句話結論 ＋ 三個支撐數字，放在第一屏。
我們的資料其實現成（`get_readiness_status`、[`training_load.py`](../../src/main/python/services/training_load.py) 的 ATL/CTL/TSB），
缺的只是一塊前端 panel 與一支聚合端點。三項裡成本最低。

**② 準備度拆成多維度呈現** — 速度／耐力／比賽適能／訓練負荷／恢復狀態分開顯示，不合成單一分數。
理由是：**單一分數低了，看不出是「累」還是「體能退步」**，而這兩者處置完全相反
（前者該休息，後者該加量）。合成一個分數等於把最關鍵的區辨資訊丟掉。原料我們有，需一支聚合端點。
⚠️ 但拆開呈現**不等於**做成黑盒分數卡——見下方「明確排除」。

**③ 既有計算能力的預填呈現** — VDOT 配速、心率區間、HRV 調整、ACWR、環境配速等，
若要呈現，**關鍵在預填 Fu 的真實資料**：要人手動輸入的工具不會真的被打開。
我們 [`vdot_engine.py`](../../src/main/python/services/vdot_engine.py) 已有 VDOT。
⚠️ 其中「環境配速」依賴 [TODO.md](TODO.md) N-10 的天氣回填，在 `activity_weather` 有資料前做不了。
⚠️ 另見 [TODO.md](TODO.md) N-18 的明確排除：不做成一格一格的獨立計算機集合。

### 這些既有約束仍然適用（不因改版放寬）

- 上方「RWD 硬性要求」與「架構要求」全部條款——特別是 **整頁 body 絕不可橫向捲動**、觸控目標 ≥44px、`app.js`／`charts.js` 解耦、`available:false` 要顯示 `reason` 不畫空圖
- 卡片上的數字**不可只顯示裸數字**：依 [PLAN.md §7.3](PLAN.md#73-定案框架事實--依據來源--信賴度) 三欄位契約須帶依據來源與信賴度；無法標記信賴度者不顯示
- 若「恢復狀態」取用 Garmin 的 Training Readiness／Body Battery，須帶 `garmin_blackbox`（`unusable`）標記且不可當結論依據（見 [PLAN.md §1](PLAN.md#1-決策記錄decisions-log) 2026-09-01 該條）
- ACWR 要用 `training_status.acwr_ratio`，**不是** `daily_wellness.acwr`（後者是 Garmin 貢獻度分數，語意不同）
- 呈現不等於決策：規則層永不自動改寫課表，卡片只呈現與建議
- 新頁面照既有慣例用 Playwright 實測多種寬度，並由 Fu 在瀏覽器實際確認後才算完成

### 明確排除的做法

**沒有信賴度的合成分數**（單一「準備度 87 分」這類黑盒數字、雷達圖、以及「今日建議：可執行高強度訓練」這種結論句）：
違反 [PLAN.md §7.3](PLAN.md#73-定案框架事實--依據來源--信賴度) 三欄位契約——分數的組成與權重不可追溯，
使用者無從判斷該不該信。解讀一律進 AI 教練對話面板（Task D）。

**把資料丟給 LLM 產一段話式的 AI 頁**：我們 Phase 3 已有規則引擎與事實層信賴度契約
（見 [PLAN.md §7](PLAN.md#7-事實層與判斷層的分工phase-3-規格依據-2026-08-25-grill-定案)），
教練面板必須走行為約束層與唯讀 MCP 工具，讓每個建議可追溯到它引用的事實。

### 四項既定原則（呈現層改版不得放寬）

本機優先／AI 只輔助不代替決定／推送前一定要確認／資料來源要標註信心程度。
四項皆為本專案既有規範（見 `CLAUDE.md` 憑證章節與 [PLAN.md §7](PLAN.md#7-事實層與判斷層的分工phase-3-規格依據-2026-08-25-grill-定案)），
列於此以說明**呈現層改版純為重排既有資料，唯一的實質能力缺口是環境資料**（N-10）。
