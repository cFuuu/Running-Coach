# MCP Server 測試計畫

> 本檔案記錄 `src/main/python/mcp/server.py`（Task 3.1）8 個 tools 的系統化驗證進度。
> 目的：補齊「單元測試（合成假資料）」與「對話中隨手實測（無記錄）」之間的缺口——
> 逐項列出每個 tool 該驗證的情境、驗證方式、目前狀態，供之後回頭確認或補測。
> 個人資料相關的實測數字不寫入本檔，只記錄「驗證了什麼、結果是否符合預期」。

## 現有驗證層級

| 層級 | 涵蓋範圍 | 現況 |
|---|---|---|
| 單元測試 | `src/test/unit/test_mcp_server.py`，16 項，直接呼叫 tool 函式本體，合成假資料，臨時 `:memory:` DB | ✅ 已存在，隨 `unittest discover` 一併跑 |
| 對話中實測 | 2026-08-20，透過真正 MCP protocol（stdio）呼叫，對 `output/running_coach.db` 真實資料 | ✅ 7 個唯讀 tool 已呼叫成功，但**無系統化記錄涵蓋哪些邊界情況** |
| 本計畫 | 針對真實 DB 的邊界情況與跨執行緒/協定層驗證，含 1 次 `generate_training_plan` 真實寫入 | ✅ 2026-08-21 執行完成，發現 1 項 bug 並已修復驗證 |

單元測試不重複列在本計畫——本計畫只列**單元測試用合成假資料測不到、必須對真實 DB 或真實 MCP protocol 驗證**的項目。

## 測試環境

- Server 已用 `claude mcp add running-coach`（project scope）註冊，`claude mcp get running-coach` 應顯示 `Status: ✔ Connected`
- DB：`output/running_coach.db`（唯一真實學員 `athlete_id=1` "Fu"）
- 執行前確認：`git status` 乾淨（寫入型測試會真的改資料庫檔案，非程式碼變更不會被 git 追蹤，但仍應在乾淨狀態下執行以利事後比對）
- 2026-08-21 執行方式：直接呼叫 `src/main/python/mcp/server.py` 內的 tool 函式本體（同單元測試手法），但接的是真實 DB 連線而非 `:memory:`——不是走真正 stdio protocol，但涵蓋「真實資料的邊界情況」這個目標

---

## 唯讀 Tools（7 個）

### 1. `get_athlete_meta`

| # | 情境 | 驗證方式 | 狀態 |
|---|---|---|---|
| 1.1 | 省略 `athlete_id`，預設取第一位學員 | 呼叫後確認回傳 `athlete.id == 1`、`athlete.name == "Fu"` | ✅ 通過 |
| 1.2 | 指定不存在的 `athlete_id`（如 999） | 確認不拋例外，回傳結構合理降級 | ✅ 通過——`resolve_athlete_id` 對無效 id 降級忽略，退回真實學員，非 500 錯誤 |
| 1.3 | 各指標可用日期範圍 | 交叉比對 `metric_coverage` 表 | ✅ 通過——活動 2019-12-14~2026-08-16（約 7 年）、HRV 2025-10-25 起（約 10 個月），與 TODO.md 記錄吻合 |

### 2. `list_running_sessions`

| # | 情境 | 驗證方式 | 狀態 |
|---|---|---|---|
| 2.1 | `range_key="7d"` | 確認回傳合理筆數 | ✅ 通過（3 筆） |
| 2.2 | `range_key="all"` | 確認總筆數與已知跑步活動數一致 | ✅ 通過（266 筆，與 TODO.md 記錄的跑步活動數一致） |
| 2.3 | `range_key="custom:YYYY-MM-DD:YYYY-MM-DD"` 自訂區間 | 帶已知有活動的區間（2025-11-20~2025-11-26），確認解析正確 | ✅ 通過（5 筆，含已知的 2025-11-23 10K） |
| 2.4 | 非法 `range_key`（如 `"3d"`） | 確認明確報錯，不靜默回傳空清單 | ✅ 通過——明確拋出 `ValueError: 不支援的 range：3d（合法值：7d, 30d, 90d, 1y, all 或 custom:YYYY-MM-DD:YYYY-MM-DD）` |

### 3. `get_session_detail`

