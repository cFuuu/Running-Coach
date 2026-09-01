# Garmin Connect 指標完整性審計報告

> **審計日期**：2026-09-01
> **狀態**：📋 待決策 — 本報告**未修改**任何 schema 或程式碼
> **實測帳號**：Fu 本人（Forerunner 965）｜**基準日**：2026-08-31
> **函式庫版本**：`garminconnect` 0.3.11（cyberjunky/python-garminconnect, MIT）
> **方法**：DB schema 盤點 + 已落庫 `raw_data_json` 統計（528 筆活動 / 1667 筆 wellness）+ 對 8 個未使用端點做即時探測 + `garminconnect` 原始碼審讀

---

## 0. 執行摘要

### 0.1 兩個問題的答案

**Q：`python-garminconnect` 有沒有把這些指標拿來做參考與評估？**

**沒有。它 100% 是資料搬運層，零分析邏輯。** 原始碼實證：

| 證據 | 結果 |
|---|---|
| `__init__.py` 中 176 個 def/class | 其中 87 個是 `return self.connectapi(url)` 的純轉發 |
| 搜尋 `mean` / `median` / `stdev` / `statistics` / `numpy` / `percentile` | **0 個命中**（僅命中 URL 字串與參數名） |
| 搜尋 `PRODUCTIVE` / `MAINTAINING` / `STRAINED` / `BALANCED` / `feedbackPhrase` 的解讀邏輯 | **0 個命中** — 狀態碼原樣傳出，不做翻譯或判讀 |
| 最「複雜」的方法 `get_lactate_threshold()` | 只是把兩支 endpoint 的回傳合併成一個 dict，外加處理 Garmin 的歷史拼字錯誤 `hearRate` |

README 自述為 *"A comprehensive Python3 API wrapper"*，明講範圍是 **data retrieval and CRUD operations**，不含 metric interpretation。

**這其實是好消息，而非壞消息**：訓練科學的判讀邏輯本來就是本專案 Phase 2/3 的核心價值所在。若函式庫代勞了，反而只是換一個黑盒。`garminconnect` 把「乾淨取得原始數據」這件事做完整，正是我們需要它做的事。

**Q：有沒有目前能直接借鑑的部分？**

**有三項，價值依序遞減**，詳見 §4。最有價值的是 `typed.py`（Pydantic 欄位契約，可當免費的 payload 規格書）與 `workout.py`（結構化課表建構器，可讓 Phase 2 的課表**回推到手錶**）。

### 0.2 落差的三個層次

審計發現的問題不只是「欄位沒抓」，而是三種性質不同、嚴重度遞減的落差：

1. **🔴 已抓到但丟棄**（成本最低、價值最高）— `acuteLoad`、HRV baseline 三值、Running Dynamics 全套**已經在現有 API 回傳裡**，正規化層直接沒接。**不需要多打任何一次 API**。
2. **🔴 端點從未呼叫** — `get_training_status` 一支就同時帶回真 ACWR、急慢性負荷、最佳範圍、VO2Max、熱適應。每日僅 +1 次請求。
3. **🟠 三個現存資料正確性缺陷** — 其中一個會產生「看似有值但語意錯誤」的欄位，比缺值更危險。

---

## 1. ⚠️ 優先於擴充：三個現存資料正確性缺陷

這三項與新增欄位無關，是既有邏輯的問題，建議先修。

### 缺陷 A：`stress_avg` 與 `all_day_stress_avg` 被寫入同一個值 🔴

`garmin_sync_runner.py:316-317`：

```python
"stress_avg": stats.get("averageStressLevel"),
"all_day_stress_avg": stats.get("averageStressLevel"),   # ← 同一個來源
```

schema 註解明確寫著這兩欄「範疇不同，不可互相取代，故分開存欄位」——`stress_avg` 應為**睡眠期間**平均壓力（`sleep.dailySleepDTO.avgSleepStress`），`all_day_stress_avg` 才是全天。`garmin_export_parser.py:192` 走的正是正確語意。

**實測佐證（2026-08-31）**：`avgSleepStress = 18.0`，`averageStressLevel = 20`。DB 兩欄皆為 20 — 睡眠壓力被靜默替換成全天壓力。20 筆 api_sync 列全數受影響。

**修法**：一行改為 `sleep_dto.get("avgSleepStress")`。

