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
| **Phase 2 規則引擎** | 🔵 **進行中** | VDOT／週期化排程器／訓練負荷（ATL/CTL/TSB）／恢復判斷邏輯／資料庫整合層皆已完成；已對 Fu 本人成功產生並寫入 16 週課表（111 天，VDOT 42.91）；N-2 外部課表實際匯入機制待 P-1（等有真實跟團格式）；`max_hr_bpm=198`（手錶顯示值，日後可用實測值校正，非阻塞） |
| **Phase 3 AI Coach** | 🔵 **進行中** | ★ 專案主目標。MCP server 已完成，7 個唯讀 tools 已實測成功（含修復一個 SQLite 跨執行緒 bug）；`generate_training_plan` 寫入型 tool 待測；**N-6（訓練方法論）與 N-4（事實層/判斷層分工）已於 2026-08-25 grill 定案**（見 [PLAN.md §5](PLAN.md#5-訓練方法論的選擇-2026-08-25-grill-定案)／[§7](PLAN.md#7-事實層與判斷層的分工phase-3-規格依據-2026-08-25-grill-定案)）；**下一步為 N-3 科學依據**（範圍依 N-6 拆為兩批：課表結構出處 → Higdon/Daniels；生理閾值出處 → 運動生理文獻，後者與流派無關可先做），其後為訓練品質評估、對話邏輯/教練人設/N-1 onboarding |

**環境**：conda `rc`（Python 3.12），套件見 `requirements.txt`／`environment.yml`。測試 301 項全通過。
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
  - [x] **2026-08-20 已寫入 `athlete_profile`**：`max_hr_bpm=198`（`max_hr_source='watch_display'`，Fu 本人確認採用手錶目前顯示值，非資料分析暫定值）、`resting_hr_bpm=56`（近 30 天 `daily_wellness` 平均值），供 Phase 2 orchestrator 實際運作
  - [ ] **待辦（需使用者本人執行，非阻塞）**：實際進行一次最大心率測試（熱身後 3-5 分鐘全力跑到力竭，或多組衝刺間歇）取得真正實測值後更新 `max_hr_bpm` 並改 `max_hr_source='measured'`，資料分析無法替代生理測試
- [x] 盤點歷史比賽成績——2026-08-20 Fu 確認**近期沒有比賽成績**，維持空白不臆測代填；Phase 2 orchestrator 靠非全力候選的心率換算路徑運作（已驗證可行），待未來有新比賽成績再補標記 `workout_type='race'` 以提升配速推算信賴度

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
- [x] 新增 `athlete_profile` 表（對應 `output/athlete_profile.md`，見 [PLAN.md §8](PLAN.md#8-個人資料存放位置)）與 `training_plan` 表（呼應 N-2，`plan_source` 區分 AI 課表／跑團課表）
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

> 本階段的規格依據見 [PLAN.md §6 全馬週期化課表模板](PLAN.md#6-全馬週期化課表模板phase-2-規格依據)。
> 模組邊界、VDOT 成績篩選、負荷模型、恢復判斷、版本化等設計決策已於 2026-08-18 grill 定案，詳見 [PLAN.md §1 決策記錄](PLAN.md#1-決策記錄decisions-log) 與 §6.4~6.9。

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
  - ✅ **2026-08-20 已補齊 Fu 的心率參數**：`max_hr_bpm=198`（`max_hr_source='watch_display'`，Fu 本人指定為手錶目前顯示值；先前資料分析暫定的 195 已作廢，改用此值）、`resting_hr_bpm=56`（近 30 天 `daily_wellness` 平均值，n=31）。比賽成績確認**維持空白**（Fu 表示近期沒有比賽成績），系統靠非全力候選的心率換算路徑運作，不臆測代填 `workout_type='race'`
  - ✅ **端到端驗證成功**：`generate_and_save_plan()` 已對 Fu 本人實際產生並寫入一份 16 週課表（111 天，VDOT 42.91，以 `max_hr_bpm=198` 重新產生），`get_active_schedule()` 可正確查回；`compute_readiness_for_athlete()` 同樣運作正常

- [ ] **N-2 外部課表協調**：`training_plan.plan_source` 欄位與排程器跳過邏輯已就緒（見上）；尚未做的是實際匯入/解析機制，待 P-1 有真實跟團課表格式後再啟動
- [x] **驗證**（Task 2.9 + #20）：`periodization_scheduler` 已有不變量式單元測試與參數空間範圍性測試；`training_plan_generator.py` 新增含真實 DB 互動的整合測試；已用 Fu 真實資料實際產生課表（見上）

## Phase 3 — AI Coach 分析層（★ 專案主目標所在，資源應優先集中於此）
- [x] **設計 MCP server**（2026-08-20）：見 [`src/main/python/mcp/server.py`](../../src/main/python/mcp/server.py)。8 個 tools，薄封裝既有 `dashboard_queries.py`／`training_load_queries.py`／`training_plan_store.py`／`training_plan_generator.py`，不新增規則判斷；`lifespan` 機制建立一次性 DB 連線注入每個 tool；stdio transport
  - ⚠️ **重要發現**：MCP Python SDK 剛從 v1 改版到 v2（PyPI `mcp` 目前預設安裝即 v2.0.0），`from mcp.server.fastmcp import FastMCP` 這個網路上常見教學仍在用的 import path 在 v2 已移除，改為 `from mcp.server import MCPServer`（用法幾乎一致：`@mcp.tool()`、docstring 自動變 description、支援 async）；`requirements.txt` 已鎖 `mcp[cli]>=2.0.0,<3` 避免未來 v3 無預警壞掉
  - 8 個 tools：`get_athlete_meta`／`list_running_sessions`／`get_session_detail`／`get_wellness_trend`／`get_recovery_impact`／`get_readiness_status`／`get_active_training_plan`（讀）、`generate_training_plan`（**寫**，description 明確標示會建立新版本課表並取代舊排程）
  - `suggest_recovery_threshold` 暫緩納入 v1，避免第一版 tool 清單過大難以驗證，下一輪可加
  - 已用 16 項單元測試驗證（直接呼叫 tool 函式本體，含 datetime.date 序列化正確性）；已用 `claude mcp add running-coach` 實際註冊到本機 Claude Code，`claude mcp get running-coach` 確認 `Status: ✔ Connected`（handshake 成功）
  - ✅ **2026-08-20 對話中實測完成**：7 個唯讀 tools（`get_athlete_meta`／`list_running_sessions`／`get_session_detail`／`get_wellness_trend`／`get_recovery_impact`／`get_readiness_status`／`get_active_training_plan`）已逐一實際呼叫，回傳 Fu 的真實資料正確。`generate_training_plan`（寫入型）尚未主動測試——會取代目前生效課表，屬有副作用動作，待使用者決定時機再測
  - ⚠️ **修復**：實測時發現與 Task A 曾在 FastAPI 踩過的**同一類 SQLite 跨執行緒 bug**（見上方 Phase 1.5 附註）在 MCP server 重現——`app_lifespan` 用預設 `sqlite3.connect()` 開一次連線注入所有 tool call，但 MCP SDK 執行各 tool 呼叫是在跟 lifespan 不同的 thread，導致 `SQLite objects created in a thread can only be used in that same thread`。修法沿用同一原則：[`db.py`](../../src/main/python/models/db.py) 的 `get_connection()` 新增 `check_same_thread` 參數（預設 `True`，其餘 12 處呼叫端不受影響），MCP server 呼叫時傳 `False`。過程中另發現有 3 個殘留的舊 server process 同時在跑（沿用了修改前的程式碼），需手動終止並重啟 Claude Code 才讓新程式碼生效——MCP server 是獨立 process，程式碼修改不會自動熱重載。301 項單元測試全過
- [x] **★ N-6 決定訓練方法論的選擇**（2026-08-25 grill 定案）：結論見 [PLAN.md §5](PLAN.md#5-訓練方法論的選擇-2026-08-25-grill-定案)，一手查證見 [docs/research/training-methodologies.md](../research/training-methodologies.md)
  - **框架被推翻**：原「單押一派 vs 多派融合 vs 依情境切換」問錯層級。盤點後四維度中只有**課表結構**存在流派分歧——配速換算（VDOT/Riegel 無替代品）、負荷模型（Banister/Coggan，四位作者皆未提出負荷量化模型）、恢復判斷皆非流派選擇。專案「早已在融合」，但那是各維度各有唯一可用解，不是選擇的結果
  - **定案：分層選用**——量的成長曲線與週頻率用 **Higdon Novice 1**（首週 24.1K、4 跑+1 交叉+2 休，四派中唯一在頻率上對得上）；長跑佔比／強度分配／品質課上限用 **Daniels 百分比規則**（長跑規則在 40 英里線分岔，是其預期低量使用者的正面證據；強度上限皆為百分比會自動縮放）
  - **明確排除 Hansons**：官方教練明述 Beginner 前提為已能跑 32K/週且具 5K–半馬經驗，尖峰達 88K/週；Hansons 自承 16 英里上限「不是魔術數字」，是從週跑 50–70 英里推導。Fu 週均 15K 時長跑僅該 2.3–2.8K，**cumulative fatigue 在物理上不可能發生**
  - **非「四不像」的理由**：Higdon 未提出強度分配主張，其體系內無規則層可被破壞，故填入 Daniels 規則不拆解任何流派的自洽性
  - **重啟條件**：週跑量穩定 ≥45K 且週練 ≥5 次持續 8 週。不採「先跑一週期再評估」——實測 2026-01～06 半年僅 17 次跑步，該實驗設計沒有檢定力
- [ ] **Phase 0「重建規律期」實作**（承 N-6）：正式週期前的前置期，以**頻率**為目標（穩定週 3–4 跑，里程不設要求），連續 3–4 週達標才進 Higdon 週期第一週。驗收標準為行為指標而非配速/距離
- [ ] **品質課絕對下限**（承 N-6）：暫定主段 ≥3K 或 ≥12 分鐘，低於此則該週不排該課種、改全 easy。**須在程式碼與文件明確標註為工程實務決策、無文獻出處**（週 15K 時 Daniels 的 T 上限 10% 僅 1.5K，短於暖身；四派文獻均查不到實務下限）
- [x] **★ N-4 事實層與判斷層的分工比重**（2026-08-25 grill 定案）：結論見 [PLAN.md §7](PLAN.md#7-事實層與判斷層的分工phase-3-規格依據-2026-08-25-grill-定案)
  - **前提變更**：因「跟團課表介入時系統退居分析層」拍板，系統不再產生課表，**價值 100% 押在判讀上**，本項風險等級提高
  - **§7.4 第 3 點（錯誤事實放大）成立，且已有實際案例**：`acwr` 欄位存的是 Garmin `acwrFactorPercent`（0–100 貢獻度）卻被標成「急慢性負荷比（ACWR）」，已於本次修正。實測偏誤盤點：`workout_type` unknown+NULL 達 **25.6%**（原記 18%，低估）、HRV 僅 292 天（佔 wellness 17.7%）、HRmax 未實測
  - **但未推翻分層**：讓 AI 改看原始數據不更安全（它看到 `acwr: 47` 只是把規則的錯誤語意換成自己瞎猜的語意）。**定案為補上第三欄位**——事實層輸出契約改為 `{數值, 依據來源, 信賴度/已知偏誤}`，且信賴度**必填**，無法標記就回傳 `None` 不給裸數字
  - **衍生處置**：①`workout_type` 為 unknown/NULL 時不輸出類型相關判讀（少評 25.6% 優於在 25.6% 上給錯判讀）②HRV 短期變化標可信、長期基準標低信賴，並以 Garmin 1086 天序列作偏移對照 ③AI 每次建議須**回述所用事實清單**（失敗歸因與比重驗證的唯一入口）
  - **界線判準定案**：閾值拆開處理——事實層輸出「數值＋門檻＋門檻適用性」，**不**輸出「偏高」這類已定性結論，定性由 AI 做（門檻的適用性即門檻的信賴度，與三欄位契約同形狀）
  - **軟性輸入定案**：新增 `context_notes`（自由文字＋日期範圍＋athlete_id），**不**預設類型/嚴重度結構化欄位（尚不知如何分級，硬訂即在假設上建構）。⚠️ 硬邊界：軟性輸入永不進事實層，僅作判斷層補充輸入（無法驗證，參與規則計算等於在事實層開一個沒有信賴度可標的洞）
- [x] **🐛 TSB 觸發門檻單位不相容（N-3 第一批，2026-08-29 已修）**：`TSB_LOW_THRESHOLD = -30.0` 取自 TrainingPeaks 慣例，但該慣例定義在 **TSS 尺度**上；本專案負荷為自建「秒 × %HRR」，尺度相差約 30 倍，−30 幾乎貼著 0
  - **修正**：改用該學員自身歷史 TSB 分布的第 10 百分位（`TSB_LOW_PERCENTILE`），樣本 < 60 天時不做判斷。實測 readiness low 由 **53.0% 降至 11.8%**，TSB 觸發由 139 次降至 14 次，且組成變健康（HRV 21 次 > TSB 14 次，符合「HRV 為恢復主要訊號」）
  - **明確不採 ACWR 式比值**：`TSB/CTL = 1 − ATL/CTL` 為 ACWR 的單調變換，繼承相同數學耦合缺陷；ACWR 已遭 Impellizzeri et al. 系統性駁斥
- [ ] **N-3 第一批的後續項目**（承 2026-08-29 查證，見 [physiological-thresholds.md](../research/physiological-thresholds.md)）
  - [ ] **HRV 改用 LnrMSSD + SWC**（第二步；第一步的資料充足度檢查已完成）：`hrv_ln = ln(rMSSD)` → 4 週基線 mean/SD → 觸發線 `mean − 0.5×SD` → 基線每週更新。🟢 有完整文獻支持，是四項閾值中唯一如此者
  - [ ] **配速執行度容差**：⚠️ **無任何文獻定義「偏差多少算沒執行」**。建議 easy/long 用區間判定且只在偏快時提示；threshold ±3%、interval 偏慢 −3% 提示而偏快改提示「超速無額外效益」——±3% 為 🔴 純工程決策，實作時須明確標註無文獻背書
  - [ ] **環境修正（優先度高於容差本身）**：🟡 Daniels/VDOT 規則 `adjustment_pct = max(0, (temp_F + dewpoint_F - 130) / 10) × 1%`，作為判定前的預處理。**台灣氣候會讓此規則一年中大半時間觸發，不做修正則夏季課表會被系統性判為未執行**。Ely et al. 2007 顯示較慢的跑者受罰更重，單一係數會低估影響，須用自己的資料重新校正
  - [ ] **心率漂移若要判讀**：須先改算 Pa:HR（現為純心率差、分母無配速）並加坡度校正（GAP），在此之前任何閾值都不成立；閾值應取個人 P75/P90 而非搬 5%
  - [ ] **可選**：Plews 指出 LnrMSSD 的 **CV 塌縮**（日間變異異常變小）為非功能性過度努力的早期訊號，方向與直覺相反（HRV「太穩定」也是壞事），現行實作完全未捕捉此維度
- [ ] **N-3 建立訓練科學依據基準（承 N-6／N-4 結論，待 grill 深度討論後定案）**：規則引擎的判讀閾值不可憑感覺或由 AI 即興決定，須參考市面上科學化訓練方法與運動生理學界的權威依據，並在程式碼與文件中標註出處
  - **動機**：目前「訓練品質評估」若交由 AI model 臨場判讀，等於每次對話標準都可能飄移，且無法解釋依據。改為規則引擎後，閾值本身就必須有可追溯的來源，否則只是把飄移換成寫死的任意數字
  - **既有依據盤點**（PLAN.md §6 已採用，本次應補標出處與版本；若 N-6 決定更換或融合流派，本項範圍須同步調整）：Daniels VDOT 配速推算、Riegel 公式（指數 1.06）跨距離換算、80/20 強度分配、週跑量增幅 ≤10%、每 3–4 週減量至 70–80%、Peak 期最長 LSD 30–35K／3–3.5 小時上限
  - **待補研究方向**（grill 時決定取捨，非全部都要做）：
    - 訓練負荷／恢復：ACWR（acute:chronic workload ratio）合理區間、TSB 判讀門檻、HRV 用於訓練決策的實證強度與已知限制
    - 訓練品質判讀：心率漂移（cardiac drift／Pw:HR decoupling）的閾值定義、配速執行度容許誤差、環境因素（高溫濕度）校正
    - 課表遵從度：偏離多少才算「沒照課表跑」、連續偏離如何判定需要調整課表
  - **原則**：⚠️ 有爭議或證據強度不足的指標（如 HRV 單日變化）須明確標記信賴度，不可與高共識規則（如 10% 進量）等同對待；規則引擎輸出應附「依據來源」欄位供 AI 教練解釋原因時引用
  - **產出形式**：研究結論寫入 PLAN.md 成為 Phase 3 規格依據（比照 §6 全馬課表模板的寫法），不散落在程式碼註解
- [ ] 定義「訓練品質評估」的分析維度（⚠️ 承 N-4：**遵從度已自品質分數拆出**）：品質分數＝**單場生理三維度**（配速執行度、心率漂移〔需 1E 的 FIT 逐秒資料，摘要資料做不到〕、恢復是否充足）；**遵從度改為獨立的週期性行為指標，不併入品質分數**（否則沒空練造成的低遵從度會拉低單場其實練得很好的評分）。所有輸出須附信賴度標記，`workout_type` 為 unknown/NULL 時不評類型相關維度——閾值依 N-3 的科學依據訂定，實作為 `services/` 純函式（比照 `readiness` 輸出「分數／旗標＋觸發原因」）後再包成 MCP tool，**不由 AI model 臨場判讀**
- [ ] 對話式教練互動：查詢近況、解釋建議原因、動態微調課表
  - 定位：AI 教練負責「拿規則引擎的旗標做決策與溝通」，不負責「產生旗標」（延續 PLAN.md §6.7 恢復判斷模組只輸出狀態、不自動改課表的原則）
  - 人設以**行為約束**為主而非風格描述：每項建議須引用具體數據來源、無資料時明說不臆測（呼應 PLAN.md §4 fallback 期間須誠實反映降級）、涉及傷病症狀不給醫療建議
  - ⚠️ 本階段**只做行為約束層**；可自訂的**風格層**（語氣/口吻/幽默感）已預埋設計於 [PLAN.md §7.6](PLAN.md#76-人設的兩層結構行為約束層與風格層-風格層已擱置待未來有空檔再研究)，列為 Phase 4 的 **N-5**，不在本階段實作。實作行為約束層時**須預留風格層的擴充點**（system prompt 組裝方式應能之後插入風格區塊，而非把約束與語氣混寫成一段）
- [ ] **N-1**：新學員 onboarding 流程須詢問希望的稱呼，並在後續對話一致使用
- [ ] 建立評估回饋迴圈：AI 建議 → 實際執行 → 下次分析納入偏差比對

## Phase 4 — 之後可做（非 MVP）
- [ ] **⏸️ N-5 可自訂的教練回答風格層（已擱置，構想與問題分析見 [PLAN.md §7.6](PLAN.md#76-人設的兩層結構行為約束層與風格層-風格層已擱置待未來有空檔再研究)）**：讓 AI 教練回答不枯燥乏味，構想為「專業使用」與「輕鬆使用」兩條路
  - **狀態（2026-08-21）**：想法與初步問題分析已整理歸檔，**目前擱置**，待未來有空檔再進行深度研究與開發
  - ⚠️ **啟動前須先解決的兩個問題**（不可依現有內容直接實作）：(1)「不可違背專業度」此約束無法直接執行，需換成可驗證形式；(2) 風格中立性假設在**壞消息**上會破——「輕鬆」的本質有一部分是降低壞消息衝擊，與完整傳達風險直接衝突
  - **待研究解法**：嚴重度熔斷機制——規則引擎旗標達門檻時風格層自動停用，即「風格自由度與訊息嚴重度成反比」；優點是可寫測試驗證、界線畫在資料而非語感上
  - **仍未解爭點**：嚴重度門檻由誰訂（回到 N-3）、兩條路是同一使用者可切換還是給不同客群（若「輕鬆」給新手，而新手最需要清楚警訊，此張力須處理）、熔斷為二元或漸進
  - **前置條件**：N-4／N-3／訓練品質評估已定案並實作完成（判斷層穩定後才調風格，否則無法分辨「回答變好」是風格改善還是判斷改善）
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
- **N-3 訓練科學依據基準**：規則引擎的判讀閾值須參考科學化訓練與運動生理學界的權威依據，並標註出處，而非由 AI model 即興判斷或憑感覺寫死數字。屬 Phase 3 規則層前置工作，已加入 Phase 3 TODO，**待 grill 深度討論後定案研究範圍與取捨**。
- **✅ N-6 訓練方法論的選擇（2026-08-25 grill 定案）**：原問題為「決定單押一派、多派融合、或依情境切換」，grill 中發現此框架問錯層級——四維度中只有課表結構有流派分歧。**定案為分層選用**：量的曲線與週頻率用 Higdon Novice 1、比例規則用 Daniels、明確排除 Hansons（准入門檻未達，cumulative fatigue 在週 15K 下不成立）。一手查證見 [docs/research/training-methodologies.md](../research/training-methodologies.md)，結論見 [PLAN.md §5](PLAN.md#5-訓練方法論的選擇-2026-08-25-grill-定案)。重啟條件：週跑量穩定 ≥45K 且週練 ≥5 次持續 8 週。
- **✅ N-4 事實層與判斷層分工比重（2026-08-25 grill 定案）**：§7.4 第 3 點（錯誤事實被包裝成帶依據的「事實」）**成立且已有實際案例**（`acwr` 語意錯誤），但結論非推翻分層——改看原始數據不更安全。**定案為事實層輸出契約補上第三欄位**：`{數值, 依據來源, 信賴度/已知偏誤}`，信賴度必填，無法標記就回傳 `None`。結論見 [PLAN.md §7](PLAN.md#7-事實層與判斷層的分工phase-3-規格依據-2026-08-25-grill-定案)。界線判準與軟性輸入亦已定案（見 §7.5）：閾值輸出數值＋門檻＋適用性由 AI 定性；軟性輸入半落地為 `context_notes`（自由文字＋日期範圍），永不進事實層。
- **⏸️ N-5 可自訂的教練回答風格層**：只有行為約束的教練雖可信但回答易枯燥乏味，構想為「專業使用」／「輕鬆使用」兩條路，在行為約束層優先的前提下加入語氣變化。**2026-08-21 討論後擱置**——已識別兩個未解問題（「不可違背專業度」無法執行、風格中立性假設在壞消息上會破），待未來有空檔再深度研究。構想與問題分析見 [PLAN.md §7.6](PLAN.md#76-人設的兩層結構行為約束層與風格層-風格層已擱置待未來有空檔再研究)，已列入 Phase 4 TODO；Phase 3 實作行為約束層時仍須預留其擴充點。