| # | 情境 | 驗證方式 | 狀態 |
|---|---|---|---|
| 3.1 | 已知 FIT 來源活動（2021-12-19 21K，session id 478） | 對照已知數字逐項核對 | ✅ 通過——22 圈、配速 430/399/419 秒（=7:10/6:39/6:59）、心率 150/164/162，與 TODO.md 記錄完全一致 |
| 3.2 | JSON 手動分圈來源（無 FIT）的活動 | 確認 `laps` 來源優先序正確退回手動分圈 | ⚠️ **無法在真實資料上驗證**——查詢發現目前資料庫中「有手動 splits 且無 FIT 分圈」的組合不存在（FIT 匯入涵蓋 91% 跑步活動，剩餘無 FIT 的活動也剛好都沒有手動 splits）。此路徑僅由單元測試合成假資料覆蓋，非本次計畫的缺口，但記錄於此供未來有新資料時補測 |
| 3.3 | 不存在的 `session_id` | 確認回傳 `None`，非拋例外 | ✅ 通過 |
| 3.4 | `hr_zones` 數值 | 確認 Z1~Z5 皆列出（含 0 值），單位換算正確 | ✅ 通過——Z1~Z5 秒數加總（53+3629+20+0+0）+ 暖身 92 秒 = 3794 秒，與 `duration_sec` 完全吻合，證實 `hrTimeInZone_N` 毫秒→秒換算正確 |

### 4. `get_wellness_trend`

| # | 情境 | 驗證方式 | 狀態 |
|---|---|---|---|
| 4.1 | `range_key="30d"` 預設 | 確認 `metrics` 結構含 HRV/睡眠/RHR/全天壓力 | ✅ 通過 |
| 4.2 | `range_key="all"` 觸發 `clipped` | 確認 HRV 正確標記 `clipped:true` | ✅ 通過 |
| 4.3 | 缺資料日期 | 確認缺值日期不補 0 | ✅ 通過——30 天窗口中 `2026-07-23`（HRV 為 NULL 那天）正確地不出現在 `points` 裡（29 點而非 30 點） |

### 5. `get_recovery_impact`

| # | 情境 | 驗證方式 | 狀態 |
|---|---|---|---|
| 5.1 | `range_key="30d"` | 確認 `impacts` 只有原始數值、無判讀文字 | ✅ 通過 |
| 5.2 | 訓練後隔天缺 wellness 資料 | 確認正確標記 `available:false` + 原因，不強行補值 | ✅ 通過——實測案例回傳 `{"available": false, "reason": "隔天沒有每日身體狀況資料"}` |

### 6. `get_readiness_status`

> ⚠️ 對應 [readiness.py](../../src/main/python/services/readiness.py) 的 warmup fix（commit `6d80212`），本次測試計畫的重點項目。

| # | 情境 | 驗證方式 | 狀態 |
|---|---|---|---|
| 6.1 | 同一天的判讀結果不因 `start_date` 不同而改變 | 用多組不同 `start_date`（皆涵蓋同一目標日）各呼叫一次比對 | ❌→✅ **初次失敗，已修復（見下方「本次發現並修復的問題」）並複測通過**——修復後對 4 個目標日 × 5 種 `start_date` 組合複測，`readiness` 離散判讀結果全部一致，TSB 殘留誤差降到 0~0.8（原本高達 72.8），且遠小於觸發門檻附近的差距 |
| 6.2 | `start_date` 落在資料庫最早活動之前，暖機資料不足 168 天（修復後的 `CTL_WARMUP_DAYS`） | 確認不報錯，降級行為合理 | ✅ 通過（修復後複測）——不報錯，回傳 `readiness: normal`（無足夠負荷歷史時的合理降級） |
| 6.3 | 高強度訓練日附近 TSB 不出現不合理極端值 | 與 6.1 為同一根因，不重複驗證 | 併入 6.1 結果 |
| 6.4 | 非法日期格式 | 確認報錯清楚 | ✅ 通過（修復後複測）——`ValueError: Invalid isoformat string: 'not-a-date'` |

### 7. `get_active_training_plan`

| # | 情境 | 驗證方式 | 狀態 |
|---|---|---|---|
| 7.1 | 目前生效課表 | 確認回傳筆數與 DB `is_active=1` 筆數一致 | ✅ 通過（111 筆，完全吻合） |
| 7.2 | 版本化欄位 | 確認回傳含 `plan_source`／`is_active`／`superseded_by`，日期已序列化 | ✅ 通過 |

---

## 寫入型 Tool（1 個）

### 8. `generate_training_plan` ⚠️

執行前快照：`total=222`、`active=111`、`active range=2026-09-01~2026-12-20`。

用不重疊的未來區間執行：`start_date=2027-01-01`、`total_weeks=4`、`days_per_week=3`。