### 缺陷 B：`acwr` 欄位名與內容不符 🔴（已知，但現在有解）

`acwr` 存的是 `acwrFactorPercent`（0–100 的 readiness 貢獻度分數，實測 87），不是急慢性負荷比。`data_confidence.KNOWN_BIASES` 已登記 `acwr_not_a_ratio` 標記 — 處置正確，但那是**繞過**而非修復。

本次審計找到真值來源：`get_training_status` 回傳 `dailyAcuteChronicWorkloadRatio = 1.0`（古典 0.8–1.3 區間的真 ACWR）。補上此端點後即可讓欄位名副其實，並撤下該偏誤標記。

### 缺陷 C：Training Readiness 取 `[0]` 是未驗證的假設 🟠

`garmin_sync_runner.py:296`：`readiness = readiness_list[0] if readiness_list else {}`

該端點**每日回傳多筆快照**（實測 2–4 筆）。實測確認 `[0]` 確實是最新的一筆（時間戳遞減排序），**故目前行為正確**——但這是未寫下的隱含假設，Garmin 若改變排序即靜默取錯值。

**更重要的是它揭露的語意問題**：readiness 分數在**同一天內劇烈變動**。

| 時間 | score | recoveryTime | inputContext |
|---|---|---|---|
| 02:22 | 50 | 1760 | UPDATE_REALTIME_VARIABLES |
| 08:14 | 60 | 1408 | AFTER_WAKEUP_RESET |
| 19:46 | 73 | 604 | AFTER_POST_EXERCISE_RESET |
| 22:22 | **75** | 445 | UPDATE_REALTIME_VARIABLES |

「2026-08-31 的 readiness」是 50 還是 75，取決於你問的是哪個時刻。存最後一筆（睡前 22:22）等於存了「一整天恢復後的結果」，但教練真正想問的是**「今天早上起床時身體準備好了嗎」**（`AFTER_WAKEUP_RESET`，60）。

**建議**：改為明確挑選，並額外存下 `inputContext` 與 `timestampLocal`，讓下游知道這個分數是哪個時點的。`garminconnect` 的 `typed.py` docstring 也提醒「use the snapshot with the most recent timestamp」，但對教練用途而言，**起床時的那筆才是決策相關的**。

> 附帶確認：`recoveryTime` 單位為**分鐘**（`typed.py` docstring 明載 *"reported in minutes"*），現行 `/60` 換算為小時**正確**。另 `typed.py` 提醒 `recoveryTimeChangePhrase == 'REACHED_ZERO'` 時使用者已完全恢復，不論數值為何（Garmin 會保留最後一次指派的值）——目前程式未處理此旗標，屬 P1 級的細節。

---

## 2. 指標落差對照表

### 2.1 訓練狀態與適應指標 — 端點 `get_training_status`（**從未呼叫**）

| 類別 | API 欄位路徑 | 中文指標名稱 | DB | 實測值 | 建議 |
|---|---|---|---|---|---|
| 訓練負荷 | `…acuteTrainingLoadDTO.dailyTrainingLoadAcute` | 短期訓練負荷 | ❌ 遺漏 | 302 | ✅ 全部加入（P0） |
| 訓練負荷 | `…acuteTrainingLoadDTO.dailyTrainingLoadChronic` | 長期訓練負荷 | ❌ 遺漏 | 275 | ✅ 全部加入（P0） |
| 訓練負荷 | `…dailyAcuteChronicWorkloadRatio` | **真 ACWR（比值）** | ❌ 遺漏 | 1.0 | ✅ 全部加入（P0，修缺陷 B） |
| 訓練負荷 | `…minTrainingLoadChronic` / `maxTrainingLoadChronic` | 最佳負荷範圍下/上限 | ❌ 遺漏 | 220.0 / 412.5 | ✅ 全部加入（P0） |
| 訓練負荷 | `…acwrStatus` | 負荷比狀態 | ❌ 遺漏 | `OPTIMAL` | ✅ 全部加入（P0） |
| 訓練狀態 | `…latestTrainingStatusData.{dev}.trainingStatus` | 訓練狀態代碼 | ❌ 遺漏 | 4 | ✅ 全部加入（P0） |
| 訓練狀態 | `…trainingStatusFeedbackPhrase` | 訓練狀態文字 | ❌ 遺漏 | `MAINTAINING_2` | ✅ 全部加入（P0） |
| 訓練狀態 | `…fitnessTrend` | 體能趨勢 | ❌ 遺漏 | 2 | 🟡 部分加入（P1） |
| 體能 | `mostRecentVO2Max.generic.vo2MaxPreciseValue` | VO2Max（精確值） | ❌ 遺漏 | 52.8 | ✅ 全部加入（P0） |
| 適應 | `…heatAltitudeAcclimation.heatAcclimationPercentage` | 熱適應 % | ❌ 遺漏 | 73 | 🟡 部分加入（P1） |
| 適應 | `…previousHeatAcclimationPercentage` + `heatTrend` | 熱適應趨勢 | ❌ 遺漏 | 84 → `DEACCLIMATIZING` | 🟡 部分加入（P1） |
| 適應 | `…altitudeAcclimation` | 高海拔適應 | ❌ 遺漏 | 0（台北，海平面） | ⛔ 暫不加入（P2） |
| 負荷組成 | `mostRecentTrainingLoadBalance.…monthlyLoadAerobicLow/High/Anaerobic` | 月負荷有氧低/高/無氧 | ❌ 遺漏 | 1117.7 / 0.0 / 0.0 | 🟡 部分加入（P1） |
| 負荷組成 | `…trainingBalanceFeedbackPhrase` | 負荷平衡回饋 | ❌ 遺漏 | `AEROBIC_HIGH_SHORTAGE` | 🟡 部分加入（P1） |

