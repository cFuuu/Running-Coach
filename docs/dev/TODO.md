# AI 跑步教練專案 TODO

> 本檔案放**高頻變動內容**（Phase 勾選清單、待確認事項、尚未併入的新需求）。
> 願景、架構、決策記錄、課表模板等穩定內容見 [PLAN.md](PLAN.md)。
> 個人學員資料一律放 `output/`（不進版控），不寫入本檔。

## 進度總覽（2026-08-18 更新）

| Phase | 狀態 | 說明 |
|---|---|---|
| Phase 0 需求確認 | ✅ 完成 | 學員資料見 `output/athlete_profile.md` |
| Phase 1A 歷史資料匯入 | ✅ 完成 | 520 活動、1651 每日 wellness、13 項 metric_coverage |
| Phase 1C 統一資料模型 | ✅ 完成 | 7 張表，含多人可用性設計 |
| Phase 1E FIT 解析 | ✅ 完成 | 243/266 場（91%）分圈＋逐秒降頻＋自動分類 |
| **Phase 1.5 Dashboard** | 🔵 **進行中** | Task A/B/C 已完成並經瀏覽器實測；改版 Phase 1~5（深色模式/心率區間修正/tooltip/自訂區間/指標自訂）已完成；**Phase 6 行事曆檢視未開始** |
| Phase 1B 即時同步 | ⏸️ 延後 | Garmin 已封鎖自動化登入，待評估 MCP／Strava |
| **Phase 2 規則引擎** | 🔵 **進行中** | VDOT／週期化排程器／訓練負荷（ATL/CTL/TSB）／恢復判斷邏輯／資料庫整合層皆已完成；已對 Fu 本人成功產生並寫入 16 週課表（111 天）；N-2 外部課表實際匯入機制待 P-1；`max_hr_bpm` 為暫定保守值，待實測更新 |
| Phase 3 AI Coach | ⏸️ 未開始 | ★ 專案主目標 |

**環境**：conda `rc`（Python 3.12），套件見 `requirements.txt`／`environment.yml`。測試 285 項全通過。
**執行測試**：`"C:/Users/cFu/anaconda3/envs/rc/python.exe" -m unittest discover -s src/test/unit -p "test_*.py"`

**目前資料庫內容**（`output/running_coach.db`，15.3 MB，不進版控）

| 資料表 | 筆數 | 涵蓋範圍 |
|---|---|---|
| `activities` | 520 | 2019-12 ~ 2026-08（近 7 年）|
| `daily_wellness` | 1651 | 睡眠/步數/全天壓力近 6 年；HRV/SpO2 約 10 個月 |
| `activity_laps` | 2381 | 243 場跑步的每公里分圈 |
| `activity_records` | 68884 | 逐秒降頻（每 10 秒）|
| `metric_coverage` | 13 | 各指標實際可用起訖日 |
| `training_plan` | 0 | 待 Phase 2 產生 |

⚠️ **這是一次性歷史快照**（截至 2026-08-16），非即時同步。新資料需重新匯出並重跑匯入（冪等，可安全重複執行）。

## Phase 0 — 需求確認（blocking）

> 這是**每位新學員 onboarding 時都要收集的欄位清單**（Phase 3 的 N-1 onboarding 流程應據此設計）。首位學員的實際內容見 `output/athlete_profile.md`，不寫入本檔。

**身體狀況**
- [x] 基本身體數據（年齡／身高／體重／體脂率）
- [x] 跑步年資與目前訓練量
- [x] 傷病史與個人恢復上限訊號
- [x] 心率數據（靜止心率、最大心率）— 最大心率須確認為實測或手錶顯示值
- [x] 每週訓練頻率
- [x] 既有數據完整度（Garmin/Strava 累積年限、是否配戴睡眠監測）

**比賽計畫**
- [x] 目標賽事名稱與日期
- [x] 目標完賽時間（主要目標／高標）
- [x] 近期比賽成績（VDOT 起始配速區間的關鍵輸入）
- [x] 每週可訓練時段與偏好（跑步日／交叉訓練日分配、晨跑或晚跑）
- [x] 特殊限制（出差、旅遊、場地、是否有外部教練／跑團）

## Phase 1 — 資料基礎建設（Garmin 資料導入 subtask）

