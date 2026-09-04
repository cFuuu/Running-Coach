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
| **Phase 1.5 Dashboard** | 🔵 **進行中** | Task A/B/C 已完成並經瀏覽器實測；改版 Phase 1~5（深色模式/心率區間修正/tooltip/自訂區間/指標自訂）已完成；**Phase 6 行事曆檢視未開始**；2026-09-02 發現並當日修正 **`hr_zones` 讀取端單位錯誤**（API 同步的 7 場心率區間值被縮小 1000 倍，同時影響 MCP 與前端圖表；改以 `source_version` 判定單位，510 項測試通過）|
| **Phase 1B 即時同步** | ✅ **主體完成**（2026-09-01）| 1B-0～1B-5、1B-7、1B-8 完成並對真實資料庫實測：資料由 2026-08-16 推進至 **2026-09-01**；重複同步筆數不變（冪等成立）。認證與執行已解耦（`garmin_auth` 只讀 token）、MCP 新增唯一寫入型 tool `sync_garmin_data`。**1B-7 斷點自動補齊**——`days` 省略時依資料庫回推（斷點取兩表較舊者＋3 天重疊，上限 90 天）並預告耗時，解決固定天數的靜默資料遺漏。**1B-8 補抓分圈**——`get_activity_splits` 可取得完整逐圈明細（不需下載 FIT），同步進來的活動已能套用既有分類器補上 `workout_type`；同時修正 `external_id` 去重（原 UNIQUE 因浮點距離精度差擋不住跨來源重複）。⚠️ 逐秒資料仍需 FIT，故心率漂移／配速穩定度對同步活動仍不可評。剩 1B-6 備援層（Strava）未做。**1B-9 指標擴充：✅ 完成（2026-09-02）**——審計發現大量指標被 ETL 丟棄，schema／migration／正規化層／兩張新表 upsert 皆已完成，三個現存資料正確性缺陷已修（stress_avg 來源、acwr 語意、readiness 快照選取）。**本機對真實帳號驗證抓出雲端實作的五處巢狀路徑與單位錯誤並修正**（`a07bbf6`，詳見 1B-9 段落表格），兩張新表為零 NULL。歷史資料已回填（`74c4686`，504 筆活動、17 個活動類指標的 coverage）。505 項測試全過|
| **Phase 2 規則引擎** | 🔵 **進行中** | VDOT／週期化排程器／訓練負荷（ATL/CTL/TSB）／恢復判斷邏輯／資料庫整合層皆已完成；已對 Fu 本人成功產生並寫入 16 週課表（111 天，VDOT 42.91）；N-2 外部課表實際匯入機制待 P-1（等有真實跟團格式）；`max_hr_bpm=198`（手錶顯示值，日後可用實測值校正，非阻塞） |
| **Phase 3 AI Coach** | 🔵 **進行中** | ★ 專案主目標。MCP server 已完成，7 個唯讀 tools 已實測成功（含修復一個 SQLite 跨執行緒 bug）；`generate_training_plan` 寫入型 tool 待測；**N-6（訓練方法論）與 N-4（事實層/判斷層分工）已於 2026-08-25 grill 定案**（見 [PLAN.md §5](PLAN.md#5-訓練方法論的選擇-2026-08-25-grill-定案)／[§7](PLAN.md#7-事實層與判斷層的分工phase-3-規格依據-2026-08-25-grill-定案)）；**下一步為 N-3 科學依據**（範圍依 N-6 拆為兩批：課表結構出處 → Higdon/Daniels；生理閾值出處 → 運動生理文獻，後者與流派無關可先做），訓練品質評估（2026-09-01）與**行為約束層/教練人設**已完成——**2026-09-02 對 Fu 真實資料實跑三情境，五條行為約束逐條驗收通過**（含約束 1 實際攔截到一個錯誤數字，見 Phase 1.5 的 `hr_zones` bug），僅剩 `/coach` slash command 注入路徑待使用者親自走一次。**下一個大缺口為「課表調整」（N-7）**：主目標明列「訓練建議與**課表調整**」，但目前唯一寫入型 tool `generate_training_plan` 是整份重產，無單日／單週調整粒度，情境 C 驗收時暴露 |

**環境**：conda `rc`（Python 3.12），套件見 `requirements.txt`／`environment.yml`。測試 **510 項**全通過。
**執行測試**：`"C:/Users/cFu/anaconda3/envs/rc/python.exe" -m unittest discover -s src/test/unit -p "test_*.py"`

**目前資料庫內容**（`output/running_coach.db`，不進版控，2026-09-01 同步後）

| 資料表 | 筆數 | 涵蓋範圍 |
|---|---|---|
| `activities` | 529 | 2019-12 ~ **2026-09-01**（近 7 年）；1B-9 回填後含跑姿／功率／溫度／心率區間 |
| `daily_wellness` | 1668 | 睡眠/步數/全天壓力近 6 年；HRV/SpO2 約 10 個月；最新至 **2026-09-01** |
| `activity_laps` | 2438 | **248 場**跑步的每公里分圈（1B-8 後含 API 同步場次）|
| `activity_records` | 68884 | 逐秒降頻（每 10 秒）——**僅 FIT 來源**，API 同步無此資料 |
| `metric_coverage` | 43 | 各指標實際可用起訖日（1B-9 後含 17 個活動類指標）|
| `training_plan` | 246 | Phase 2 已產生（含版本歷史，`is_active` 區分）|
| `training_status` | 6 | 1B-9 新增：真 ACWR／急慢性負荷／熱適應，僅當日值無歷史回溯，故自 2026-08-28 起累積 |
| `performance_metrics` | 1 | 1B-9 新增：賽事預測／乳酸閾值／FTP／耐力分數／體能年齡／爬坡分數，每次同步一筆快照 |

✅ **已可增量同步**（1B 完成）。日常更新直接跑，會自動回推到上次斷點：
```bash
"C:/Users/cFu/anaconda3/envs/rc/python.exe" -m src.main.python.services.garmin_sync_runner --db-path output/running_coach.db
```
同步會一併抓逐圈明細並套用既有分類器補 `workout_type`（1B-8）。
⚠️ 但**逐秒資料仍需 FIT**，故同步進來的活動在 `assess_session_quality` 中
心率漂移與配速穩定度兩維度不可評（強度符合度可評）。

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
>
> ⚠️ **後續修正（2026-09-01）**：上段「永久封鎖」的判斷**已不成立**。該封鎖為 Cloudflare 的
> **TLS 指紋辨識**（非 JS challenge），`python-garminconnect` 已於事後改用 `curl_cffi`
> 模擬官方 Android app 的 TLS 指紋重寫登入流程，純 HTTP 路線因此復活。詳見下方 1B 段落。
> 此處保留原文作為當時判斷的紀錄，**結論以 1B 為準**。

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

**1B. 持續同步（Garmin 增量匯入器）——🔵 可以開始（2026-09-01 前提已改變）**

> **定位：這是「匯入器」，不是查詢工具。** 目標是讓 `output/running_coach.db`
> 能增量更新到今天，資料進 DB 後，既有 8 個 MCP tools 自動就查得到最新資料。
> **不新增任何直接查 Garmin API 的查詢型 tool**（理由見 [PLAN.md §1](PLAN.md#1-決策記錄decisions-log)
> 「Garmin 即時同步定位為匯入器」——會造成雙重真相、繞過信賴度標記、Phase 2 讀不到）。
>
> **前提變更（2026-09-01 實測查證）**：原記「Garmin 已封鎖自動化登入」**已過時**。
> `garth` 確實於 2026-03-28 deprecated（新登入完全失效），但 `python-garminconnect`
> 已改用 `curl_cffi` 模擬官方 Android app 的 TLS 指紋重寫登入，純 HTTP 路線復活。
> 已驗證：PyPI `garminconnect` 最新為 **0.3.11**（requires-python ≥3.12，符合 conda `rc` 環境），
> 依賴 `curl_cffi>=0.15.0`（最新 0.16.2）、`requests>=2.33.0`、`ua-generator>=1.0`，
> **已完全移除 garth**。故 `requirements.txt` **不應**加入 `garth`。

**1B-0. 可行性驗證——✅ 已完成（2026-09-01，實測通過）**
- [x] 在 conda `rc` 安裝 `garminconnect 0.3.11` + `curl_cffi 0.16.2`（已裝）
- [x] 實際登入 Fu 的 Garmin 帳號成功——**2026 的 Cloudflare 封鎖對本帳號不生效**
- [x] token 正確存入 `~/.garminconnect/garmin_tokens.json`，後續可純 token 認證
- [x] 抓一場**已在 DB 內**的活動與 DB 現值逐欄比對——**四欄全數一致**
- [x] 黑盒衍生值皆可取得：`get_training_readiness`／`get_hrv_data`／`get_sleep_data`

> **✅ 比對結果（活動 23997807612，2026-08-16）**
> | 欄位 | DB | API | 判定 |
> |---|---|---|---|
> | `distance_km` | 10.1586 | 10.15857 | ✅ 一致 |
> | `duration_sec` | 3794 | 3794.447 | ✅ 一致 |
> | `avg_hr_bpm` | 150 | 150.0 | ✅ 一致 |
> | `max_hr_bpm` | 161 | 161.0 | ✅ 一致 |
>
> 差異僅在小數位（DB 存四捨五入值），**無 100/1000 倍的單位落差**。

> **🔑 三項影響實作的實測結論（1B-2／1B-3 必讀）**
>
> **① API 單位是標準公尺／秒，與匯出包不同** ——`distance=10158.57`（公尺）
> 對應 10.16km。匯出包用的是 `CENTIMETER`／`MILLISECOND`／
> `CENTIMETERS_PER_MILLISECOND`。故 1B-3 正規化層**不可重用**
> `garmin_export_parser.py` 的換算係數，但要寫的換算比匯出包更單純。
>
> **② 取活動摘要要用 `get_activities_by_date()`，不是 `get_activity()`**
> ——實測 `get_activity(id)` 只回 13 欄且 `distance`／`duration`／`averageHR`
> 全為 `None`；`get_activities_by_date(day, day)` 回 **111 欄**且填滿。
>
> **③ 增量同步的資料確實存在** ——DB 最新為 2026-08-16，API 可見
> 2026-08-27（5.1km）與 2026-08-30（15.1km）兩場跑步。此二筆即為
> 1B-5 端到端驗證的現成素材。

> **⚠️ 實測佐證：`CLAUDE.md` §🔐 第 2 條「執行階段嚴禁帳密登入」是必要的**
> 首次以帳密登入時，`garminconnect` 會依序嘗試多種策略
> （`mobile+cffi` → TLS `WRONG_VERSION_NUMBER` 失敗 → `mobile+requests`
> → **429 IP rate limited**  → 再換策略才成功），等於**一次登入連打數次 SSO**。
> 改為只讀既有 token 後重跑，**零登入請求、零錯誤**。
> 這條原本是推論，現已有實測依據——執行階段若允許帳密自動重登，
> 失敗重試會快速累積 429，把帳號推向被鎖。

> **已驗證的 API 介面（2026-09-01 實際 probe 0.3.11，非文件推測）**
> - `Garmin(email, password, prompt_mfa=<callable>, return_on_mfa=False, verify_login=True, retry_attempts=3)`
> - `client.login(tokenstore: str | None = None) -> tuple[str | None, str | None]`
>   ——回傳 `(needs_mfa, None)` 表示需要 MFA，`(None, None)` 表示登入成功
> - `client.resume_login(client_state: dict, mfa_code: str)`——MFA 二階段完成用
> - `client.logout(tokenstore=None)`——清除本機 token（不會撤銷已發出的 token）
> - token 預設存 `~/.garminconnect/garmin_tokens.json`，環境變數 `GARMINTOKENS` 可覆寫；
>   **會自動 refresh**，refresh token 未過期前不需重新輸入帳密
> - ⚠️ **`client.garth.dump()` 不存在**——0.3.11 已無 garth，`Garmin` 物件無 `garth` 屬性。
>   token 持久化由 `login(tokenstore)` 自行處理，不需（也不能）手動 dump
> - 取資料：`get_activities(start, limit)`／`get_activities_by_date(startdate, enddate, activitytype)`／
>   `get_activity(id)`／`get_activity_splits(id)`／`get_activity_hr_in_timezones(id)`／
>   `get_hrv_data(cdate)`／`get_hrv_data_range(start, end)`／`get_sleep_daily(start, end)`／
>   `get_rhr_daily(start, end)`／`get_all_day_stress(cdate)`／`get_training_readiness(cdate)`／
>   `get_body_battery(startdate, enddate)`／`get_max_metrics(cdate)`／`get_stats(cdate)`
> - `download_activity(id, dl_fmt=ActivityDownloadFormat.ORIGINAL)` 可取原始檔（zip 內含 FIT），
>   格式列舉為 `ORIGINAL/TCX/GPX/KML/CSV`——**無 `FIT` 成員，要用 `ORIGINAL`**。
>   這條路可直接餵給既有 `fit_parser.py`，取得分圈與逐秒資料

**1B-1. 認證（一次性 CLI，與執行階段解耦）——✅ 已完成（2026-09-01）**
- [x] [`services/garmin_auth.py`](../../src/main/python/services/garmin_auth.py)：
      `login`／`status`／`logout` 三個子指令，`getpass` 取密碼（不回顯）、支援 MFA
- [x] token 存 `~/.garminconnect/`（**專案目錄外**）；路徑不 hardcode，
      支援 `--tokenstore` 參數與 `GARMINTOKENS` 環境變數
- [x] ⚠️ `load_client()` 為執行階段唯一入口，**只讀 token**：`Garmin()` 以零參數
      建構、token 失效時拋 `GarminAuthRequired` 要求人工重跑，**不自動用帳密重登**
- [x] 原始例外訊息不外流（可能含 request 細節／token），只保留例外類型名稱
- [x] email 遮蔽（`s***@example.com`），不印 token／cookie／userProfileId
- [x] 單元測試 10 項（合成假資料，無真實憑證、不發網路請求），全數通過
- [x] `status` 已對真實 token 實測：✅ 已認證、token 可用

**1D. 憑證與機密管理——✅ 已完成（2026-09-01）**
- [x] `.gitignore` 補上 `.env`、`.env.*`（`!.env.example` 例外）、`.garminconnect/`、
      `*.token`、`session.json`、`*.session.json`——已用 `git check-ignore` 逐項驗證
- [x] `.env.example` 範本（只放 key 不放 value，已驗證**不**被 ignore、可進版控）
- [x] 「每位使用者自帶憑證」設計：token 路徑可由環境變數覆寫，無任何硬編碼憑證
- [x] setup 說明：寫在 `garmin_auth.py` 模組 docstring 與 `.env.example` 註解
- [x] ⚠️ Windows 平台 `chmod 0700` 失效一事已在 `status` 輸出明示，
      程式碼**不宣稱**已限縮權限

**1B-2. API client 封裝——✅ 已完成（2026-09-01）**
- [x] [`services/garmin_api_client.py`](../../src/main/python/services/garmin_api_client.py)：
      只負責認證載入 + 抓 raw JSON，不做正規化（分層比照 `fit_parser`）
- [x] 錯誤處理：`GarminFetchError`／`GarminRateLimited` 分類，429 單獨成型別
- [x] ⚠️ 例外訊息只保留型別名稱，**不帶原始訊息**（可能含 token／request 細節）
- [x] rate limit 自我節制：預設每次呼叫間隔 1 秒、單次區間上限 365 天
- [x] 各日 wellness 獨立容錯（某日缺資料不影響其他日），但 429 一律往外拋停止整批

**1B-3. 正規化與去重——✅ 已完成（2026-09-01）**
- [x] [`services/garmin_sync_runner.py`](../../src/main/python/services/garmin_sync_runner.py)：
      正規化 → 去重 → 寫入 DB，附 CLI 進入點
- [x] ⚠️ **已實測確認 API 單位與匯出包不同**，故自行換算、**未**重用
      `garmin_export_parser.py` 的係數：

      | 欄位 | 匯出包 | REST API |
      |---|---|---|
      | `distance` | 公分 | **公尺** |
      | `duration` | 毫秒 | **秒** |
      | `elevationGain` | 公分 | **公尺** |
      | `calories` | 千焦耳 | **大卡** |
      | `maxSpeed` | 公分/毫秒 | **公尺/秒** |

- [x] 去重：**不自行實作指紋比對**——`activities` 已有
      `UNIQUE (athlete_id, started_at, activity_type, distance_km)`、
      `daily_wellness` 有 `UNIQUE (athlete_id, date, source)`，
      沿用 `INSERT ... ON CONFLICT DO UPDATE` 由資料庫保證冪等
- [x] `source` 沿用 `'garmin_export'`（**非** `garmin_mcp`）：`daily_wellness` 的
      UNIQUE 含 `source`，改用新值會讓同一天出現兩列，而現有查詢皆未依 source
      篩選、`readiness.py` 更以日期為 dict key，兩列會非確定性互相覆蓋。
      來源可追溯性改由 `source_version='api_sync'` 承載
- [x] wellness 的 upsert 用 `COALESCE`，新資料缺值時不覆寫既有值
- [x] ⚠️ ON CONFLICT 刻意不更新 `workout_type`／`workout_type_source`，
      保護人工標註不被同步覆蓋
- [x] **同步後重算 `metric_coverage`**——實測發現若不做，wellness 已寫到 09-01
      但 coverage 仍停在 08-16，`get_meta()` 會回報過時的可用範圍

> **⚠️ 已知限制：同步進來的活動 `workout_type` 為 NULL**
> API 的活動摘要**不含分圈資料**，而 `classify_workout()` 需要分圈才能判斷
> 配速結構。這是**刻意不猜**——依 PLAN.md §1「`workout_type` 為 unknown/NULL
> 時不輸出類型相關判讀」，缺前提時正確行為是留空讓下游拒絕判讀。
> 補齊方式（後續工作）：①用 `download_activity(id, ORIGINAL)` 取 FIT 後走
> 既有 `fit_import_runner`（可同時補分圈與逐秒，是完整解）②人工標註。

> **🐛 實測抓到的邊界 bug：配速的進位邊界**
> 活動 23997807612 的真實配速為 **373.5 s/km**，正好落在進位邊界：
> 用 API 原始浮點秒（3794.447）得 **374**，用四捨五入後的整數秒（3794）得
> **373**——而 DB 既有值為 373。若不對齊 `garmin_export_parser` 的做法
> （分子用整數秒），**每次重新同步都會讓配速在 ±1 秒間跳動**，產生假的
> 資料變動。已修正並加迴歸測試守住。

**1B-4. MCP 整合——✅ 已完成（2026-09-01）**
- [x] `sync_garmin_data(days: int = 7) -> dict` 已註冊（回傳 dict，與既有慣例一致）
- [x] 標為寫入型，docstring 明示副作用、冪等性、未認證與 429 的處置
- [x] ⚠️ docstring 明示同步進來的活動 `workout_type` 為 NULL 屬正確行為
- [x] 回傳新增筆數與日期區間，**不含任何憑證資訊**
- [x] MCP server 現有 10 個 tool（8 唯讀 + 2 寫入），已驗證註冊成功
- [x] ❌ 確認**未**新增任何直接查 Garmin API 的查詢型 tool

**1B-3b. Garmin 黑盒衍生值：照抓照存，但只當對照組**

> **決策依據**：[PLAN.md §1](PLAN.md#1-決策記錄decisions-log)「Garmin 衍生值為對照組／校準來源，
> 不進事實層」（2026-08-25 grill）。Training Readiness／Body Battery／HRV status／
> `recovery_time_hours` 等為 Firstbeat 黑盒演算法產物，公式未公開、無法標出處。
>
> ⚠️ **`unusable` 標記的意思是「不得作為判讀依據」，不是「不得存在」。** 這些資料
> **要抓、要存**，只是取用時必須帶 `garmin_blackbox` 標記，不可當結論的依據。

- [ ] 同步時一併抓取並寫入 `daily_wellness`（既有 schema 已有欄位，不需改 schema）：
      `get_training_readiness(cdate)`／`get_body_battery(start, end)`／
      `get_hrv_data_range(start, end)` 的 status 欄位／`recovery_time_hours`
- [ ] ⚠️ 取用時一律走既有 `data_confidence.KNOWN_BIASES['garmin_blackbox']`（`unusable`），
      **不得另開一條無標記的路直送 AI**
- [ ] 明確區分三種**允許**的用途（皆為對照，非判讀）：
  - **校準自建門檻**：TSB 個人 P10、HRV 7 日均值等自訂門檻，用 Garmin 長序列對照，
    找出兩邊分歧的日子作為檢查自建模型的線索
  - **補資料最薄處**：Garmin 恢復指標覆蓋 1086 天（2023-08 起），為自建 HRV 序列
    292 天的 **3.7 倍**，可作偏移對照，判斷該 292 天是否落在異常期
  - **背離本身即訊號**：自建說 normal 而 Garmin 說 low 時，此分歧值得對話層點出
    （可能源自重訓計法差異——自建把重訓併入同一累加池，Garmin 基於 EPOC 計法不同）
- [ ] ❌ **禁止**：把 Garmin 分數當結論依據（「你今天 readiness 32，建議休息」）、
      混進自建公式當輸入、或用它取代自建判斷
- [ ] ⚠️ **不做自動背離偵測**——「兩邊不一致代表什麼」屬判斷層職責，規則層只提供
      兩組數值與各自標記，不代為判定誰對誰錯（呼應 §1「規則層永不自動改寫課表」的同一形狀）

**1B-5. 端到端驗證——✅ 已完成（2026-09-01，對真實資料庫實測）**
- [x] 記錄基準：activities **520**、daily_wellness **1651**，兩者皆止於 2026-08-16
- [x] 執行同步（`--days 20`）→ activities **530**（+10）、wellness **1667**（+16），
      資料推進至 **2026-09-01**；新增 7 場跑步、2 場重訓、2 場健走
- [x] **再執行兩次同步 → 筆數維持 530／1667，零重複列**（冪等性成立）
- [x] 回歸檢查：既有活動 23997807612 的距離／時長／HR／配速**完全未變動**
- [x] 確認既有唯讀 tools 查得到新資料——`list_running_sessions(30d)` 回 13 場
      （含 8/30 的 15.11km、8/27 的 5.11km）
- [x] `metric_coverage` 經修正後正確推進至 2026-09-01
- [x] 單元測試 **29 項**（合成假資料，無真實憑證、不發網路請求）
- [x] 同步前已備份資料庫至 scratchpad（未進版控）

**1B-7. 自動補齊斷點＋耗時預告——✅ 已完成（2026-09-01）**

> 起因：固定 `--days 7` 在長期未同步時會漏掉中間的缺口——程式不看資料庫
> 現況，只認傳入的天數。使用者要求「避免資料遺漏」，並要在同步前告知預計耗時。

- [x] `resolve_sync_days()`：依資料庫回推需同步天數，`--days` 省略時為預設行為
- [x] **斷點取 activities／daily_wellness 兩表較舊者**——兩表天生不同步
      （沒運動的日子沒有活動列）；取較新的 wellness 日期會讓當天稍晚上傳的
      活動永遠落在下次區間外，形成永久缺口
- [x] **往前多抓 `OVERLAP_DAYS=3` 天**：Garmin 的睡眠分數／HRV／Training
      Readiness 常隔日才算出，當天抓到的列欄位不全需重抓；upsert 的
      COALESCE 只補空值不覆寫，故重抓無副作用
- [x] **`MAX_AUTO_SYNC_DAYS=90` 上限**：wellness 逐日各打 4 個端點，天數與
      請求數成正比。90 天＝361 次請求／約 6 分鐘，可涵蓋一個完整馬拉松
      訓練週期；`garmin_api_client` 的硬上限 365 天＝**1461 次請求／約 24 分鐘**
      連續請求，已超出正常使用者行為樣態，有觸發 Cloudflare 風控的風險
- [x] 夾擠時回傳 `gap_truncated=True`，CLI／MCP 據此提示改走官方匯出包
- [x] `estimate_sync_cost()`：回傳請求數與預估秒數，CLI 於同步前顯示並詢問
      確認（`--yes` 略過、`--dry-run` 只看計畫不打 API）
- [x] MCP `sync_garmin_data` 新增 `dry_run` 參數、`days` 改選填；docstring
      指示 AI 先取 `estimate` 告知使用者再實際執行（MCP 無互動能力）
- [x] 單元測試 **+16 項**（gap 計算／上限夾擠／重疊／空庫 fallback／成本估算／
      dry-run 不觸碰 API 與 DB），全套 **415 項通過**
- [x] 真實資料庫實測：dry-run 正確算出 08-28~09-01（5 天）；實跑後筆數維持
      530／1667（冪等成立）；模擬 40 天未同步得 45 天、模擬一年得 90 天且
      正確標記 `gap_truncated`

**1B-8. 補抓分圈補齊分類＋修正跨來源重複列——✅ 已完成（2026-09-01）**

> 起因：同步進來的活動 `workout_type` 全為 NULL，導致近兩週訓練在
> `assess_session_quality` 全部不可評——1B-7 讓資料不再遺漏，卻也讓這個
> 缺口穩定累積。查證時另發現一個資料重複的 bug，兩者一併處理。

**🔑 關鍵發現：分圈明細拿得到，先前「需下載 FIT」的判斷不正確**

- 活動摘要端點（`get_activities_by_date`）確實沒有逐圈資料，但它有的
  `splitSummaries` 是**按 `RWD_RUN`／`RWD_WALK`／`RWD_STAND` 類型彙總**的
  總和與平均（`noOfSplits` 只說該類型有幾段），`fastestSplit_1000` 之類
  也只是單一極值——都不構成逐圈序列。摘要的 `lapCount` 告知圈數存在
- **`get_activity_splits(id)` 直接回傳完整 `lapDTOs`**。實測 2026-08-30
  那場 `lapCount=16`，本端點回 16 圈齊全（距離／時長／avgHR／maxHR），
  餵既有 `classify_workout()` 得 `lsd`（正確：15.1km 為近期均值 1.5 倍以上）
- 成本與下載 FIT 相同（皆每場 1 次請求），但**不需下載檔案、不需解析 FIT**

- [x] `garmin_api_client.fetch_activity_splits()`：取逐圈明細
- [x] `garmin_sync_runner.normalize_laps()` + `_sync_splits()`：正規化 → 寫入
      `activity_laps` → 套用分類器
- [x] **重用** `fit_import_runner.store_laps()`／`update_workout_type()`
      （兩者改為公開函式）——兩條匯入路徑必須用**同一套分類標準**，
      否則同一個 `workout_type` 欄位會混入兩種語意
- [x] 只處理 `DEFAULT_CLASSIFIABLE_SPORTS`（重訓/健走不分類）；已有分圈者
      跳過不重抓（省 API，也讓重複同步真正冪等）；單場失敗不中斷整批，
      但 429 往外拋中止
- [x] `estimate_sync_cost()` 加計分圈請求數，耗時預告不再低估

> **⚠️ 明確不採「以心率區間推算課種」**
> 摘要帶有 `hrTimeInZone_1..5`、`hasIntensityIntervals`、`trainingEffectLabel`，
> 看似可用，但**心率區間分布把時間軸攤平了**——10×400m 間歇與穩定節奏跑的
> 區間分布可以幾乎相同（間歇的休息段把平均拉回來），且心率有滯後。用它反推
> 配速結構正是分類器設計要避開的錯誤。這些欄位適合當**分類結果的交叉驗證**
> （分類器說 interval 但 `hasIntensityIntervals=False` 就值得懷疑），
> 不適合當主要依據；另建一套也會讓同一欄位混入兩種語意。

**🐛 同時修正：跨來源重複列（實測發現）**

- 原 UNIQUE 為 `(athlete_id, started_at, activity_type, distance_km)`，但
  API 回傳完整浮點距離（10.1585703125）、匯出包存四捨五入值（10.1586），
  **兩者不相等，UNIQUE 擋不住**，同一場活動寫成兩列。實測有 2 組
  （8/13、8/16），正落在 API 與匯出包的來源重疊區
- 後果：`list_running_sessions` 把同一場跑步列兩次，一次有 `workout_type`
  一次是 NULL——AI 教練看到的場次數量是錯的
- [x] 改以 **`external_id`**（Garmin activityId）為去重鍵。實測 530 筆活動
      `external_id` 全部非 NULL，且兩組重複列的 `external_id` 相同
- [x] `schema.sql` 新增 `idx_activities_external_id` 唯一索引，原 UNIQUE 保留
      為次要防線並註明其不足
- [x] [`models/migrate_dedup_external_id.py`](../../src/main/python/models/migrate_dedup_external_id.py)：
      一次性 migration，預設 dry-run、`--apply` 前自動備份、單一 transaction、
      建索引前重驗；保留有分類的那列（匯出包來源，資料較完整）

**實測結果**

| 項目 | 變化 |
|---|---|
| 活動總數 | 530 → **528**（刪 2 筆重複，皆無附帶 laps/records，零損失）|
| `activity_laps` | 2381 → **2438**（+57 圈）|
| 跑步缺分類 | 27 → **22**（剩下為更早期無 FIT 的舊資料）|
| 近兩週訓練 | 全 NULL → **全部有分類**（8/18 tempo、8/22 easy、8/26 tempo、8/27 easy、8/30 lsd）|

- [x] 實際呼叫 `assess_session_quality(1042)` 驗證：8/30 那場評出強度符合度
      `within`（66.2% HRR 落在 lsd 預期 55–75%），並正確帶出兩項 low 信賴度標記
- [x] 單元測試 **+17 項**，全套 **432 項**通過

> **⚠️ 仍未解決：逐秒資料**
> `activity_records` 仍需 FIT，本路線不提供，故同步進來的活動**心率漂移與
> 配速穩定度兩個維度仍不可評**（實測 1042 的 `not_assessed` 確認）。
> 若日後要補，走 `download_activity(id, ORIGINAL)` → `fit_import_runner`
> （1B-0 已確認格式列舉用 `ORIGINAL`，無 `FIT` 成員）。優先度不高——
> 心率漂移目前本就無可用閾值（見 N-3 第一批），補了也只是多一個不判讀的數值。

**1B-9. Garmin 指標擴充（審計已完成，實作進行中——2026-09-02）**

> 起因：ETL 過程中大量 Garmin 指標被過濾或丟棄。完整審計見
> [GARMIN_METRICS_AUDIT.md](GARMIN_METRICS_AUDIT.md)。
> **決策（2026-09-02）**：方案 A′——P0+P1 全部入庫，分析邏輯後續分階段加入。
> 理由：入庫與分析的時程本來就不同，`get_training_status` 等端點只回傳當日值、
> 無歷史回溯，今天不抓就永遠沒有；而跑姿類指標雖需數月才累積出可判讀的個人
> 基準，資料本身已躺在 `raw_data_json` 裡，順手一起接的邊際成本趨近於零。

**🔑 三個層次的落差（依成本效益排序）**

1. **已抓到但丟棄**（零 API 成本）：`acuteLoad`、HRV baseline 三值、跑步動態
   全套已在現有回傳裡，正規化層直接沒接
2. **端點從未呼叫**：`get_training_status` 一支同時帶回真 ACWR（急慢性負荷比）、
   急慢性負荷、最佳範圍、VO2Max 精確值、熱適應。每日僅 +1 次請求
3. **三個現存資料正確性缺陷**（見下方，其中缺陷 A 會產生「有值但語意錯誤」
   的欄位，比缺值更危險）

**🐛 三個現存缺陷（一律修）**

- **缺陷 A**：`stress_avg`（睡眠期間壓力）與 `all_day_stress_avg`（全天壓力）
  被寫入同一個值（皆為 `averageStressLevel`），違反 schema 註解明訂的
  「兩者範疇不同不可互相取代」。實測 2026-08-31：`avgSleepStress`=18、
  `averageStressLevel`=20，DB 兩欄皆為 20。20 筆 api_sync 列全數受影響
- **缺陷 B**：`acwr` 欄位存的是 `acwrFactorPercent`（0–100 貢獻度分數），
  非急慢性負荷比。真值 `dailyAcuteChronicWorkloadRatio` 在 `get_training_status`
- **缺陷 C**：`readiness_list[0]` 為未驗證假設。該端點每日回多筆快照（實測 2–4 筆），
  分數在一天內劇烈變動（2026-08-31：起床 60 分、睡前 75 分）。
  **決策：取起床那筆（`AFTER_WAKEUP_RESET`）**——課表決策要問的是
  「今天能不能練」而非「恢復得如何」

- [x] `schema.sql`：`activities` +14 欄、`daily_wellness` +14 欄、新增
      `training_status` 與 `performance_metrics` 兩表（commit `c0aefb8`）
- [x] `db.py`：`_COLUMN_MIGRATIONS` 補 28 欄，已於真實資料庫副本驗證
      （四表結構正確，528 筆活動與 1667 筆 wellness 完好）

**☁️ 可在雲端完成（不需 token、不碰真實 DB）—— ✅ 已完成（2026-09-02）**

- [x] `garmin_api_client.py`：新增 `fetch_training_status()`、
      `fetch_race_predictions()`、`fetch_lactate_threshold()`、
      `fetch_endurance_score()`、`fetch_fitness_age()`、`fetch_hill_score()`；
      `fetch_daily_wellness_raw()` 納入 training_status；另新增
      `fetch_performance_metrics_raw()` 彙整後五者（快照類端點不進逐日迴圈）
- [x] `garmin_sync_runner.py`：`normalize_activity()` 補 14 欄、
      `normalize_wellness()` 補 14 欄、新增 `normalize_training_status()`
      與 `normalize_performance_metrics()`、兩張新表的 upsert（沿用
      ON CONFLICT + COALESCE 慣例）
- [x] 修正缺陷 A（`stress_avg` 改讀 `sleep.dailySleepDTO.avgSleepStress`）、
      缺陷 C（readiness 快照選取 `AFTER_WAKEUP_RESET`，找不到時退回 `[0]`
      但誠實記錄實際 `inputContext`／`timestampLocal`，不靜默取錯）
- [x] `data_confidence.py`：缺陷 B——撤下 `acwr_not_a_ratio`（無其他呼叫端
      依賴此標記，直接刪除；真 ACWR 依決策為可判讀，存於
      `training_status.acwr_ratio`）
- [x] `_WELLNESS_ENDPOINTS_PER_DAY` 4 → 5；`_WELLNESS_METRIC_COLUMNS` 補新欄位；
      連帶更新 `estimate_sync_cost()` 相關文件數字（90 天：361→451 次請求、
      約 6→7.5 分鐘）與 `mcp/server.py` 的 `sync_garmin_data` docstring
- [x] 單元測試：全部用 fixture，不打 API。新增 56 項
      （`test_garmin_sync_runner.py` +44、新建 `test_garmin_api_client.py` 12），
      含 `training_status` 的 deviceId 主力錶選取、readiness 快照 fallback、
      hrTimeInZone 毫秒換算等迴歸測試；488 項全過
- [x] ⚠️ 修正 `schema.sql` 中 `min_temperature_c`／`max_temperature_c` 的註解
      ——現寫「影響配速判讀」會誤導。依 N-3 查證，該欄位是**手腕體溫污染值**
      （四年間從未低於 21°C，一月台北記錄 max 29°C），不可當氣溫用

**💻 必須在本機完成（需 token 或真實資料）**

- [x] 打真實 API 驗證正規化結果（2026-09-02，commit `a07bbf6`）——
      **本機驗證抓出雲端實作的五處錯誤，見下方**：
  - [x] `avgGradeAdjustedSpeed` 換算合理：8/30 那場 pace 6:14、GAP 6:14，
        台北平地跑校正後幾乎不變，單位正確
  - [x] `training_status` 的 deviceId 挑選正確（Forerunner 965，
        `primaryTrainingDevice=True`）；但**外層路徑錯誤**，見下方
  - [x] `AFTER_WAKEUP_RESET` 6/6 天全數抓到，fallback 未觸發，
        時點落在 07:45–10:30，語意正確——缺陷 C 修正有效
  - [x] 五個端點的真實簽章與巢狀結構已 dump 驗證

**🐛 本機驗證抓出的五處錯誤（雲端 `251deaa` → 本機 `a07bbf6` 修正）**

> 根因：**測試 fixture 與實作用同一套錯誤假設**，488 項測試全過卻一個都
> 沒抓到。修正時一併補上能抓出這類錯誤的迴歸斷言（單位總和關係、
> 合理值域、錯誤層級須取不到值、StubClient 複製 keyword-only 限制）。

| # | 錯誤 | 後果 |
|---|---|---|
| 1 | `latestTrainingStatusData` 實際埋在 `mostRecentTrainingStatus` 下，少讀一層 | `acwr_ratio`／`acute_load`／`chronic_load`／`training_status_code`／`fitness_trend` 全 NULL——**缺陷 B 修正的關鍵欄位反而沒進來** |
| 2 | `heatAltitudeAcclimation` 掛在 `mostRecentVO2Max` 下而非裝置節點 | 熱／高度適應三欄 NULL |
| 3 | `get_lactate_threshold` 為 **keyword-only**，雲端傳位置參數 | `TypeError` 被 `_call()` 轉成 `GarminFetchError` 後遭容錯吞掉，三欄靜默落 NULL |
| 4 | 乳酸閾值回傳為 `speed_and_heart_rate`／`power` 兩個子區塊，非平坦 | 同上 |
| 5 | `hrTimeInZone_N` 單位是**秒**不是毫秒，誤套 `ms_to_sec()` | 5197 秒的 Zone 2 被壓成 5 秒——**有值但語意錯誤，比缺值更危險** |

實測前 `training_status` 24 欄有 12 欄 NULL、`performance_metrics` 3 欄 NULL；
修正後兩表皆**零 NULL**，ACWR 0.7–1.2 落在古典區間，心率區間總和佔活動
時長 98%，乳酸閾值配速 5:58/km @179bpm 與 DB 中 avgHR 153–161 場次的
5:44–6:19/km 相符。

- [x] 對 `output/running_coach.db` 實際執行一次同步，確認端到端正常
      （2026-09-02，6 天：活動 3、wellness 6、training_status 6、
      performance_metrics 1）
- [x] 從既有 `raw_data_json` 回填跑姿／功率／溫度欄位（2026-09-02，
      commit `74c4686`）——實際回填 **504 筆活動**（不限跑步，raw 有值就
      建模），資料回溯至 2020-12。CLI：
      `--backfill-metrics`（加 `--dry-run` 預覽），只填 NULL 不覆蓋既有值，
      可重複執行
  - ⚠️ 回填**必須分來源換算**，兩處單位不同：`avgGradeAdjustedSpeed`
        匯出包是公寸/秒（×10 才是公尺/秒）、`hrTimeInZone_N` 匯出包是
        毫秒而 API 是秒。其餘欄位經逐欄位比對確認兩來源相同
- [x] 回填後確認 `metric_coverage` 正確反映各新指標的可用區間
      （新增 `refresh_activity_metric_coverage()`，17 個活動類指標）——
      各指標深度確實不同：步幅 2020-12、跑步動態 2023-08、心率區間
      2024-12、功率 2022-11，**不可假設一致**

> **⚠️ 為何本機驗證不可省**：這批變更的主要風險是**單位換算**與**巢狀結構
> 解析**，兩者讀程式碼都看不出對錯。缺陷 A 與 B 正是這樣產生的——兩者都
> 通過了 review 才進版控。雲端寫完不等於做完。
> **2026-09-02 補充**：這條原則當天就再次應驗——雲端 488 項測試全過的
> 提交，本機一驗就抓出五處錯誤。**測試通過不等於路徑正確，當 fixture
> 與實作出自同一個推斷時尤然。**

**1B-6. 備援層（維持原規劃，優先度低於上述）**
- [ ] 釐清「token 過期（例行重新認證）」vs「結構性失效（Garmin 改版／上 JS challenge，需切 Strava）」
      的判斷邏輯，避免小問題觸發整條備援鏈
- [ ] 備援：Strava API（帳號已開自動同步）——註冊 API App、client credentials、OAuth refresh
- [ ] ⚠️ 殘留風險：`curl_cffi` 只能過 TLS 指紋檢查，若 Garmin 日後加上 JS challenge／Turnstile
      則此路線失效，屆時才需重新評估 headless 瀏覽器方案

> **1D 憑證與機密管理**已完成，內容併入上方 1B-1 段落（兩者是同一批工作）。

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

**🐛 `hr_zones` 讀取端單位錯誤（2026-09-02 Phase 3 驗收時發現，✅ 當日修正）**

- [x] `get_session_detail` 回傳的心率區間對 **API 同步的活動全部錯誤**（值被縮小 1000 倍）。同一條路徑也餵給 Dashboard 前端的心率區間圖
  - **根因是兩條路徑對同一份 payload 的單位假設相反，1B-9 只修了寫入端**：
    - 寫入端 [`garmin_sync_runner._build_hr_time_in_zone_json()`](../../src/main/python/services/garmin_sync_runner.py)（1B-9 已修）正確地把 **API 的 `hrTimeInZone_N` 當秒**，存進 `activities.hr_time_in_zone_json`。實測 session 1042 該欄位為 `{"1":204,"2":5197,"3":116,"4":0,"5":0}`，總和 5517 秒 ≈ `duration_sec` 5648，正確
    - 讀取端 [`dashboard_queries._build_hr_zones()`](../../src/main/python/services/dashboard_queries.py) **不讀那個已正規化的欄位**，而是回頭重解 `raw_data_json` 並套 `ms_to_sec()`（該假設只對**官方匯出包**成立）→ 5517.4 / 1000 ≈ **5 秒**
  - **影響範圍實測**：290 場有 `hrTimeInZone`，其中 **7 場**為 API 同步（單位為秒）會被誤除 1000；其餘 283 場來自匯出包（確實是毫秒）目前正確。**壞掉的正好是最近同步、最常被問到的那幾場**，且此數字會隨每次同步增加
  - **⚠️ 原訂修法被推翻（改前查證擋下）**：原本打算「讀取端改吃 `activities.hr_time_in_zone_json`」，實查後不可行——該欄位**刻意只存 Z1~Z5、不存 Z0**，而 `_build_hr_zones` 的百分比分母**必須含 Z0**（暖身時間），改讀該欄位會讓各區間佔比虛增，正是 2026-08-18 改版 Phase 2 修掉的那個 bug；且該欄位僅涵蓋 203/529 場，退回路徑無論如何省不掉
  - **實際修法（✅ 已實作）**：修在**判斷單位**這一層而非資料來源。`_build_hr_zones(raw_data_json, *, is_api)` 新增來源旗標，由 `get_session_detail` 依該列的 `source_version == "api_sync"` 傳入；`is_api` 時直接取秒，否則維持 `ms_to_sec()`。此形狀與 `garmin_import_runner._build_backfill_hr_zones(raw, *, is_api)` 既有前例一致
    - **判定規則已全庫驗證無例外**：290 場有心率區間者中，7 場 api_sync 全為秒、283 場匯出包全為毫秒。另 2 筆曾被誤判為異常的匯出包場次（id 214／267），實為其 zone 總和對應 `elapsedDuration`（含暫停）而非 `duration`，單位仍是毫秒
    - **常數不 import 同步層**：`garmin_sync_runner` 在 module level 匯入 `garmin_api_client`／`garmin_auth`，會把網路相依拖進純查詢層（FastAPI 與 MCP server 都載入它）。故 `dashboard_queries.API_SYNC_SOURCE_VERSION` 重寫字面值，兩邊一致性由 `test_api_sync_source_version_matches_sync_runner` 強制
    - **真實資料庫驗證**：session 1042 由 **5 秒 → 5517 秒**（占時長 97.7%），1043／1044 同樣恢復正常；匯出包場次數值零變動。全庫 290 場掃描後僅剩 1 筆比值異常（id 267 重訓，`duration_sec=53` 但實際經過 4170 秒，屬既有資料問題、與本修正無關）
    - **新增 5 項單元測試**（毫秒路徑／秒路徑／Z0 仍計入分母的迴歸防護／`get_session_detail` 接線的端到端／常數一致性），全套 **510 項**通過
  - **⚠️ 順帶查明的跨來源限制（已寫入 docstring，非缺陷）**：API payload **不含 `hrTimeInZone_0`**（12 筆 api_sync 全數只有 Z1~Z5），故 API 來源的 `below_zone_1` 恆為 0、分母只有 Z1~Z5，匯出包則含 Z0。兩者 `pct` 的分母語意不同，跨來源並排比較時需知道這件事。**不可為了「一致」而把匯出包的 Z0 也排除**——那會回頭破壞既有場次的佔比正確性
  - **教訓（已寫入 [PLAN.md §1](PLAN.md#1-決策記錄decisions-log)／[§7.4](PLAN.md#74-錯誤事實的放大風險原-74-第-3-點--已處理)）**：正規化欄位一旦建立，下游就**不該再有第二條從 `raw_data_json` 重算的路徑**，否則單位假設會各自漂移。諷刺的是寫入端的 docstring 上個 session 已把這個失敗預言得一字不差（「若誤除以 1000 會得到總和 5 秒的荒謬值」），當時只修了自己那一側

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
  - [ ] **HRV 改用 LnrMSSD + SWC**（第二步；第一步的資料充足度檢查已完成）：`hrv_ln = ln(rMSSD)` → 4 週基線 mean/SD → 觸發線 `mean − 0.5×SD` → 基線每週更新。🟢 有完整文獻支持（Plews/Buchheit），是四項閾值中唯一如此者
    - **⚠️ 資料世代限制（2026-09-01 查證，使用者確認換錶）**：`hrv_ms`（每日 rMSSD）**僅 308 天**、2025-10-25 起（312 天中缺 4 天，皆為單日斷點）；`hrv_weekly_avg_ms` 雖有 1086 天、2023-08-13 起，但**其中 778 天只有週均無每日值**。裝置紀錄：deviceId `3453515234` 自 2023-08 用到 2026-08，與週均起始日吻合——**舊錶無 HRV 功能，2023-08 換錶後才有週均；同一支錶再隔 26 個月（2025-10-25）才開始提供每日值**（`source_version` 為舊 export 路徑，非匯入器問題）
    - **已排除「配戴不連續」解釋（2026-09-01 查證）**：2023-08~2025-10 這 26 個月，每月步數／睡眠／靜息心率幾乎滿月（多為 30/30、31/31），週均 HRV 自 2023-11 起全滿——**使用者持續配戴且戴著睡覺（HRV 正是睡眠時測），條件早已滿足**。且切換為瞬間發生：2025-10 僅月底 7 天有值、2025-11 立刻 30/30 全滿，**無逐步爬升的暖機形狀**。故非配戴行為所致，最可能為 Garmin 端韌體／Connect 功能上線（⚠️ 此點資料無法證實，需查當時韌體更新紀錄；但不影響結論）
    - **已排除「換錶」解釋（2026-09-01 裝置查證）**：每日 HRV 全數來自 deviceId `3453515234`，該錶 **2023-08-27 起使用至 2026-08-31 未再更換**（前一支 `3424804616` 於 2023-10-07 最後一次出現，2023-08~10 為換錶重疊期）。即**每日值出現時已用同一支錶 26 個月**，期間活動密集（2025-06 達 30 場）。裝置時間軸：無ID(2019-12~2020-12)→`3320049624`(2020-12~2022-07)→`3424804616`(2022-07~2023-10)→`3453515234`(2023-08~迄今)
    - **⚠️ 型號查不到**：activity payload 僅有 `deviceId` 與 `manufacturer=GARMIN`，**無型號名稱、無韌體版本**（已掃全欄位）。要對應實體型號須由使用者從 Garmin Connect 裝置頁或錶身確認。註：Garmin「連戴 3 週」要求是為算**狀態標籤**，非產生每日數值本身
    - **因此 778 天是「功能不存在」而非「資料缺漏」，永遠補不回來**；週均已抹平日間變異，**不可用於 SWC**（SWC 需日間 SD）。⚠️ N-4 「以 Garmin 1086 天序列作偏移對照」須據此修正：該序列非同質資料，308 天每日值與 778 天週均不可當一條連續序列比較
    - **每日值日間變動實測**：中位數 5.0ms、平均 5.9ms、單日跌幅 >10ms 者 307 天中 24 天；全距 21–80ms。故單日數值不可直接判讀，需基線
    - **🔴 現行規則的結構缺陷（已用實資料確認）**：`readiness.py` 現以「相對前 7 日均值」判斷，**基準會隨使用者一起下沉**——2026-08-07 hrv=44，前 7 日均 45.1（線 42.9）判為正常，但 28 天 SWC 線為 49.2 判為偏低。7 天太短，會漏掉慢性累積疲勞（正是最該抓的情境）。Garmin 本身要求連戴 3 週才給狀態標籤，與 Plews 4 週基線同理
    - **實測觸發率對照**（280 個可判斷日）：現行 90 次（32.1%）vs SWC 87 次（31.1%），總量接近但**僅 67 天重疊**——兩者抓的是不同日子，非同一批
    - **📋 下一步定為「先出對照報告、不動 `readiness.py`」**（2026-09-01 使用者質疑「7 天平均怪怪的」後轉向）：把 308 天以「現行 7 日規則／SWC 4 週規則／實際訓練紀錄」並排，由使用者本人以體感驗證何者貼近，再決定是否替換。理由：①每日值僅 308 天，樣本充足度須先確認 ②月跑量 4–7 次，有負荷的日子稀疏 ③「那幾天實際上累不累」只有使用者知道，無法由資料推得
    - **不採 Garmin 內建狀態標籤**：門檻為未公開的內部演算法，不可追溯／不可解釋／不可校正，違反 N-3「閾值須標註出處」前提；且目前 DB 未存該標籤（僅存數值）
    - **已知限制（實作時須寫入輸出說明）**：SWC 偵測的是「相對個人近期基線的偏離」，**不知原因**——熬夜／飲酒／生病／生活壓力同樣會觸發。它是偏離偵測器，非疲勞偵測器，故 readiness 須維持三維度合看，不可單押 HRV
  - [ ] **配速執行度容差**：⚠️ **無任何文獻定義「偏差多少算沒執行」**。建議 easy/long 用區間判定且只在偏快時提示；threshold ±3%、interval 偏慢 −3% 提示而偏快改提示「超速無額外效益」——±3% 為 🔴 純工程決策，實作時須明確標註無文獻背書
  - [ ] **環境修正（⏸️ 已擱置——缺資料，待補天氣來源後再做；2026-09-01 使用者決定「可以加但目前還不需要」）**：🟡 Daniels/VDOT 規則 `adjustment_pct = max(0, (temp_F + dewpoint_F - 130) / 10) × 1%`，作為判定前的預處理。**台灣氣候會讓此規則一年中大半時間觸發，不做修正則夏季課表會被系統性判為未執行**。Ely et al. 2007 顯示較慢的跑者受罰更重，單一係數會低估影響，須用自己的資料重新校正
    - **🔴 阻斷點 1：露點完全不存在。** 2026-09-01 實查一筆 Garmin activity payload 全部 133 個 leaf 欄位，環境相關**只有** `minTemperature`／`maxTemperature`——無濕度、無露點、無 weather 區塊；本機無 FIT 檔可退回，`fit_parser.py` 亦無溫度 channel。露點是公式約一半的輸入，且是決定汗水能否蒸發散熱的那一半（見 research 檔），不可用相對濕度替代
    - **🔴 阻斷點 2：現有溫度不是氣溫，是手腕體溫污染值。** 205/271 場跑步有溫度（75.6%），但**四年間從未有一筆低於 21°C**（`max<20°C` 計 0 筆），2023-01-14 一月台北記錄 min 25／max 29，全資料集中位數 max 29／min 27。台北冬晨常態 12–16°C，故此欄位為手錶熱敏電阻受體熱污染的讀數
    - **因此不採「標低信賴度硬上」**：三欄位契約能標記「吵雜」的輸入，救不了「語意錯誤」的輸入——一月 29°C 會在前處理階段灌大夏季修正值，正是 N-4 §7.4 第 3 點「錯誤事實放大」（同 `acwr` 欄位那次）。依契約規則「無法標記就回傳 `None`」。且校正係數需要可信自變數，否則是拿體熱假影去擬合修正曲線
    - **解法（未實作）**：用歷史天氣 API 依 `started_at` 回填 271 場的氣溫＋露點。候選 Open-Meteo archive（免費、免金鑰、可回溯數年、提供逐時 temperature 與 dewpoint）。⚠️ 代價：新增外部相依；定點天氣抓不到實際路線日照差異
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
- [x] **訓練品質評估**（2026-09-01 完成）：實作於 [`services/session_quality.py`](../../src/main/python/services/session_quality.py)，包成 MCP tool `assess_session_quality`
  - **三個生理維度**：強度符合度（實際 %HRR 對該課種預期區間）、配速穩定度（前後半配速差）、心率漂移（透傳 `fit_parser`）
  - **範圍收斂**（2026-09-01 使用者決定）：**只分析實際練的內容，不與計畫課表對照、不算遵從度**。既有的 135 天自動排課表當參考、不當基準
  - **刻意不輸出總分**：三維度分開呈現，合成單一分數等於把判斷偷渡進事實層。已寫 `test_no_overall_score_is_emitted` 斷言此約束
  - **不評就是不評**：課種 unknown/NULL 不評強度、短於 20 分鐘不評配速，並在 `not_assessed` 附理由
  - **心率漂移刻意不給 verdict**：本專案算的是純心率差非 Pa:HR，無可用閾值（見 N-3 第一批）
  - 18 項單元測試，全套 350 項通過
- [x] **★ 對話層：行為約束層（教練人設）**（2026-09-01 完成）：實作為 [`mcp/server.py`](../../src/main/python/mcp/server.py) 的 `@mcp.prompt(name="coach")`，由 `_ROLE`／`_BEHAVIOR_CONSTRAINTS`／`_OUTPUT_SHAPE` 三個獨立常數組裝（即風格層擴充點）。10 項單元測試逐條斷言約束存在，防止日後改寫時無聲刪除；全套 360 項通過
  - **介接方式定案**（2026-09-01）：先走 **Claude Code / Desktop + MCP**，**不自建 App、不自己呼叫 Anthropic API**。Claude 本身即判斷層，專案不需管 token 費與 model 版本。此決定解除 PLAN.md §1「對話介面暫不決定」那條擱置
    - 理由：行為約束層那份 prompt 兩種方案內容完全相同，先用 MCP 驗證「教練講話對不對」，確認有用再決定值不值得做 App；反過來先做介面，可能做完才發現教練講的話不對
    - 之後若要搬到自建 App，該 prompt 原封不動可帶走
  - **實作形式**：`@mcp.prompt()` 寫在 [`mcp/server.py`](../../src/main/python/mcp/server.py)，非散落的文件
  - **必須寫進約束的項目**（全部承本輪已定案的結論，不可再自行發明）：
    - [x] 看到 `confidence.level == "low"` 必須說明不確定性，不可當精確值使用；`biases` 裡寫明錯在哪就照實講
    - [x] `not_assessed` / `assessability.blocked` 列出的維度**不得下結論**，應明說「這項無法評估」及原因，不可自行猜測課種後照評
    - [x] **不得自行發明閾值**——所有判讀門檻只能來自規則引擎的輸出。心率漂移**不可**套用 5% 門檻（本專案算的非 Pa:HR，實測中位即 6.2%）
    - [x] **每次給建議須回述所用的事實清單**（列出輸入，非解釋原因）——承 N-4 定案，這是失敗歸因與比重驗證的唯一入口
    - [x] 無資料時明說不臆測；涉及傷病症狀不給醫療建議
    - [x] 不主動改課表——規則層與判斷層皆只輸出建議，改不改由使用者決定（承 §1「規則層永不自動改寫課表」）
  - **⚠️ 只做行為約束層**；風格層（語氣/口吻/幽默感）已擱置為 Phase 4 的 N-5，見 [PLAN.md §7.6](PLAN.md#76-人設的兩層結構行為約束層與風格層-風格層已擱置待未來有空檔再研究)。**組裝方式須預留風格層擴充點**（約束與語氣分開成區塊，不可混寫成一段）
  - **驗收狀態（2026-09-01）**：三個情境的**資料層**已用 Fu 真實資料逐一驗證，prompt 引用的欄位（`confidence.level`／`biases`／`not_assessed`／`assessability.blocked`／`hr_drift` 無 verdict）確認全部實際存在——① 正常路徑：近 30 天 9 場活動 ② session 78（unknown 課種）：`dimensions` 為空、三個維度全落 `not_assessed` 並附理由 ③ session 6（tempo）：兩個維度皆帶 `confidence.level == "low"`（HRmax 手錶顯示值＋自訂區間）
  - [x] **行為驗收通過（2026-09-02）**：以 `coach()` 回傳的三段常數為約束，對 Fu 的真實資料實跑三個情境，五條約束逐條檢核通過
    - **情境 A（近況查詢）**：正確區分可評與不可評——強度符合度可評（8/30 LSD 66.2% HRR、8/27 easy 61.3% HRR，皆 within）但就地標註 low 並轉述 `biases[].detail` 的具體內容（HRmax ±5bpm、區間為自訂未校正）；配速穩定度與心率漂移列為不可評。**另主動修正口徑**：`list_running_sessions` 的 `7d` 是滾動 7 天（8/27–9/2）而非日曆週，以日曆週計本週（8/31 起）實為 0 場
    - **情境 B（需逐秒資料的維度）**：問「上次長跑心率漂移多少」→ 誠實答「這場評不了」並附 `reason`（API 同步無逐秒 record），**未用分圈心率湊近似值**；並主動說明即使補上資料，本專案算的是純前後半心率差、Pa:HR 的 5% 門檻不適用
    - **情境 C（要求改課表）**：只給建議未動手，明說 `generate_training_plan` 會取代現行課表、須明確指示才呼叫；本次未呼叫任何寫入型 tool。**另指出現行課表已與實跑脫節**（課表下週 LSD 6.6K vs 實跑 15.1K），並標明自己給的 10–12K 是拿近期實跑量往回收、非文獻閾值
    - **五條約束檢核**：①信賴度一起讀 ✅ ②不可評不評 ✅ ③不發明閾值 ✅（主動擋掉 Pa:HR 5%、`acwr` 貼近 100 時拒套 1.3 比值門檻）④回述事實清單 ✅（三情境皆附 tool／數值／信賴度三欄）⑤不主動改課表 ✅
    - **約束 1 實際擋下一個錯誤數字**：情境 A 中 `hr_zones` 回傳全場區間總時長僅 5 秒（實際 5648 秒），因是無信賴度標記的裸數字而被拒用——**這是真 bug，見 Phase 1.5「🐛 `hr_zones` 讀取端單位錯誤」**。此為 §7.3 三欄位契約首次在對話中攔截到錯誤事實的實例
  - ⏳ **殘留一步（使用者操作）**：上述驗收是把 `coach()` 的常數逐字讀出後套用，內容與 `/coach` 注入的完全相同，但 **slash command 的注入路徑本身尚未被走過**（MCP prompt 屬使用者端指令，AI 無法自行觸發）。請於重啟後的 session 親自打一次 `/coach` 並問情境 B，確認 prompt 確實載入
- [ ] 對話層的其餘能力（承行為約束層完成後）：查詢近況、解釋建議原因、動態微調課表
  - 定位：AI 教練負責「拿規則引擎的旗標做決策與溝通」，不負責「產生旗標」（延續 PLAN.md §6.7 原則）
  - **2026-09-02 驗收後盤點**：前兩項（查詢近況、解釋建議原因）**已隨行為約束層一併可用並實測通過**（情境 A／B）；第三項「動態微調課表」**不成立**——缺的不是對話能力，而是底層根本沒有調整粒度 → 已獨立為 **N-7**，須先 grill
- [ ] **`context_notes` 軟性輸入落地**（承 N-4 定案，⚠️ 規格已定、尚未實作）：新增資料表（自由文字＋日期範圍＋`athlete_id`），**不**設類型／嚴重度結構化欄位；配一個寫入型 MCP tool 讓學員在對話中記錄「睡不好／工作累／腳有點緊」
  - **硬邊界**：永不進事實層、不參與任何規則計算，僅作判斷層的補充脈絡
  - ⚠️ `@mcp.prompt("coach")` 約束 5 已寫明此界線，但目前**無處可存**——等於該約束描述了一個不存在的輸入
- [ ] **N-1**：新學員 onboarding 流程須詢問希望的稱呼，並在後續對話一致使用
  - ⚠️ 與 **N-8** 同根：稱呼要「後續對話一致」就需要跨對話持久化，目前無此機制
- [ ] 建立評估回饋迴圈：AI 建議 → 實際執行 → 下次分析納入偏差比對
  - ⚠️ **2026-09-02 判定此項目前無法開工**：前提是「建議」有被記錄下來，而目前建議只存在於對話中，關掉視窗即消失 → 已獨立為 **N-8**，須先規劃

- [x] **★ 計畫↔實際配對層 A1/A2 已完成（2026-09-03；B 段仍待 N-7 grill）**：教練看到的「這場活動對應的計畫」目前是一個**無防護的猜測**，且沒有任何信賴度標記——與 §7.3 三欄位契約直接衝突。
  - **F1 `linked_activity_id` 是死欄位**：[`schema.sql`](../../src/main/python/models/schema.sql) 有此欄、[`dashboard_queries.py`](../../src/main/python/services/dashboard_queries.py) 的 `_fetch_planned()` 會讀它，但**全專案無任何一行寫入**。實測 `training_plan` 246 筆中 `linked_activity_id IS NOT NULL` 為 **0 筆**，故第一條 query 永遠 miss、永遠掉到 fallback。
  - **F2 fallback 無防護**：fallback 為「同一天 + `ORDER BY id LIMIT 1`」，**不過濾 `activity_type`**、不檢查該課表是否已被同日其他活動認領、歧義時直接取第一筆。實測 **65 天有多場活動**，最常見組合為 `walking, running` 與 `walking, strength_training`——走路那場查 session detail 會拿到當天的跑步課表當成「這場的計畫」。
  - **影響範圍已查證**：`planned` 進入 MCP `get_session_detail` 與 Dashboard session detail（教練會直接讀到）；**未**進入 `assess_session_quality`（[`server.py`](../../src/main/python/mcp/server.py) 的品質評估未使用 `detail["planned"]`），故不污染評分，但污染教練敘述。
  - **F3 脫節偵測為零**：生效課表 2026-09-01 起，9/2 排 tempo 4.9km 而當天 0 場活動——**課表第二天就漏了且無人偵測**。此即 N-7 記載的「課表已與現實脫節」，現有具體實例。
  - [x] **A1 抽出運動類型家族常數（2026-09-03 完成，commit `37e3b5c`）**：跑步類型集合現定義於三處。⚠️ **初判「三處不一致」是誤讀**——[`fit_import_runner.py`](../../src/main/python/services/fit_import_runner.py) 的 `DEFAULT_COMPARISON_ACTIVITY_TYPES` 刻意不含 `trail_running`，其註解寫明用途是「配速**可比較**」，而 trail 因地形配速本就不可比，**這是正確的第三種語意、不是缺漏**。真正重複的是 [`dashboard_queries.py`](../../src/main/python/services/dashboard_queries.py) 與 [`training_load.py`](../../src/main/python/services/training_load.py) 的兩份完全相同定義。故 A1 為：建立單一模組同時定義「跑步家族」與「配速可比較的跑步」兩個具名集合，讓語意差異顯性化，而非把它們合併成一個。
  - [x] **A2 `_fetch_planned()` 改為明確配對器（2026-09-03 完成，commit `37e3b5c`）**：精確配對（`linked_activity_id`）→ 啟發式（同日 **+ 運動類型相符** + 該課表未被同日其他活動認領）→ 多候選回 `ambiguous` **不選**；課表日 ≥ 今天無活動為 `planned_future`、< 今天為 `skipped`。每筆帶 `match_method`／`match_confidence`，接上既有 [`data_confidence.py`](../../src/main/python/services/data_confidence.py)。**純函式、不寫入 DB**。
  - **⚠️ 刻意不做（屬 N-7 爭點，不可先斬）**：不落地 `linked_activity_id`（爭點②版本化）、不定脫節偵測門檻（出處回到 N-3）、不推課表上手錶（爭點①粒度）。A1/A2 只把「猜的」標記成「猜的」，不改課表、不落地決策，故不預先決定 N-7 任何爭點；做完反而能提供真實配對率與歧義率供 grill 使用。
  - **完成後的實測（2026-09-03）**：對真實資料庫比對舊新邏輯，與課表日期重疊的活動僅 1 場（生效課表 9/1 才開始）——activity 1080（走路）舊邏輯回報「計畫是 rest」，新邏輯回報 `no_compatible_plan`。**樣本數為 1，非統計證據**，但該案例正是 F2 的典型形狀。531 項單元測試全過；啟動 API 實查 session 1042／1080 確認 `planned_match` 正確透出。
  - **形狀出處**：[`ching-kuo/garmin-py`](https://github.com/ching-kuo/garmin-py)（MIT）的 `services/reconciliation.py` 狀態機與 `metrics/registry.py` 的 sport family 常數。**只取形狀不取程式碼**；其 metric registry 全面重構已評估後不採用（本專案 `normalize_activity()` 已是單一函式且註解記載得來不易的單位事實，重構風險大於收益）。

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
- **🔴 N-7 課表調整的操作粒度（2026-09-02 Phase 3 驗收時暴露，⚠️ 需先 grill 定案，這是主目標的一半）**：專案主目標明列「給出訓練建議與**課表調整**」，但目前**只有建議、沒有調整**——唯一的寫入型 tool `generate_training_plan` 是**整份重產並取代**（`is_active=0` + `superseded_by`），沒有「把下週三降為 easy」「這週整體減量 20%」這種單日／單週粒度的操作。
  - **驗收時的實例**：情境 C 問「幫我把下週課表調輕一點」，教練只能給文字建議，因為唯一能動課表的手段是砍掉整份 111 天課表重排——代價與請求完全不成比例。
  - **同時暴露第二個問題：課表已與現實脫節且無人偵測**。現行課表 2026-08-20 產生，下週排 LSD 6.6K，而 Fu 8/30 實跑 15.1K（2.3 倍）。沒有任何機制發現「這份課表已經不適用了」。
  - **相關背景（2026-09-03 補）**：若調整後的課表最終要推回手錶，Garmin 的 `workout-service` 把「課表內容」與「行事曆排程」分成兩個獨立資源——改內容用 `PUT /workout/{id}`、改日期用 unschedule + schedule，兩者可各自單獨動。**這個分法本身就是一組現成的調整粒度候選**，可作為爭點 ① 的輸入。API 形狀與 enum 對照見 [docs/research/garmin-workout-upload-api.md](../research/garmin-workout-upload-api.md)（⚠️ 全部未對 Garmin 實測，僅為第三方 repo 的閱讀所得）。
  - **既有實作可參考**：[`ching-kuo/garmin-py`](https://github.com/ching-kuo/garmin-py)（MIT，同樣建構於 `garminconnect`）的 `services/reconciliation.py` 已解決本項的第二個問題（課表與現實脫節無人偵測）——先用 `workout_id` 精確配對、失敗才退到 date+sport 啟發式，且**歧義時明確標記 `ambiguous` 而非硬選一個**，每筆帶 `match_method`／`match_confidence`／`data_quality[]`／`provenance`。grill N-7 前值得先讀，可省去重新發明狀態機。
  - **待 grill 的爭點**（不可直接實作）：①調整的最小單位是什麼（單日／單週／期別）②調整後如何與 `is_active`／`superseded_by` 版本化模型相容——逐日改動若每次都產生新版本，版本歷史會爆炸 ③「規則層永不自動改寫課表」的既有硬規則下，調整的發起者必須是使用者，那 AI 的輸出形狀應該是什麼（一份 diff 提案？）④是否需要「課表過期偵測」規則，其門檻的出處為何（回到 N-3）
- **🔴 N-8 跨對話記憶與回饋迴圈（⚠️ 需先規劃）**：目前每次對話都是冷啟動，教練**不知道自己上週建議過什麼**，因此 TODO 既有的「AI 建議 → 實際執行 → 下次分析納入偏差比對」在缺少「建議被記錄下來」這一環時無法成立。
  - **與 N-4 的關係**：N-4 定案「AI 每次建議須回述所用事實清單」是**失敗歸因的唯一入口**，但那份清單目前只存在於對話中，關掉視窗就沒了——歸因入口實際上是斷的。
  - **待釐清**：建議存哪（新表？`context_notes` 併用？）、存什麼粒度（整段文字 vs 結構化的建議+事實清單）、誰負責寫入（AI 主動呼叫寫入型 tool，與「唯一寫入型 tool」的既有克制原則衝突）、如何連結到後續實際執行的活動。
- **🟡 N-9 逐秒資料的長期缺口（⚠️ 需一個小決策）**：API 同步進來的活動沒有 `activity_records`，故 `assess_session_quality` 三個維度中**有兩個（配速穩定度、心率漂移）對所有新活動永久不可評**，且此缺口隨每次同步擴大——歷史 FIT 涵蓋到 2026-08-16，其後全部是摘要。
  - 2026-09-01 的決策記錄判定「優先度低」，理由是心率漂移本就無可用閾值。**但配速穩定度不受此理由保護**，它是可評的維度，只是缺資料。
  - **候選解**：`garminconnect` 的 `download_activity(activity_id, dl_fmt=ORIGINAL)` 可取回原始 FIT，接既有 `fit_parser`／`fit_import_runner` 即可，不需新解析器。代價是每場多一次請求與檔案暫存。**要不要做、是否只對長跑/品質課做，需使用者決定。**
- **⏸️ N-5 可自訂的教練回答風格層**：只有行為約束的教練雖可信但回答易枯燥乏味，構想為「專業使用」／「輕鬆使用」兩條路，在行為約束層優先的前提下加入語氣變化。**2026-08-21 討論後擱置**——已識別兩個未解問題（「不可違背專業度」無法執行、風格中立性假設在壞消息上會破），待未來有空檔再深度研究。構想與問題分析見 [PLAN.md §7.6](PLAN.md#76-人設的兩層結構行為約束層與風格層-風格層已擱置待未來有空檔再研究)，已列入 Phase 4 TODO；Phase 3 實作行為約束層時仍須預留其擴充點。
- **🟡 N-10 環境資料與 WBGT（2026-09-05 定案資料源與 schema，資料表已建立，回填未做）**：目前系統**無法分辨一場掉速的成因**是體能退步、疲勞、還是高溫高濕，AI Coach 的建議因此建立在未校正環境因素的配速趨勢上。這不是加分功能而是**推論層缺口**——依 [PLAN.md §7.3](PLAN.md#73-定案框架事實--依據來源--信賴度) 三欄位契約，「未校正環境因素」本身就是一個目前沒被標出來的已知偏誤。
  - **起因**：參考外部作品 [running-dashboard](https://works.lifehacker.tw/works/running-dashboard.html)（品捷，雷蒙三十學員作品）。該作品的四項取捨（本機優先／AI 只輔助／推送前確認／來源要標註）本專案皆已是既定原則，**唯一實質缺口是環境資料**。
  - **為何對本專案特別重要**：Fu 的訓練密度為週跑量約 15K、月跑 4–7 次，樣本數少到單場的環境干擾佔比特別大——跑量大的人可靠樣本數把天氣雜訊平均掉，本專案不行。
  - **✅ 資料源已定案（見 [PLAN.md §1](PLAN.md#1-決策記錄decisions-log) 2026-09-05 該條）**：Open-Meteo 單一來源；排除中央氣象署（無歷史 API、需 key、輻射覆蓋存疑）與 OpenWeather（缺輻射欄位）；WBGT 自行實作簡化式，不採 Liljegren／PyWBGT（授權 CC BY-NC-SA + 需 Cython 編譯 + 精度用不上）。
  - **✅ schema 已定案並建立（2026-09-05）**：使用者決定**存逐小時序列**（而非起跑快照），理由是主要用途為歸因，而長跑後半段正是最需要解釋的那一段，與起跑時可能差 3–4°C。新增 `activity_weather` 表（見 [schema.sql](../../src/main/python/models/schema.sql)），每場活動 1~3 列、以 `is_representative` 標出起跑那一小時。**若日後太吃資源可退回只用代表值那列，不需改表結構**——此為使用者明確指示的退場路徑。已於真實資料庫副本（529 筆活動）驗證遷移成功，UNIQUE／CHECK／FK 三項約束均實測觸發，531 項測試全通過。
  - **✅ 資料可用性已查證（2026-09-05，僅查證未實作）**：
    - **座標覆蓋率：戶外跑步 252 場 100% 有座標**，回填不需猜預設地點。缺座標的 202 場全為**本來就無 GPS 的運動**（重訓 183、跑步機 16、室內有氧／風箏衝浪／游泳 3），而這些**本來就不該有戶外環境資料**。座標取自 `raw_data_json` 的 `startLatitude`／`startLongitude`。品質已驗：無 `(0,0)` 空值、無超出合法範圍、252 場全落在台灣境內（緯度 22.67–25.09、經度 120.50–121.57），分屬 17 個起跑點（前二者各 129／84 場）。
    - **⚠️ 夜跑佔 57%，熱壓力主因是濕度不是日照（此點修正了先前對輻射權重的判斷）**：起跑時段以 18:00（67 場）、17:00（52 場）、19:00（43 場）為大宗，日落後／日出前起跑共 **143 場（57%）**。實測 Open-Meteo 資料：2026-08-13 夜跑 20:00 `GHI=0.0 W/m²`，此時戶外版與遮蔭版 WBGT **完全相同**（27.35 vs 27.35）。**輻射的價值集中在白天那 43%，並非普遍性的**——但這不推翻 Open-Meteo 的選用（2026-08-22 傍晚場實測 `GHI=331 W/m²`，戶外版高出遮蔭版 1.09°C，該差距只有含輻射才算得出）。
    - **真正的發現**：那場夜跑 `T=28.5°C RH=88%`，WBGT 達 **27.35°C，已超過 ACSM 極高風險門檻（>23）**，且完全沒有太陽。台北夏夜本身即高熱壓力環境。**判讀與對話層不可預設「熱＝太陽曬」而漏掉濕度這條線。**
    - **資料量：252 場約 441 列**（79 場跨 1 個整點、157 場跨 2 個、16 場跨 3 個）。對照 `activity_records` 已有 68,884 列，此量可忽略——「太吃資源」在目前規模下不會發生，退場路徑短期內用不到。
    - **邊界安全性**：以 Stull (2011) 濕球近似搭配線性 Tg 式試算六情境，`GHI=0` 與 `風速=0` 皆不發散（風速取下限 0.3 m/s 防除零），退化為合理值。
  - **⚠️ 公式出處仍待補（N-3 範圍）**：上述試算用的 Stull (2011) 濕球近似與線性 Tg 式**僅為可行性驗證，尚未查到權威出處，不等於選定公式**。依 N-3「閾值須標註出處」，正式實作前須先確認公式來源並記入 `wbgt_formula`。同理，ACSM 的 WBGT 風險門檻（<10／10–18／18–23／>23）目前僅為對照參考，引用前需查證原始出處。
  - **實作要點**（尚未實作）：
    - 歷史回填走 `https://archive-api.open-meteo.com/v1/archive`（ERA5，1940 起，非商用免 key，已實測 `200`）；**近 5 天須改走 forecast API 的 `past_days` 參數**——ERA5 有 **5 天延遲**，剛跑完的活動在 archive 端點查不到。
    - 需要的 hourly 欄位：`temperature_2m`、`relative_humidity_2m`、`dew_point_2m`、`shortwave_radiation`、`direct_radiation`、`wind_speed_10m`。
    - **WBGT 必用戶外版**（`0.7·Tw + 0.2·Tg + 0.1·Ta`，Tg 由輻射與風速推算）。**嚴禁**使用遮蔭版 `0.7·Tw + 0.3·Ta`——該式忽略輻射與風速，只適用於室內或良好遮蔭，對戶外跑步是系統性低估，且正好低估在大太陽天。
    - 信賴度須標明**公式版本**與**「輸入為 11km 網格內插值而非實測」**兩項已知偏誤。
  - **⚠️ 查證限制（誠實聲明）**：中央氣象署的結論建立在文件閱讀與兩次 `401`／端點語意判讀上，**未能實際 dump 到 API 回應**（需 key）。若日後要走這條路，必須先申請 key 打一次真實請求確認欄位，不得照本條結論直接寫程式——此為 `cloud_agent_needs_local_verification` 的同類風險。Open-Meteo 則已對真實日期（2025-11-23，資料庫既有的 10K 場次）實測回傳確認欄位與單位。
  - **相關但獨立的後續**：該作品另兩個值得參考的呈現層想法——**依「問題」而非「資料粒度」分頁**（總覽／今天該練嗎／訓練負荷／有氧效率），與**準備度拆成多張評分卡**（速度／耐力／比賽適能／訓練負荷／恢復狀態，避免單一分數低了卻分不出是累還是體能退步）。兩者皆為純前端重排、資料現成（`get_readiness_status`／`training_load.py`）、無過期風險，可於 Phase 1.5 Phase 6 前後任意時點插入，**不阻塞本項**。