> ⚠️ **結構陷阱**：`latestTrainingStatusData` 與 `metricsTrainingLoadBalanceDTOMap` 的 key 是 **deviceId 字串**（`'3453515234'`），不是固定名稱。取值須挑 `primaryTrainingDevice == True` 的那筆，**不可寫死 device id，也不可直接取第一個 key**（換錶或多錶並用時會取錯）。

> 📊 **實測解讀**：`monthlyLoadAerobicHigh = 0.0`、`monthlyLoadAnaerobic = 0.0`，配合 `AEROBIC_HIGH_SHORTAGE` — Garmin 判定完全缺乏高強度有氧刺激。這與 Fu 目前的訓練型態一致，也正是 Phase 2 規則引擎該給出建議的地方。

### 2.2 已在現有 payload 裡、但正規化層丟棄（零 API 成本）

| 類別 | API 欄位路徑 | 中文指標名稱 | DB | 實測 | 建議 |
|---|---|---|---|---|---|
| HRV | `hrvSummary.baseline.balancedLow` / `balancedUpper` | HRV 平衡基準線下/上限 | ❌ 丟棄 | 47 / 61 | ✅ 全部加入（P0） |
| HRV | `hrvSummary.baseline.lowUpper` | HRV 偏低門檻 | ❌ 丟棄 | 44 | ✅ 全部加入（P0） |
| HRV | `hrvSummary.status` | HRV 狀態評級 | ❌ 丟棄（註解明說「無處可放」） | `BALANCED` | ✅ 全部加入（P0） |
| HRV | `hrvSummary.lastNight5MinHigh` | 夜間 5 分鐘最高讀數 | ❌ 丟棄 | 95 | 🟡 部分加入（P1） |
| HRV | `hrvReadings[]` | 夜間逐筆讀數 | ❌ 丟棄 | 72 筆 | ⛔ 暫不加入（P2） |
| 訓練負荷 | `training_readiness[0].acuteLoad` | 短期訓練負荷 | ❌ 丟棄 | 302 | ✅ 全部加入（P0）**零成本** |
| 壓力 | `sleep.dailySleepDTO.avgSleepStress` | 睡眠期間平均壓力 | ⚠️ 被覆蓋 | 18.0 | ✅ 修正（缺陷 A） |
| 睡眠 | `dailySleepDTO.deepSleepSeconds` 等四項 | 深/淺/REM/清醒時長 | ❌ 丟棄 | 3000/15900/2640/180 | 🟡 部分加入（P1） |
| 身體電池 | `stats.bodyBatteryAtWakeTime` | 起床時身體電池 | ❌ 丟棄 | 75 | 🟡 部分加入（P1） |
| 心率 | `stats.lastSevenDaysAvgRestingHeartRate` | 7 日平均靜止心率 | ❌ 丟棄 | 49 | ✅ 全部加入（P0） |
| Readiness | `training_readiness[0].inputContext` / `timestampLocal` | 快照時點與觸發原因 | ❌ 丟棄 | `AFTER_WAKEUP_RESET` | ✅ 全部加入（P0，修缺陷 C） |