| # | 情境 | 驗證方式 | 狀態 |
|---|---|---|---|
| 8.1 | 呼叫 `generate_training_plan` | 確認 `available=True`、`schedule` 非空、日期已序列化 | ✅ 通過——`available=True`，24 筆排程，日期為字串格式 |
| 8.2 | 執行後重新查詢 DB | 確認新寫入列數合理、原有生效課表未被誤動 | ✅ 通過——`total: 222→246`（+24）、`active: 111→135`（+24），原 111 筆（2026-09-01~2026-12-20）的日期範圍與筆數完全未變 |
| 8.3 | `get_active_training_plan` 呼叫後 | 確認新舊兩批生效課表同時查到 | ✅ 通過——合併回傳 135 筆，涵蓋 2026-09-01 至 2027-01-24 |
| 8.4 | 事後清理 | 記錄本次測試寫入的 `training_plan_ids`，不刪除 | 已記錄：**id 250~273（共 24 筆，`planned_date` 2027-01-01~2027-01-24）為本次測試資料**，非真實課表，未來若查到這批 id 請對照本檔 |

---

## 本次發現並修復的問題

### 🟢 CTL 暖機期不足，`get_readiness_status` 的 TSB 曾因 `start_date` 不同而改變（已修復）

**現象**（6.1 初次實測）：目標日 2026-08-10 的 readiness 判讀結果依呼叫時傳入的 `start_date` 不同而不同：

| 呼叫 `start_date` | 2026-08-10 的 TSB（修復前） |
|---|---|
| `2026-08-01` | -214.2 |
| `2026-07-01` | -141.4 |

**根因**：commit `6d80212` 的 warmup fix 只往前抓 `CTL_TIME_CONSTANT_DAYS`（42 天）暖機資料，這對 **ATL**（7 天時間常數）的收斂綽綽有餘（實測兩種情境 ATL 僅差 729.3 vs 729.7，幾乎一致），但对 **CTL 自己**（42 天時間常數）而言，EWMA 只給 1 個時間常數的暖機時間遠遠不夠收斂——實測兩種情境 CTL 差了 515.1 vs 588.3，這個差距直接反映到 TSB（= CTL − ATL）上，導致修復後的症狀依然存在，只是幅度略有改善。

**修復**（2026-08-21）：
- [training_load.py](../../src/main/python/services/training_load.py)：新增 `CTL_WARMUP_DAYS = CTL_TIME_CONSTANT_DAYS * 4`（168 天），取 EWMA 收斂慣例的 4 倍時間常數作為暖機門檻
- [training_load_queries.py](../../src/main/python/services/training_load_queries.py)：`compute_readiness_for_athlete()` 的暖機窗口改抓 `CTL_WARMUP_DAYS`（原本抓 `CTL_TIME_CONSTANT_DAYS`），順帶解決常數跨 seam 外洩問題（呼叫端改成引用「暖機需要多少天」這個獨立命名的常數，不必自己知道「CTL 時間常數是 42 天、需要抓幾倍」的內部邏輯）

**修復驗證**：301 項單元測試全過；對真實 DB 重跑 6.1，4 個目標日 × 5 種 `start_date` 組合的離散判讀結果（`readiness` 標籤）全部一致，TSB 殘留誤差降到 0~0.8（原本高達 72.8），且遠小於觸發門檻（-30.0）附近的差距，不影響任何判讀結論。EWMA 理論上不會有 100% 收斂（誤差指數衰減趨近於 0 但不歸零），此殘留量級已屬可接受範圍。

**影響範圍**：`readiness.py`／`training_load_queries.py`（commit `6d80212`）、`mcp/server.py` 的 `get_readiness_status` tool、`api/routes_dashboard.py` 若有呼叫同一條路徑者皆受益於此修復。

---

## 執行紀錄

| 日期 | 執行範圍 | 結果摘要 | 備註 |
|---|---|---|---|
| 2026-08-21 | 7 個唯讀 tool 全部情境（3.2 除外，資料庫無可測組合）+ `generate_training_plan` 真實寫入 | 25/26 項通過，1 項失敗 | 發現 `get_readiness_status` 的 TSB 仍會因 `start_date` 不同而改變（CTL 暖機不足）。`generate_training_plan` 寫入測試乾淨完成，未影響原有生效課表，測試資料為 `training_plan.id` 250~273 |
| 2026-08-21 | 修復 CTL 暖機不足問題並複測 6.1/6.2/6.4 | 26/26 項通過 | 詳見上方「本次發現並修復的問題」。`training_load.py` 新增 `CTL_WARMUP_DAYS`，`training_load_queries.py` 改用該常數。301 項單元測試全過，DB 狀態未受影響（total=246, active=135，與寫入測試後一致） |