> **MVP 範圍**：本階段先只做 1A + 1C，讓 Phase 2/3 儘快有真實資料可用。1B（即時同步三層備援）與 1D（憑證管理）延後到之後真的要做即時同步時再啟動，詳見 [PLAN.md §1 決策記錄](PLAN.md#1-決策記錄decisions-log)。

**1A. 歷史資料回補（MVP，先做）**
- [x] 向 Garmin Connect 申請官方個人資料匯出（帳號設定 → Export Your Data，GDPR/CCPA 機制，零風險，一次性快照）——2026-08-17 已取得，放在 `input/garmin_export/`（不進版控，屬個人原始資料）
- [x] **評估 [GarminDB](https://github.com/tcgoetz/GarminDB) 能否直接解析匯出包＋FIT 檔到 SQLite**（2026-08-17 研究結論，詳見下方；2026-08-17 拿到實際檔案後結論已更新，見「匯出包內容盤點」）
- [x] **實作 parser**（2026-08-17，見 [`src/main/python/services/garmin_export_parser.py`](../../src/main/python/services/garmin_export_parser.py) + [`garmin_import_runner.py`](../../src/main/python/services/garmin_import_runner.py)）：客製 parser 直接讀 `DI-Connect-Fitness`／`DI-Connect-Wellness`／`DI-Connect-Metrics` 的 JSON。FIT 逐秒／GPS 軌跡解析**刻意延後，見下方 1E**（不是遺漏，是排序決策）
  - ✅ **不 hardcode 個人識別資訊**：匯出包根目錄用 `discover_export_roots()` 遞迴搜尋 `DI_CONNECT` 標記自動定位；`userProfileId` 用 `discover_user_profile_id()` 從活動記錄或 glob 掃描檔名 token 取得（實測發現 Garmin 檔名格式不只一種：`<日期範圍>_<id>_<類型>.json` 與 `<id>_<類型>.json` 並存，parser 兩種都處理）；解析出的 `userProfileId` 透過 `athlete_source_identity` 對應到 `athlete_id`，重複匯入同一帳號會歸戶到同一人、不同帳號各自新建，已用單元測試驗證兩種情境
  - ✅ **單位換算已用已知真實成績反推驗證**（不是憑欄位名稱猜的）：Garmin 匯出用 `CENTIMETER`／`MILLISECOND`／`CENTIMETERS_PER_MILLISECOND`／`KILOJOULE`，非欄位名稱字面意思（distance 不是公尺、duration 不是秒、calories 不是大卡）。用 2025-11-23 10K（52:30、10.11K）反推確認換算係數，且 `measurements[].unitEnum` 欄位也獨立佐證
- [x] 統一寫入本地標準化 schema（見 1C）——parser 已接上 schema.sql 的 upsert 邏輯
- [x] **補齊 steps／全天 RHR／全天壓力（2026-08-17）**：使用者發現 `steps` 全為 NULL，追查後確認**不是解析漏掉，而是來源檔根本不含此欄位**——`healthStatusData.json` 只有 HRV/HR/SpO2/皮膚溫度/呼吸率。步數實際在從未解析過的 `DI-Connect-Aggregator/UDSFile_*.json`（每日總覽）。新增 `parse_daily_summary()` 後補上三項：
  - `steps`：涵蓋 2020-12 起近 6 年
  - `all_day_stress_avg`：**獨立新欄位**，不覆蓋既有 `stress_avg`——後者來自 `sleepData.avgSleepStress`（僅睡眠期間），兩者範疇不同不可互相取代
  - `resting_hr_bpm` **優先序合併**：healthStatusData 有值優先（較精確），缺的日期才退回 UDSFile 全天 RHR，藉此把 RHR 可用範圍從約 10 個月延伸到近 6 年
  - 連帶修正既有 bug：`steps` 雖早在 INSERT 語句中，卻不在 `ON CONFLICT DO UPDATE SET` 子句內，代表重複匯入時既有列的 steps 永遠不會被更新
- [x] **驗證最大心率（資料面分析，2026-08-17）**：查詢 `activity_records`（逐秒）／`activity_laps`／`activities` 的心率分布，結論——手錶顯示的 200 bpm**並非反覆出現的實測值**，資料庫實測最高為 **192 bpm**（2022-12-18 lsd 21.26K），190+ 出現在至少 6 個不同日期、185+ 累計 148 次，非單次感測器雜訊，具生理一致性；但觸發這些數值的都只是 easy/tempo/lsd 訓練強度、非力竭測試，故 192 只是**下界**，真實 HRmax 可能更高。**200 大機率為年齡公式估算值而非實測**。暫定保守值 194-196（192+ 緩衝）供 Phase 2 配速區間計算暫用。
  - [x] **2026-08-20 已寫入 `athlete_profile`**：`max_hr_bpm=195`（`max_hr_source='observed_from_data'`）、`resting_hr_bpm=56`（近 30 天 `daily_wellness` 平均值），供 Phase 2 orchestrator 實際運作
  - [ ] **待辦（需使用者本人執行）**：實際進行一次最大心率測試（熱身後 3-5 分鐘全力跑到力竭，或多組衝刺間歇）取得真正實測值後更新 `max_hr_bpm` 並改 `max_hr_source='measured'`，資料分析無法替代生理測試
- [ ] 順帶盤點歷史比賽成績（協助補齊配速推算依據）——2026-08-20 確認近 90 天活動皆未標記 `workout_type='race'`，Phase 2 orchestrator 目前靠非全力候選的心率換算路徑運作，仍建議之後補標記實際比賽成績以提升配速推算信賴度

> **實測驗證（2026-08-17）**：對 Fu 的真實匯出包完整跑過一次（`python -m src.main.python.services.garmin_import_runner`），寫入 520 筆活動、1580 筆每日 wellness、11 個指標的 `metric_coverage`。與 `output/athlete_profile.md` 已記錄的已知成績逐項比對：2025-11-23 10K 算出配速 5:12/km、avgHR 148、maxHR 159、距離 10.1089km，與手動記錄完全一致；`metric_coverage` 算出的各指標日期範圍也與先前手動盤點的結果吻合（活動 7 年、HRV 約 10 個月、Training Readiness 約 3 年）。單元測試（15 項，合成假資料，不含真實個人資料）與這次真實資料驗證分開進行，兩者都通過。

> **GarminDB 評估結論（2026-08-17，草案）**：GarminDB 的 `--download` 走 Garmin Connect SSO 登入，會撞上與 garth 完全相同的封鎖——Garmin 自 2026 年 3 月起在 Cloudflare 層永久封鎖自動化 SSO 登入（`/sso/signin`、`/mobile/api/login`），瀏覽器登入不受影響。這不是「要不要接受風險」的問題，是現在整條自動化下載路徑本來就是壞的，跟已經決定不做 1B 是同一個坑。GarminDB 的 `--import` 可以純離線處理已下載的 FIT 檔進 SQLite，但**不會處理 Garmin 官方匯出包裡的 JSON**（見下方盤點），只認 FIT。

> **匯出包內容盤點（2026-08-17，拿到實際檔案後）**：`input/garmin_export/` 解壓後主結構是 `DI_CONNECT/`，跟本專案相關的是 `DI-Connect-Fitness`（520 筆活動摘要 JSON，每筆 97 個欄位，含 avgHR/maxHR/power/cadence 等）、`DI-Connect-Wellness`（`healthStatusData.json` 逐日一筆，**直接含 HRV、RHR、SpO2、皮膚溫度、呼吸率**；另有 `sleepData`、`bioMetrics`、`heartRateZones` 等）、`DI-Connect-Metrics`（`TrainingReadinessDTO` 含每日 Training Readiness 分數、HRV 週均、睡眠分數、恢復時間、ACWR；`RunRacePredictions` 含 Garmin 自算的 5K/10K/半馬/全馬預測時間；還有 `EnduranceScore`、`MetricsAcuteTrainingLoad`）。另外 `DI-Connect-Uploaded-Files` 兩個 zip 裡有約 **20,778 個 FIT/GPX 檔**（遠多於 520 筆活動，代表包含逐時/逐日的原始監測 FIT，不只是單次跑步）。
>
> **這推翻了原本「GarminDB 優先」的假設**：`healthStatusData`／`TrainingReadinessDTO`／`RunRacePredictions` 這些是 **Garmin 伺服器端算好的分數**，不存在於任何 FIT 檔裡（FIT 只有裝置端的原始感測器數值）——GarminDB 只解析 FIT，**天生就拿不到這些欄位**，不管檔案結構兜不兜得上都一樣。換句話說，這些衍生分數只能靠**直接讀官方匯出包的 JSON**取得，沒有 FIT 路徑可以還原。
>
> **結論**：Phase 1A 改為**客製 parser 為主，直接讀 `DI-Connect-Fitness`／`DI-Connect-Wellness`／`DI-Connect-Metrics` 的 JSON**寫入統一 schema；GarminDB／FIT 解析降級為輔助角色，只在需要 JSON 沒有的逐秒配速、GPS 軌跡、分圈資料時才用（`DI-Connect-Uploaded-Files` 裡的 FIT）。不再需要驗證 GarminDB 對匯出包的相容性，此問題已由「用不到」取代「相容不相容」。

**1C. 統一資料模型與品質標記（MVP，先做）**
- [x] **設計 SQLite schema**（2026-08-17，見 [`src/main/python/models/schema.sql`](../../src/main/python/models/schema.sql)，已用 sqlite3 實際執行驗證通過）
- [x] 加入 `source`、`source_version`、`fetched_at`、`has_wellness_data` 完整度欄位——直接併入 `activities`/`daily_wellness` 各自的欄位，取代原規劃的獨立 `source_metadata` 表（單一資料表兩個查詢場景不需要額外 join，避免過度設計）
- [x] 新增 `athlete_profile` 表（對應 `output/athlete_profile.md`，見 [PLAN.md §6](PLAN.md#6-個人資料存放位置)）與 `training_plan` 表（呼應 N-2，`plan_source` 區分 AI 課表／跑團課表）
- [x] **多人可用性補強（2026-08-17）**：本專案目標是任何人都能用的通用工具，Fu 是第一個真實測試對象，schema 需能承載多位 athlete，非單人假設。已驗證 `athlete_id` 外鍵在 `activities`／`daily_wellness`／`training_plan` 三表皆已正確設計（原先不需要修）；新增：
  - `athlete_source_identity` 表：對應 Garmin/Strava 等來源的帳號識別（如 Garmin `userProfileId`）到內部 `athlete_id`，讓 parser 重複匯入同一人資料時能正確歸戶，而非每次新建 athlete。`UNIQUE(source, external_ref)` 確保同一來源帳號不會被誤植到兩個 athlete
  - `metric_coverage` 表：記錄每位 athlete、每個指標的實際可用起訖日。起因是實測發現同一支錶不同指標的歷史回溯長度差異極大（活動紀錄 7 年 vs HRV/SpO2 僅 10 個月），這對任何使用者都會發生，規則引擎判斷前應先查這張表而非查了才發現是空的
  - `daily_wellness` 補齊實測發現的欄位：`spo2_pct`、`skin_temp_c`、`respiration_rate`、`hrv_weekly_avg_ms`、`sleep_score`、`training_readiness_score`、`recovery_time_hours`、`acwr`（來源：`healthStatusData.json` 與 `TrainingReadinessDTO`，原 schema 只有 HRV/RHR/睡眠/壓力/Body Battery/步數，沒有這些欄位）
  - 已用 sqlite3 實際執行驗證：雙 athlete 情境、身分對應查詢、`metric_coverage` 讀寫、`daily_wellness` 新欄位讀寫、重複 `external_ref` 被 UNIQUE 正確擋下
- [ ] 待實際資料匯入時，依真實資料再檢視欄位是否足夠（目前 `raw_data_json` 保留未逐欄位建模的原始欄位，如步幅、垂直振幅、觸地時間、NP、功率等）

**1E. FIT 解析（每公里分圈 + 逐秒降頻 + 訓練類型）——2026-08-17 提前實作**

> **為何從「延後」改為「現在做」**：原本判斷 dashboard 可以先用 Garmin 匯出 JSON 的整場摘要做。但實測 266 場跑步後發現，JSON 裡的 `splits` 是**手動按錶的不規則分段**（某場 10km 只有 2~5 段、長度 3.42/2.97/3.76km，還夾雜 0km 空圈），且僅 94 場有多於 1 段。使用者把「單場訓練成效分析」列為最高優先，而這靠摘要資料做不到——FIT 檔裡才有整齊的每公里分圈（實測某場 21km：22 圈、每圈精準 1000.0m、含每圈平均心率）與逐秒資料。

- [x] **實作 FIT parser**（見 [`fit_parser.py`](../../src/main/python/services/fit_parser.py)）：解析 session/lap/record，含逐秒降頻與心率漂移計算，純函式不碰資料庫
- [x] **實作訓練類型判定**（見 [`workout_classifier.py`](../../src/main/python/services/workout_classifier.py)）：依分圈配速結構推測 interval/lsd/tempo/easy，閾值集中管理，標記 `workout_type_source='auto'` 供使用者覆寫
- [x] **實作匯入 runner**（見 [`fit_import_runner.py`](../../src/main/python/services/fit_import_runner.py)）：從 zip 直接讀取（不解壓到磁碟）、對應活動、寫入 DB
- [x] **schema 擴充**：新增 `activity_laps`、`activity_records` 兩表；`activities` 加 `workout_type`／`workout_type_source` 欄位。⚠️ 後者因 `CREATE TABLE IF NOT EXISTS` 對既有表無效，另在 [`db.py`](../../src/main/python/models/db.py) 加了可重複執行的 `ALTER TABLE` 遷移，既有資料庫不必砍掉重建
- [x] **運動類型不寫死（2026-08-17，使用者要求）**：`import_fit_files()` 新增 `import_sports`／`classifiable_sports` 兩個參數，CLI 對應 `--sports`／`--classifiable-sports`，預設皆只有 `running`。兩者分開的理由：未來匯入騎車時逐圈逐秒仍值得存，但用跑步的配速門檻分類騎車會產生誤導標籤，故「匯入」與「分類」須為獨立開關。連帶修正 `_recent_averages()` 內另一處隱性寫死（算近期基準時寫死只認跑步的 activity_type，改為參數傳入）
- [x] **補上 `fit_import_runner.py` 的單元測試**：此模組原本完全沒有測試（先前只測純函式模組 `fit_parser`／`workout_classifier`），新增 8 項涵蓋最高風險的 `match_activity()` 邊界情境（時區偏移超過容忍值、多候選取距離最近者、缺時間/距離）與 sport 過濾／分類 gating
- [x] **對真實資料完整匯入並驗證**：見下方實測結果
- [x] 單元測試累計 44 項全通過（合成假資料，不含真實個人資料）

> **FIT 匯入實測結果（2026-08-17）**
> 掃描 20,778 個 FIT，成功匯入 **243 場**（跑步 266 場的 **91%**），分圈 2,381 筆、逐秒降頻 68,884 筆，對應失敗僅 1 場、錯誤 0，耗時約 12 分鐘，DB 15.3 MB。
> 抽驗已知的 21km 那場：**22 圈、每圈精準 1.0km**，配速 7:10／6:39／6:59 與心率 150／164／162 皆與直接讀 FIT 的結果一致。降頻效果：該場原始 9,037 筆逐秒 → 907 筆。
> 訓練類型自動分類分布：easy 87、tempo 50、unknown 45、lsd 39、interval 22。`unknown` 佔約 18%，為分圈數不足 3 圈無法判斷配速結構者。
> 重構為可設定運動類型後重跑，數字完全一致（2381／68884／243），確認為行為保持的重構。

> **⚠️ 實測踩到的坑：FIT 檔名編號無法對應資料庫**
> FIT 檔名是 12 位數的 **upload ID**（`<email>_152627722646.fit`），資料庫 `external_id` 是 11 位數的 **activity ID**（`23997807612`）。實測用 external_id 比對 266 場跑步：**0 筆對得上**。
> 正確做法：用 FIT `session.start_time`（**UTC**）＋總距離比對 `activities`（存的是**當地時間**）。實測驗證：FIT `2021-12-18 23:03:36` UTC ↔ DB `2021-12-19T07:03:36`，距離 21163.94m ↔ 21.1639km 完全吻合。
> 時區偏移**不寫死 +8**——以「時間差在 ±14 小時內且距離差 <1%」搜尋，偏移由資料自然推導，維持多使用者可用性。

> **效能考量**：20,778 個 FIT 檔中，大小中位數僅 793 bytes（多為每日監測片段而非活動）。以檔案大小 3000 bytes 為下限先過濾，可把掃描量降到約 4,659 個（約 15 分鐘）。門檻取 3000 是因為資料庫中最短的跑步活動僅 107 秒（估約 7KB），取更高的門檻會漏掉短跑步。

---

**1B. 持續同步（三層備援）——延後**
- [ ] 主力：評估/整合 [etweisberg/garmin-connect-mcp](https://github.com/etweisberg/garmin-connect-mcp)（現成 27+ 工具，Playwright 真瀏覽器繞過 Cloudflare）或參考其設計自建
- [ ] 釐清並實作「session 過期（例行重新登入）」vs「結構性失效（需切換 Strava）」的判斷邏輯，避免小問題觸發整條備援鏈
- [ ] 備援：Strava API（帳號已開自動同步）——註冊 Strava API App、取得 client credentials、實作 OAuth token 管理與 refresh
- [ ] 設計來源切換後的資料去重邏輯（時間+距離+時長指紋比對，避免 Strava 補的資料跟 MCP 回補的歷史重複）
- [ ] 單位／時區正規化層（三種來源格式不一致）

**1D. 憑證與機密管理——延後（進 1B 時才需要）**
- [ ] 本地機密儲存策略（`.env` 不進 git，或用 Windows Credential Manager）
- [ ] 為未來可能公開專案預留「每位使用者自帶自己憑證」的設計——不可硬編碼任何 token，需有清楚的 setup 說明
- [ ] `.gitignore` 與 `.env.example` 範本

---

## Phase 1.5 — 本機 Dashboard（★ 進行中，下一步）

> 使用者要求圖表化查看，優先序（由小到大）：**單場分析 → 跨場趨勢 → 每日身體狀況**。
> 完整規格與 API contract 見 [DASHBOARD_TASKS.md](DASHBOARD_TASKS.md)（三個 Task 的共同契約）。
> 排在 Phase 2 之前的理由：資料已入庫但目前只能用 Python 指令查，先讓資料「看得見」才能驗證品質、也才知道 Phase 2/3 該分析什麼。

**Task C — FIT 解析**：✅ 已完成（見上方 1E）

**Task A — 後端查詢層 + FastAPI**（2026-08-17 完成，經主 session 整合修正）
- [x] `dashboard_queries.py`：純查詢函式，不 import fastapi，可獨立單元測試
- [x] `api/app.py` + `api/routes_dashboard.py`：FastAPI app、靜態檔掛載、CLI 入口
- [x] **訓練後恢復關聯查詢**：即時計算「訓練日 vs 隔天 HRV/Readiness 變化」，**不新增 schema**（決策理由見 [PLAN.md §1](PLAN.md#1-決策記錄decisions-log)）
- [x] 把 `garmin_export_parser.py` 的單位換算私有函式改為公開供重用（純機械式改名，邏輯零改動）
- [x] 單元測試：87 項全通過（既有 44 + 新增 43），涵蓋 range 篩選邊界、`clipped` 標記、laps 來源優先序、`hr_drift` 計算、缺值不補 0

> **主 session 整合時修正的 3 個問題**（Task A 用假資料開發時無法發現，靠真實 DB 交叉比對才抓到）：
> 1. **`hr_zones` 單位是毫秒不是秒**：`raw_data_json` 的 `hrTimeInZone_N` 欄位實測是毫秒（例：`hrTimeInZone_2=3629313` 對應 60.5 分鐘），已改用 `ms_to_sec()` 換算，並同步修正測試 fixture
> 2. **手動分圈退回路徑的欄位路徑錯誤**：Task A 原本假設 `split.get("distance")`／`split.get("avgHr")` 是扁平欄位，實測發現真實結構是 `split["measurements"]` 陣列、用 `fieldEnum`（`SUM_DISTANCE`／`SUM_DURATION`／`WEIGHTED_MEAN_HEARTRATE`）標記，已改用 `_measurement()` helper 正確解析
> 3. **SQLite 跨執行緒 bug**：FastAPI 把同步 dependency 丟進執行緒池執行，`yield conn` 與 `finally: conn.close()` 不保證落在同一 thread，導致 `sqlite3.ProgrammingError`（已用乾淨環境重現兩次確認非偶發殘留問題）。修法：`sqlite3.connect(db_path, check_same_thread=False)`，已用 40 個真併發請求壓力測試驗證零錯誤
>
> **已用真實 DB 交叉比對通過**：2025-11-23 10K（配速 5:12/km、avgHR 148、maxHR 159、10.1089km）、2021-12-19 21K（22 圈、每圈精準 1.0km、source=fit）、HRV `range=all` 正確回 `clipped:true`

**Task B — 前端 Dashboard**（2026-08-17 完成）
- [x] `index.html`／`styles.css`／`app.js`／`charts.js`，純 HTML/CSS/JS 零建置、不引用 CDN、不裝圖表庫
- [x] 三區塊：單場分析（含每公里分圈圖、逐秒配速/心率曲線、心率漂移、HR 區間分布）→ 跨場趨勢 → 每日身體狀況（訓練日疊圖）
- [x] **RWD 不跑版**：viewport meta、SVG viewBox、X 軸刻度依寬度動態疏密、列表手機版改卡片、觸控目標 ≥44px、整頁 body 絕不橫向捲動——Task B 已用 Playwright 實測 6 種視窗寬度（1280/1920/768/390/320px + range=all）全數 PASS（無橫向捲動、X 軸標籤零重疊、無 JS console 錯誤）
- [x] `app.js`（狀態）與 `charts.js`（畫圖）解耦，為未來滑動互動預留
- [x] 假資料機制：`mock-data.js` + `window.MOCK_API`，網址加 `?mock=0` 可強制走真實 API 而不改檔案
- [x] `grep -ri body_battery` 確認乾淨

**整合驗證（主 session，2026-08-17）**
- [x] 全測試通過（87 項）+ 對真實 `output/running_coach.db` 打 API 交叉比對已知數字（見上方 Task A 附註）
- [x] 後端併發壓力測試（40 併發請求跨 7 種端點，零錯誤）
- [x] **桌機瀏覽器實際打開頁面查看**（2026-08-18，見下方「使用者實測回饋改版」，Fu 全程在瀏覽器上逐階段確認）
- [ ] 手機實機測試（連得到 ✓ 且不跑版 ✓，兩項分開驗）——尚未執行

---

### 使用者實測回饋改版（2026-08-18，Phase 1~5 已完成，Phase 6 未開始）

> Fu 打開瀏覽器實測後提出 6 項問題／需求，逐階段修正並各自 commit。詳細技術決策見 Claude 計畫檔（本機 `.claude/plans/z6-hazy-thimble.md`，不進版控）。

**Phase 1 — CSS 變數收斂 + 深色模式**（commit `23bcab7`／`f3666dd`）
- [x] 約 50 處寫死顏色收斂成 CSS 變數（畫面零視覺變化，純重構）
- [x] 新增 `theme.js`：淺色／深色／跟隨系統三態切換，localStorage 記憶，`<html data-theme>` 單一深色定義（不重複寫 media query）
- [x] 深色模式下 3 處語意會失效的既有規則已個別處理（`.dot` 白色圓心、`.bar-partial` 半透明白、圖表色提亮降飽和）

**Phase 2 — 心率區間 Z6 修正**（commit `1f653c7`）
- [x] 查明根因：Garmin `hrTimeInZone_0~_6` 共 7 欄，`_0` 是暖身時間（275/286 場非零，真實資料）、`_6` 恆為裝置固定 padding（266 場全為 0）
- [x] 實測發現 `sum(Z0~Z5) == duration_sec`，故百分比分母須含暖身時間，否則各區間佔比虛增
- [x] 改為**固定顯示 Z1~Z5**（值為 0 也照列出，不因某場沒進某區間就少一列），暖身時間獨立列於圖表下方——此為 Fu 驗收時要求調整（原計畫是丟棄尾端連續 0，但跨場比較需要軸線一致）

**Phase 3 — 跨圖同步參考線**（commit `298b3fc`）
- [x] Fu 參考 Garmin Connect 畫面，要求把「單圖各自浮動 tooltip」改成「多圖同步垂直參考線＋各圖內部顯示數值標籤」
- [x] 分 3 個獨立同步群組（單場分析＝經過時間、跨場趨勢＝場次日期、每日身體狀況＝逐日日期），心率區間圖（無時間軸）維持單圖 tooltip
- [x] 途中修正：離散模式圖表（分圈圖／週跑量）原本無法被同步、觸控裝置 tap 後被 pointerleave 立即關閉

**Phase 4 — 自訂時間區間**（commit `16d2ee7`）
- [x] 後端用 `custom:YYYY-MM-DD:YYYY-MM-DD` 前綴，`resolve_range()` 單一入口零改動讓 6 端點取得能力，自訂區間不套用「錨定資料庫最新日」規則
- [x] 前端 range 按鈕改後端 `/api/meta` 動態產生（不再 3 處各自硬編碼），新增日期選擇面板 + URL/localStorage 持久化
- [x] 途中修正：面板 `[hidden]` 屬性被 CSS class 規則蓋過導致無法真正隱藏

**Phase 5 — 指標自訂**（commit `03f363e`）
- [x] 後端 `WELLNESS_METRIC_DEFS` 成唯一真實來源，開放 4 個新指標預設顯示（睡眠時長／HRV 週均／恢復時間／ACWR）、呼吸率預設隱藏（涵蓋率僅 17.6%）
- [x] 前端新增「⚙ 自訂」面板：上下箭頭排序＋勾選顯隱（不用拖曳，HTML5 drag&drop 在行動瀏覽器無效）；localStorage 存 order/hidden 而非完整可見清單，未來後端加新指標不會被舊設定永久隱藏
- [x] 途中修正：排序操作後面板自動關閉的可用性問題（改為保持開啟）
- [x] 併入視覺微調：同步游標標籤日期改 `MM/DD` 補零格式（不含年份，避免擁擠）＋數值粗體；X 軸刻度字體 11px→13px（已驗證 5 種寬度零重疊）；訓練日標記從短豎線改為整欄背景色帶

**Phase 6 — 行事曆檢視（未開始）**
- [ ] 新端點 `GET /api/calendar?month=YYYY-MM`，月曆邊界用真實今天／當月，**刻意不走 `resolve_range()`**（該函式錨定資料庫最新日，會讓月曆被截斷）
- [ ] 顯示全部運動類型（非僅跑步），用 `activity_type` 上色（`workout_type` 有 277/520 筆 NULL 不適合當主要顏色維度）
- [ ] 前端新增第四個 panel（動到 `index.html` 版面結構，故排最後）；週一為週首、日期一律用 UTC，與現有 `weeklyVolume()` 慣例一致
- [ ] 點擊日期格帶入既有 `selectSession()` 單場分析路徑

> **驗證慣例**：每階段用 Playwright 寫自動化腳本（存於本機 scratchpad，不進版控）驗證互動行為 + 跑 `unittest`，全數通過後才請 Fu 用瀏覽器實際確認，通過才 commit。目前累計 106 項單元測試、逾百項 Playwright 檢查。

> **接續方式**：API server 啟動指令不變（見上方），開瀏覽器連 `http://127.0.0.1:8000/?mock=0`。下一步是 Phase 6 行事曆，或先處理 Fu 提到「UI 格式還有很多想微調」的後續細節（尚未列出具體項目，需再次詢問）。

## Phase 2 — 訓練科學規則引擎（純程式邏輯，不依賴 AI）

> 本階段的規格依據見 [PLAN.md §5 全馬週期化課表模板](PLAN.md#5-全馬週期化課表模板phase-2-規格依據)。
> 模組邊界、VDOT 成績篩選、負荷模型、恢復判斷、版本化等設計決策已於 2026-08-18 grill 定案，詳見 [PLAN.md §1 決策記錄](PLAN.md#1-決策記錄decisions-log) 與 §5.4~5.9。

**模組結構**（分層比照 Phase 1 的 `fit_parser.py`／`workout_classifier.py` 慣例，各自純函式、可獨立單元測試，最後由 orchestrator 組裝）：

- [x] **VDOT/配速引擎**（2026-08-19，Task 2.2/2.3）：見 [`vdot_engine.py`](../../src/main/python/services/vdot_engine.py)。不限比賽成績，所有活動皆為候選；低強度活動先以心率強度（%HRR）推算等效全力配速再代入 Daniels 公式；雙軌新鮮度門檻（短距離 90 天／半馬全馬 6–12 個月）；通過門檻後依距離代表性排序取優先；Riegel 跨距離推算（指數 1.06）
- [x] **週期化排程器**（2026-08-19～20，Task 2.5～2.9）：見 [`periodization_scheduler.py`](../../src/main/python/services/periodization_scheduler.py)。Base/Build/Peak/Taper 全馬框架，輸出到**單日級別**；支援**低至每週 3 次跑步**配置；`is_first_marathon: bool` 驅動配速保守緩衝與補給演練日標記；外部限制窗口（`skip`/`reduced`/`flexible`）與外部課表日期跳過（`_apply_constraint_windows`／`_apply_external_dates`）皆已實作；已有範圍性測試（Task 2.9）
  - [x] schema migration（Task 2.4）：`training_plan.plan_source` 改為 `generated`/`external`，新增 `is_active`／`superseded_by` 版本化欄位
- [x] **訓練負荷計算**（2026-08-20，Issue #13/#14）：見 [`training_load.py`](../../src/main/python/services/training_load.py)。`compute_daily_loads()` 跑步優先用心率相對 HRR 百分比估算強度，缺心率退回用配速相對 VDOT easy 配速估算（標記不確定）；重訓同套 HRR 公式，連心率都沒有則用 50% HRR 保守估計並標記不確定；`compute_training_load_series()` 用 Banister EWMA 標準做法遞推 ATL（7 天）/CTL（42 天）/TSB
- [x] **恢復判斷邏輯**（2026-08-20，Issue #16/#17/#18）：見 [`readiness.py`](../../src/main/python/services/readiness.py)。`assess_readiness()` 綜合連續訓練天數／TSB 趨勢／HRV 相對 7 日均值下降三維度，任一觸發即 `readiness: low` 並附觸發原因；不自動改寫 `training_plan`；HRV 缺資料時該維度跳過，不影響其他維度判斷
  - [x] 個人化恢復閾值存 `athlete_profile.high_risk_consecutive_training_days`（Issue #16，schema migration 已補），NULL 代表未設定，判斷邏輯退回預設值 6 天
  - [x] `suggest_recovery_threshold(conn, athlete_id)` 分析函式（Issue #18）：掃描歷史連續訓練段與對應 HRV 變化估算建議閾值，樣本不足或無惡化訊號時明確回傳「資料不足」，不自動寫回

- [x] **純函式已接到資料庫**（2026-08-20，Issue #19/#20/#21）：
  - [x] [`training_plan_store.py`](../../src/main/python/services/training_plan_store.py)（#19）：`save_schedule()` 把 `generate_schedule()` 輸出寫入 `training_plan`，同一天已有生效中的 `generated` 舊列時正確標記 `is_active=0`／`superseded_by`，不刪除不覆蓋；另提供 `get_active_schedule()`／`get_plan_history_for_date()` 查詢函式
  - [x] [`training_plan_generator.py`](../../src/main/python/services/training_plan_generator.py)（#20）：`generate_and_save_plan()` 串接「查活動歷史→算 VDOT/配速→產生單日課表→寫入」全流程；VDOT 無法推算時明確中止、不寫入資料庫
  - [x] [`training_load_queries.py`](../../src/main/python/services/training_load_queries.py)（#21）：把 `training_load.py`／`readiness.py` 接到 `activities`／`daily_wellness`／`athlete_profile` 真實資料；`compute_readiness_for_athlete()` 一鍵跑完整條查詢→計算流程
  - ⚠️ **修復**（見上方 db.py 的 CHECK 重建遷移）：對真實資料庫實測時另外發現 `training_plan.plan_source` CHECK 一直卡在 Task 2.4 之前的舊版（`ALTER TABLE` 無法修改既有 CHECK，早前 migration 只補了欄位沒補 CHECK），導致真實資料庫完全無法寫入 `training_plan`；已新增重建表格的遷移修正，並在 `output/running_coach.db` 實際套用驗證
  - ✅ **2026-08-20 已補齊 Fu 的心率參數**：`max_hr_bpm=195`（`max_hr_source='observed_from_data'`，取自 2026-08-17 資料分析的實測 192 bpm + 保守緩衝，仍待日後實際做一次力竭測試更新為 `'measured'`，見 Phase 0 待辦）、`resting_hr_bpm=56`（近 30 天 `daily_wellness` 平均值，n=31）。比賽成績維持空白（近期活動皆未標記 `workout_type='race'`，未臆測代填），改靠非全力候選的心率換算路徑
  - ✅ **端到端驗證成功**：`generate_and_save_plan()` 已對 Fu 本人實際產生並寫入一份 16 週課表（111 天，VDOT 42.13），`get_active_schedule()` 可正確查回；`compute_readiness_for_athlete()` 同樣運作正常

- [ ] **N-2 外部課表協調**：`training_plan.plan_source` 欄位與排程器跳過邏輯已就緒（見上）；尚未做的是實際匯入/解析機制，待 P-1 有真實跟團課表格式後再啟動
- [x] **驗證**（Task 2.9 + #20）：`periodization_scheduler` 已有不變量式單元測試與參數空間範圍性測試；`training_plan_generator.py` 新增含真實 DB 互動的整合測試。手動展示 Fu 的實際課表供其確認合理性一項，待補齊 `max_hr_bpm`／近期比賽成績後才能實際產生課表

## Phase 3 — AI Coach 分析層（★ 專案主目標所在，資源應優先集中於此）
- [ ] 設計 MCP server，把 Phase 1 資料查詢 + Phase 2 規則引擎包成 tools
- [ ] 定義「訓練品質評估」的分析維度：配速執行度、心率漂移（⚠️ 需要 1E 的 FIT 逐秒資料，daily_wellness 摘要資料做不到）、恢復是否充足、課表遵從度等
- [ ] 對話式教練互動：查詢近況、解釋建議原因、動態微調課表
- [ ] **N-1**：新學員 onboarding 流程須詢問希望的稱呼，並在後續對話一致使用
- [ ] 建立評估回饋迴圈：AI 建議 → 實際執行 → 下次分析納入偏差比對

## Phase 4 — 之後可做（非 MVP）
- [ ] 視覺化 dashboard
- [ ] 多使用者／公開發佈準備（若決定開源）
- [ ] 訓練提醒／通知機制

---

## 待確認事項（Open Items）

> 僅列**影響專案開發方向**的項目。學員個人層級的待確認事項獨立編號（`A-` 前綴），記錄於 `output/athlete_profile.md`，不與此處共用編號。

| # | 狀態 | 待確認事項 | 為何重要 / 卡住什麼 |
|---|---|---|---|
| P-1 | 🟡 暫緩 | 外部課表（跑團）整合方式 | 使用者決定等實際看過跑團課表再定。Phase 2 先把「能匯入／標記外部課表」的彈性做進去，不鎖定誰為主，見下方 N-2 |
| P-2 | 🟢 **已解決** | Garmin 官方匯出實際涵蓋範圍 | 2026-08-17 盤點確認：涵蓋活動摘要（520 筆）、每日 HRV/RHR/SpO2/呼吸率、Training Readiness、ACWR、比賽時間預測，以及約 2 萬筆原始 FIT。**結論見 1A：改為客製 parser 讀 JSON 為主**，GarminDB／FIT 解析降級為輔助，詳見 1A 段落 |

## 新增需求（討論後補充，尚未併入 Phase）

- **N-1 教練稱呼 onboarding**：新學員加入時，AI 跑步教練要先詢問學員希望被怎麼稱呼，並在後續對話中一致使用。屬 Phase 3 對話層需求，已加入 Phase 3 TODO。
- **N-2 外部課表協調**：學員可能同時參加跑團並參考其課表。這代表課表產生器不能假設「自己是唯一課表來源」，需要能匯入／協調外部課表（至少要能在分析時知道某次訓練來自外部安排）。設計方向待 P-1 確認後併入 Phase 2。