### 2.3 單次活動進階跑步動態 — 已在 `raw_data_json`，未建欄位

覆蓋率統計自 **271 筆已落庫的跑步活動**：

| 類別 | API 欄位 | 中文指標名稱 | DB | 覆蓋率 | 建議 |
|---|---|---|---|---|---|
| 訓練效果 | `anaerobicTrainingEffect` | 無氧訓練效果 | ❌（僅有 `aerobic_te`） | 92% | ✅ 全部加入（P0） |
| 訓練負荷 | `activityTrainingLoad` | 單場運動負荷 | ❌ 遺漏 | 83% | ✅ 全部加入（P0） |
| 訓練效果 | `trainingEffectLabel` | 訓練效果標籤 | ❌ 遺漏 | 83% | ✅ 全部加入（P0） |
| 配速 | `avgGradeAdjustedSpeed` | 坡度校正配速 | ❌ 遺漏 | 72% | ✅ 全部加入（P0） |
| 跑姿 | `avgGroundContactTime` | 觸地時間（ms） | ❌ 遺漏 | 72% | 🟡 部分加入（P1） |
| 跑姿 | `avgVerticalOscillation` | 垂直振幅（cm） | ❌ 遺漏 | 72% | 🟡 部分加入（P1） |
| 跑姿 | `avgVerticalRatio` | 垂直步幅比（%） | ❌ 遺漏 | 72% | 🟡 部分加入（P1） |
| 跑姿 | `avgStrideLength` | 平均步幅（cm） | ❌ 遺漏 | 92% | 🟡 部分加入（P1） |
| 功率 | `avgPower` / `maxPower` / `normPower` | 跑步功率（W） | ❌ 遺漏 | 79% | 🟡 部分加入（P1） |
| 環境 | `minTemperature` / `maxTemperature` | 環境溫度 | ❌ 遺漏 | 高 | 🟡 部分加入（P1） |
| 心率分布 | `hrTimeInZone_1..5` | 心率區間停留時間 | ❌ 遺漏 | 高 | 🟡 部分加入（P1） |
| 功率分布 | `powerTimeInZone_1..5` | 功率區間停留時間 | ❌ 遺漏 | 中 | ⛔ 暫不加入（P2） |
| 極值 | `fastestSplit_1000/1609/5000/10000` | 各距離最速分段 | ❌ 遺漏 | 中 | ⛔ 暫不加入（P2，可由 laps 導出） |

> ⚠️ **GCT 左右平衡（GCT Balance）在本帳號取不到**。271 筆活動中 `groundContactBalanceLeft` / `avgGroundContactBalance` 覆蓋率為 **0%** — Forerunner 965 光學心率在不搭配 HRM-Pro / RD Pod 時不產生此欄位。**不建議為它建欄位**，這是硬體限制而非 ETL 缺漏。

### 2.4 其他未呼叫端點（本次新發現）

| 端點 | 中文指標名稱 | 實測值 | 建議 |
|---|---|---|---|
| `get_race_predictions` | 賽事完賽預測 | 5K 23:23 / 10K 49:54 / 半馬 1:53:16 / 全馬 3:12:45 | ✅ 全部加入（P0） |
| `get_lactate_threshold` | 乳酸閾值心率與配速 | HR 179、speed 0.358 m/s | ✅ 全部加入（P0） |
| `get_endurance_score` | 耐力分數 + 七級分級門檻 | 5905（classification 3） | 🟡 部分加入（P1） |
| `get_endurance_score.contributors[]` | 耐力分數來源貢獻 | 跑步 81.77% / 重訓 9.84% / 其他 8.39% | 🟡 部分加入（P1） |
| `get_fitnessage_data` | 體能年齡（+ 四項成分） | 19.8（實際年齡 27） | 🟡 部分加入（P1） |
| `get_hill_score` | 爬坡分數 | 27 | ⛔ 暫不加入（P2） |

> 📌 **`get_max_metrics` 不建議單獨呼叫**：實測 2026-08-31 回 `[]`、08-28 也回 `[]`，只有 VO2Max 實際更新的日子（08-30）才有值。同樣的 VO2Max 與熱適應資料 `get_training_status` **每天都回傳**（帶 `calendarDate` 標示實際量測日）。用後者，少一次請求且無空值。
>
> 📌 **`get_lactate_threshold` 與 `get_race_predictions` 不吃日期參數**，回傳當前值，屬「每次同步覆寫一筆最新快照」而非逐日序列。

---

## 3. 優先級與教練分析價值評估

### P0 — 強烈建議立即補齊

| 指標 | 教練分析價值 |
|---|---|
| **真 ACWR + 急/慢性負荷 + 最佳範圍** | 受傷風險預警的**唯一**直接因果特徵。目前 `acwr` 欄位是假的，等於這條防線實質不存在。有了 220–412.5 的最佳範圍，才能回答「這週該加量還是減量」——而非只是事後描述。 |
| **HRV baseline 三值（44/47/61）** | 單一 HRV 讀數（49）沒有意義，必須對照個人基準線才知道是否偏離。**這是 HRV 從裝飾品變成判讀依據的關鍵**，且資料已抓到、只是被丟掉。 |
| **`acuteLoad`（readiness payload 內）** | 零額外成本 — 已在現有回傳裡。 |
| **VO2Max 精確值（52.8）** | 整數 53 對訓練期的緩慢變化毫無解析度；小數點才看得出 12 週週期的體能走向。同時是 VDOT 引擎的交叉驗證錨點。 |
| **無氧訓練效果 + 單場運動負荷** | 目前只有 `aerobic_te`，等於**只能看到訓練刺激的一半**。無氧 TE 是判斷間歇課品質的核心，且它才是累積成 acute load 的來源。 |
| **賽事預測 + 乳酸閾值** | 乳酸閾值心率（179）直接決定 tempo 課的心率上限；賽事預測是課表目標配速的獨立校準源。兩者都是 Phase 2 規則引擎的直接輸入。 |
| **訓練狀態代碼與文字** | `MAINTAINING` / `PRODUCTIVE` / `STRAINED` 是 Garmin 對整體狀態的分類，作為對照組可驗證自建規則引擎的判斷是否合理。 |
| **坡度校正配速 / 7 日平均靜止心率** | 前者讓不同路線的配速可比；後者是 RHR 異常偵測的基準線。 |
| **Readiness 快照時點（`inputContext`）** | 見缺陷 C — 沒有它就無法知道分數代表一天中的哪個時刻。 |

> ⚠️ **P0 不等於「可直接用於判讀」**。訓練狀態、Training Readiness、HRV status 皆為 Firstbeat 黑盒衍生值，依 PLAN.md §1 須帶 `garmin_blackbox` 標記、只作對照。
>
> **但 acute/chronic load 與真 ACWR 是可解釋的累加量** — `dailyAcuteChronicWorkloadRatio` 有明確公開定義（急性負荷 ÷ 慢性負荷），可作為判讀依據。這是它與其他黑盒值的關鍵區別。**此判斷需經 Fu 確認**（見 §6）。

### P1 — 建議部分加入

跑姿三件套（GCT / 垂直振幅 / 垂直步幅比）、步幅、跑步功率、睡眠階段拆解、熱適應、耐力分數與來源拆解、體能年齡、心率區間分布、環境溫度、`REACHED_ZERO` 旗標。

**理由**：這些對**中長期趨勢與技術優化**有價值，但不進入短期課表決策的因果鏈。跑姿指標的真正用途是「疲勞時跑姿是否劣化」——需要 20+ 場的個人基準才有解讀力，且 72% 覆蓋率意味著會有系統性缺口。

熱適應（73%，趨勢 `DEACCLIMATIZING`）在台北夏季對配速預期有實質影響，但屬修正項而非決策項。

> 💡 **與 Fu 訓練量的交互作用**：以目前每月 4–7 次跑步的頻率，跑姿類指標要累積出可信的個人基準線需要數個月。這是把它們放 P1 而非 P0 的實際理由——不是指標不好，是**樣本數還撐不起判讀**。

### P2 — 可暫緩或忽略

| 指標 | 不建議的理由 |
|---|---|
| `hrvReadings[]`（每晚 72 筆） | 每日 72 列 × 365 天 ≈ 26K 列/年，而教練判讀只需 baseline 與 lastNightAvg。已被摘要涵蓋。 |
| `powerTimeInZone_1..5` | 與 `hrTimeInZone` 高度共線；跑步功率本身在 P1 已足夠。 |
| `fastestSplit_*` | 可由 `activity_laps` 直接導出，存了就是第二真實來源。 |
| 高海拔適應 | Fu 在台北訓練，實測恆為 0。有意義的只有出國移地訓練時 — 屆時再加。 |
| `get_hill_score` | 冷門且與耐力分數重疊。 |
| **GCT 左右平衡** | **硬體不支援，覆蓋率 0%**（見 §2.3 註記）。 |
| `ownerProfileImageUrl*` / `userRoles` / `ownerId` | 個資與 OAuth scope，依 CLAUDE.md §🔐 第 3 條不應落庫（見 §5 風險提示）。 |

---

## 4. `python-garminconnect` 可直接借鑑的部分

### 4.1 🟢 `typed.py` — Pydantic 欄位契約（**建議採用為參考文件**）

0.3.11 提供 `garminconnect[typed]` extra，內含 12 個 Pydantic 模型：
`DailyStats`、`SleepData`、`DailySleepDTO`、`SleepScores`、`HrvData`、`HrvSummary`、`HrvBaseline`、`TrainingReadiness`、`BodyBatteryEntry`、`Activity`、`ActivityType`、`SleepScoreValue`。

**價值有三層**：

1. **免費的 payload 規格書**。本次審計靠打真實 API 逐一探測欄位，`typed.py` 已把同一批欄位的名稱、型別、alias 寫成程式碼。**新增欄位時可直接對照，不必再打 API 試探**。
2. **文件化了容易踩雷的語意**。例如 `TrainingReadiness` 的 docstring 就寫明 `recovery_time` 單位是分鐘、多筆快照該取最新、以及 `REACHED_ZERO` 的特殊處理——這三點正是本次審計缺陷 C 的內容。
3. **`HrvBaseline` 模型的存在本身就是佐證**：`lowUpper` / `balancedLow` / `balancedUpper` 被獨立建模，代表上游認為這是使用者會用到的一級欄位，呼應本報告把它列為 P0。

**建議的採用方式（保守）**：把 `typed.py` 當**參考文件**讀，正規化層仍自己寫。

理由：本專案的 `normalize_*()` 已是穩定的手寫對應層，且 `pydantic` 2.13.4 雖然已因 `mcp[cli]` 間接安裝在環境中，但**目前不在 `requirements.txt` 的直接依賴清單**。為了型別驗證而讓 ETL 路徑相依於一個 optional extra，收益不足以抵過相依風險。若日後欄位數膨脹到難以手工維護，再重新評估。

### 4.2 🟢 `workout.py` — 結構化課表建構器（**對 Phase 2/3 有戰略價值**）

這是本次調查**最意外的發現**。函式庫提供完整的課表建構與上傳能力：

**建構器**：`create_warmup_step()`、`create_interval_step()`、`create_distance_interval_step()`、`create_recovery_step()`、`create_cooldown_step()`、`create_repeat_group()`、`create_strength_exercise_step()`、`create_strength_set()`

**模型**：`RunningWorkout`、`StrengthWorkout`、`ExecutableStep`、`RepeatGroup`、`WorkoutSegment` 等

**上傳/排程 API**：`upload_running_workout()`、`schedule_workout()`、`push_workout_to_device()`、`get_scheduled_workouts()`、`unschedule_workout()`

**為何這重要**：目前 `training_plan` 表產生的課表**只存在於本專案的 DB 裡**，Fu 實際跑步時手錶並不知道。這條 API 可以讓 Phase 2 排程器產生的課表**直接推送到 Forerunner 965**，形成完整迴圈：

```
規則引擎產生課表 → 推送到手錶 → Fu 照錶執行 → 同步回來 → 比對遵從度 → 調整下次課表
```

`create_interval_step(duration_seconds, step_order, target_type)` 與 `create_distance_interval_step(distance_meters, ...)` 的參數，與現有 `training_plan` 的 `planned_duration_sec` / `planned_distance_km` / `planned_pace_sec_per_km` **幾乎一對一對應**，映射成本低。

> ⚠️ **但這不屬於本次審計的範圍，且風險等級不同**：所有既有功能都是**唯讀**，這是本專案第一個會**寫入 Fu 的 Garmin 帳號**的操作。需要獨立評估寫入權限、失敗回滾、以及誤推課表到錶上的後果。**建議記錄為 Phase 2 的候選任務，本次不動。**

### 4.3 🟡 `fit.py` — FIT 檔編碼器（**與現有實作重疊，不建議切換**）

含完整的 FIT 二進位編碼實作（CRC 計算、`FitBaseType` 定義）。但本專案 Phase 1E 已用 `fitparse` 完成 FIT **解析**，且 `fit.py` 的用途偏向**產生** FIT 檔（配合課表上傳），非解析。**無替換價值。**

### 4.4 ⛔ 分析邏輯 — 不存在，無可借鑑

如 §0.1 所述。**訓練科學判讀仍須本專案自建**，這正是 Phase 2/3 的價值所在。

---

## 5. 建議的 DB 擴充方案（最小變更範圍）

前提：以 SQLite `ALTER TABLE ADD COLUMN` 為主 — 全部 nullable、無 default，對既有 528 筆活動與 1667 筆 wellness **無破壞性**，舊列自然留 NULL 由 `data_confidence` 層處理。

### 方案 A：只做 P0（建議起點）

**A-1. `activities` 加 4 欄**

```sql
ALTER TABLE activities ADD COLUMN anaerobic_te REAL;
ALTER TABLE activities ADD COLUMN activity_training_load REAL;
ALTER TABLE activities ADD COLUMN training_effect_label TEXT;
ALTER TABLE activities ADD COLUMN avg_grade_adj_pace_sec_per_km INTEGER;
```

**A-2. `daily_wellness` 加 8 欄**

```
hrv_baseline_low_upper REAL, hrv_baseline_balanced_low REAL,
hrv_baseline_balanced_upper REAL, hrv_status TEXT,
acute_load REAL, rhr_7day_avg_bpm INTEGER,
readiness_input_context TEXT, readiness_timestamp_local TEXT
```

**A-3. 新增 `training_status` 表**（每日一列，承載 `get_training_status`）

```sql
CREATE TABLE training_status (
    id, athlete_id, date,
    training_status_code INTEGER, training_status_phrase TEXT,
    acute_load REAL, chronic_load REAL,
    acwr_ratio REAL,                    -- 真 ACWR，與 daily_wellness.acwr 語意不同
    chronic_load_optimal_min REAL, chronic_load_optimal_max REAL,
    acwr_status TEXT, vo2max_precise REAL, vo2max_date TEXT,
    heat_acclimation_pct INTEGER, heat_trend TEXT,
    source, source_version, fetched_at,
    UNIQUE (athlete_id, date, source)
);
```

**A-4. 新增 `performance_metrics` 表**（賽事預測 + 乳酸閾值，非逐日序列，每次同步寫一筆快照）

> 💡 **為何 A-3 獨立建表而非塞進 `daily_wellness`**：
> 1. `daily_wellness` 的語意是「身體/恢復數據」，訓練狀態是**訓練面**的衍生量，來自不同端點、且有 device 維度。
> 2. 混入會讓該表的每日 upsert 邏輯必須處理兩組互不相干的空值模式。
> 3. **最具體的理由**：`daily_wellness.acwr` 已存了語意不同的東西，同表再放一個 `acwr_ratio` 極易誤用。

**A-5. 程式碼變更（本階段不執行）**

- `garmin_api_client.py`：新增 `fetch_training_status()`、`fetch_race_predictions()`、`fetch_lactate_threshold()`；`fetch_daily_wellness_raw()` 加入 training_status（每日 4→5 端點）
- `garmin_sync_runner.py`：`normalize_activity()` 補 4 欄、`normalize_wellness()` 補 8 欄、新增 `normalize_training_status()`；**修正缺陷 A、C**
- `data_confidence.py`：撤下 `acwr_not_a_ratio` 或改指向新欄位
- `_WELLNESS_METRIC_COLUMNS`：同步更新，讓 `metric_coverage` 涵蓋新欄位
- `_WELLNESS_ENDPOINTS_PER_DAY`：**必須由 4 改為 5**（見下方成本影響）

### 方案 B：P0 + P1

在 A 之上，`activities` 再加約 12 欄（跑姿 4、功率 3、溫度 2、心率區間 5 — 或心率區間改存 JSON 單欄避免欄位爆炸），`daily_wellness` 再加約 6 欄，新增 `endurance_score` 表含 contributors 子表。

### ⚠️ 同步成本影響

每日 wellness 端點由 4 → 5，**請求數 +25%**。以 `MAX_AUTO_SYNC_DAYS=90` 計：

| | 目前 | 方案 A/B |
|---|---|---|
| 請求數 | 361 | ~451 |
| 預估耗時 | ~6 分鐘 | ~7.5 分鐘 |

仍在 `garmin_sync_runner.py` 註解所述的安全範圍內，但 `estimate_sync_cost()` 的 `_WELLNESS_ENDPOINTS_PER_DAY` 常數**必須同步改為 5**，否則耗時預告會低估 — 而那是與 Fu 議定的行為（2026-09-01）。

### 🔐 附帶資安觀察（不在本次範圍）

`activities.raw_data_json` 目前整包存入 API 回傳，其中含 `ownerId`、`ownerDisplayName`、`ownerProfileImageUrl*`、以及完整的 `userRoles` OAuth scope 清單。

這些**不是 token**，風險等級遠低於憑證。但依 CLAUDE.md §🔐 第 3 條的精神（回傳值只應含運動與生理指標），若未來要把 `raw_data_json` 餵進 LLM context 或匯出，建議先做一層欄位白名單過濾。

---

## 6. 待 Fu 決策事項

### 6.1 採納範圍

- **選項 1**：只修三個現存缺陷（A/B/C），不擴充 — 最小風險
- **選項 2**：缺陷修正 + 方案 A（P0）— **建議**。P0 裡有相當比例是「資料已抓到只是沒接」，投入產出比最高，且直接補上受傷風險預警這條目前實質空缺的防線
- **選項 3**：缺陷修正 + 方案 B（P0+P1）— 一次到位，但 `activities` 表會從 26 欄長到約 43 欄
- **選項 4**：自訂 — 指定要納入的欄位

### 6.2 需確認的判斷

1. **acute/chronic load 與真 ACWR 是否視為「可判讀」而非黑盒對照組？**
   本報告把它們列為可判讀（理由見 §3 P0 警示框），這與 PLAN.md §1 對 Firstbeat 衍生值的一般處置不同。若認為仍應一律標記 `garmin_blackbox`，請告知，實作時會調整 `data_confidence` 的對應。

2. **Readiness 快照要取哪一筆？**
   建議取 `AFTER_WAKEUP_RESET`（起床時，決策相關）而非 `[0]`（睡前最後一筆）。但這會改變既有 20 筆 api_sync 列的數值語意，需確認。

3. **`workout.py` 課表回推手錶（§4.2）是否列入 Phase 2 候選？**
   本次不實作，僅詢問是否要記錄為未來任務。這是第一個會**寫入** Garmin 帳號的功能，風險性質與現有唯讀功能不同。

---

## 附錄：本次探測的端點清單

| 端點 | 狀態 | 備註 |
|---|---|---|
| `get_activities_by_date` | ✅ 已使用 | 111 欄，Running Dynamics 全在此 |
| `get_activity_splits` | ✅ 已使用 | lapDTOs 逐圈明細 |
| `get_stats` | ✅ 已使用 | 94 欄，多數未取 |
| `get_sleep_data` | ✅ 已使用 | 睡眠階段與 avgSleepStress 未取 |
| `get_hrv_data` | ✅ 已使用 | baseline 三值未取 |
| `get_training_readiness` | ✅ 已使用 | acuteLoad、inputContext 未取；多筆快照 |
| `get_training_status` | ❌ 未使用 | **本次最大發現**：真 ACWR + 負荷 + VO2Max + 熱適應 |
| `get_race_predictions` | ❌ 未使用 | 四距離完賽預測 |
| `get_lactate_threshold` | ❌ 未使用 | 閾值心率與配速 |
| `get_endurance_score` | ❌ 未使用 | 分數 + 來源貢獻拆解 |
| `get_fitnessage_data` | ❌ 未使用 | 體能年齡 + 四項成分 |
| `get_hill_score` | ❌ 未使用 | 爬坡分數（P2） |
| `get_max_metrics` | ❌ 未使用 | **不建議採用**，資料與 training_status 重複且常回空 |
