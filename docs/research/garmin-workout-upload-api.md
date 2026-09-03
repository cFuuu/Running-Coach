# Garmin 課表上傳 API 形狀調查（workout-service）

> **建立日期**：2026-09-03
> **調查目的**：為未來「把 Phase 2 產生的課表推上手錶」保留 API 形狀與 enum 對照，避免屆時從零逆向。
> **資料來源**：閱讀兩個第三方公開 repo 的原始碼——
> [`wenchi0920/garmin-tools-kit`](https://github.com/wenchi0920/garmin-tools-kit)（無 LICENSE，enum 數字 ID 出處）與
> [`ching-kuo/garmin-py`](https://github.com/ching-kuo/garmin-py)（MIT，2026-09-03 補入，端點補完與修正來源）。
> **範圍限制**：本文**只**記錄「課表寫入 Garmin」這條路徑。既有的活動／健康資料**讀取**已由 `garminconnect` 實作，不在本文範圍。
>
> ## ⚠️ 信賴度警語（讀之前必看）
>
> **本文全部內容來自閱讀他人程式碼，未對 Garmin 帳號實際發出過任何一個請求。**
> 這些數字是「某個 repo 的作者宣稱它可用」，不是本專案驗證過的事實。
> 實作前必須逐項對真實 API 驗證，驗證後再回頭把本文的標記從「未驗證」改掉。
> 這與本專案「規則輸出必須帶已知偏誤」是同一條原則：**來源的可信度要跟數字一起走。**
>
> 另注意：該 repo **無 LICENSE 檔**，等同未授權。
> 本文記錄的是**協定事實**（endpoint 路徑、enum 對照），可自行實作；
> **不得**複製其程式碼進本專案。

---

## 1. 結論摘要

1. **課表寫入走 `/workout-service/*`**，與現有讀取路徑（活動／健康）是不同的服務前綴。
2. **最有價值的是 enum 對照表**（§3）——`targetType`／`endCondition`／`sportType` 的數字 ID，自己試錯要花不少時間。
3. **「課表 DSL → Garmin JSON」的中介層是對的設計**（§4），但本專案應該從 `training_plan` 表直接編譯，**不要**引入 YAML 中介檔。

---

## 2. Endpoint（未驗證）

該 repo 使用 `garth` 的 `garth.client.request(method, "connectapi", url, ...)`。
本專案已於 2026-03-28 排除 garth（見 `requirements.txt` 註解），改用 `garminconnect`，
但**端點路徑與 payload 形狀與 HTTP client 無關**，可直接沿用。

| 動作 | Method | 路徑 | 備註 |
|---|---|---|---|
| 列出課表 | GET | `/workout-service/workouts` | query: `start=0&limit=100`（有分頁，未確認上限） |
| 取單一課表 | GET | `/workout-service/workout/{workoutId}` | |
| 建立課表 | POST | `/workout-service/workout` | body 為課表 JSON，回傳含 `workoutId` |
| **更新課表** | **PUT** | `/workout-service/workout/{workoutId}` | **2026-09-03 由第二來源補上**，見下方修正 |
| 刪除課表 | DELETE | `/workout-service/workout/{workoutId}` | |
| 排入行事曆 | POST | `/workout-service/schedule/{workoutId}` | body `{"date": "YYYY-MM-DD"}`，回傳含 `workoutScheduleId` |
| **取消排程** | **DELETE** | `/workout-service/schedule/{workoutScheduleId}` | ⚠️ 參數是 `workoutScheduleId`，**不是** `workoutId`；課表本身保留 |
| **讀行事曆** | GET | `/calendar-service/year/{y}/month/{m}/day/{d}/start/1` | ⚠️ `month` 是 **0-based**（一月=0）；回傳 `calendarItems`，一次一週 |

> **注意「建立」與「排程」是兩步**：上傳只是把課表存進帳號的課表庫，
> 要出現在手錶當日課表還得再呼叫一次 schedule。
>
> **🔧 修正（2026-09-03，第二來源）**：本文初版寫「改一天課表 = 刪＋建＋重排三個請求，
> 沒有 PATCH」，**這是錯的**。[`ching-kuo/garmin-py`](https://github.com/ching-kuo/garmin-py)
> （MIT，同樣建構於 `garminconnect`）明確實作了 `PUT /workout-service/workout/{id}`
> 就地更新，以及 `DELETE /workout-service/schedule/{scheduleId}` 單獨取消排程。
> 所以「改內容不動日期」只需一個 PUT；「改日期不動內容」只需 unschedule + schedule。
> 錯誤來源是初版只讀了一個 repo，而那個 repo 沒實作 update ——
> **單一來源的「沒有」不能推論成協定上的「不存在」**，這正是本文警語要防的事。

---

## 3. Enum 對照（未驗證，但這是本文最主要的價值）

### 3.1 `sportType`

| key | id |
|---|---|
| `running` | 1 |
| `cycling` | 2 |
| `swimming` | 3 |
| `strength_training` | 5 |
| `yoga` | 7 |
| `mobility` | 11 |

> 4、6、8–10 未出現在該 repo，用途不明。

### 3.2 `endCondition`（一個 step 何時結束）

| key | conditionTypeId | `endConditionValue` 單位 |
|---|---|---|
| `lap.button` | 1 | 無（或次數，見下方警告） |
| `time` | 2 | 秒 |
| `distance` | 3 | 公尺 |
| `breaths` | 12 | 次 |

> ⚠️ **可疑之處**：該 repo 把「10 reps」也映射成 `lap.button` 並塞 `endConditionValue=10`。
> `lap.button` 的語意是「按錶才結束」，帶一個數值在語意上矛盾——
> 有可能 Garmin 對肌力課表另有 `reps` 條件型別而作者沒找到，也可能真的就這樣用。
> **實作前務必先用手機 App 建一個含次數的肌力課表，再 GET 回來看真實 JSON。**

### 3.3 `targetType`（一個 step 的強度目標）

| key | workoutTargetTypeId | 附帶欄位 |
|---|---|---|
| `no.target` | 1 | 無 |
| `heart.rate.zone` | 4 | `zoneNumber`（1–5） |
| `pace.zone` | 6 | `targetValueOne` / `targetValueTwo`，單位 **公尺/秒** |

> **配速目標用 m/s，不是秒/公里**——`6:00/km` 要換成 `1000 / 360 = 2.778`。
> 且因為配速快 = 速度值大，區間的 one/two 要用 `min()`／`max()` 排序後放，
> 不能照「配速字串的前後」直接填。這是很容易寫反的一處。
> 2、3、5 等其他 target 型別（如功率、心率 bpm 絕對值）未出現。

### 3.4 step 型別

`warmup`=1、`cooldown`=2、`interval`/`run`=3、`recovery`=4、`rest`=5、`repeat`=6。
`repeat` 為群組節點，內含子 step，該 repo 用一個遞增的 `childStepId` 串接。

---

## 4. 值得借鏡的設計：課表的可讀中介表示

該 repo 讓 AI 產生一份 YAML DSL，再編譯成 Garmin JSON：

```yaml
"W22-7km T 門檻跑 (0528)":
  - warmup: 2km @H(153-166) @note(Z2 暖身)
  - run: 3km @H(171-180) @note(T 門檻配速 6:07)
  - cooldown: 2km @H(134-153)
```

**值得學的**：Garmin 的原始 workout JSON 極度囉嗦（每個 step 都是巢狀 DTO），
中間放一層人看得懂、diff 得出來的表示法是對的，出錯時能一眼看出是課表內容錯還是編譯錯。

**不該照抄的**：他們讓 LLM 直接生 YAML 檔、YAML 檔就是唯一真相。
本專案的真相在 `training_plan` 表（Phase 2 已能產出 16 週 111 天課表），
**應該從資料庫編譯成 Garmin JSON**，DSL 若要存在也只是除錯用的中間產物，不是儲存格式。
再引入一份 YAML 會製造第二個真相來源。

另一個小點子：他們用巢狀 list 表達「一天兩練」（跑步 + 瑜伽），資料模型上可參考。

---

## 5. 明確不採用的部分

記在這裡是為了避免日後有人重讀那個 repo 時再評估一次。

| 項目 | 不採用的理由 |
|---|---|
| `garth.login(email, password)` 作為 session 過期的 fallback | 直接牴觸 `CLAUDE.md` 憑證規則第 2 條：token 失效必須拋錯要求人工重跑，**不得**自動用帳密重登（Cloudflare 對 `/sso/signin` 有 rate limit） |
| 用 markdown 檔案當狀態儲存 | 本專案已有 SQLite 統一資料模型，退回去是倒退 |
| 讓 LLM 直接產生課表數字（週量、減量幅度、配速區間） | Phase 2 規則引擎的存在意義就是這件事要可驗證。該 repo 的重試迴圈只檢查「檔案有沒有生出來」，不檢查內容是否合理 |
| 其 VDOT 估算法 `VDOT = VO2cost / %HRR` | 拿 %HRR 當 %VO2max 用，低強度下系統性偏差（其自己的 spec 也承認 <50% HRR 就得放棄）；且該 repo 中**根本沒有實作**，只有 plan/spec。本專案已採 Daniels 正規路徑 |
| 個人健康資料進版控 | 該 repo 把體重、RHR、HRV、350 筆含 GPS 反解地點的活動全部公開。本專案 `output/` 不進版控的作法維持不變 |

---

## 6. 若要實作，第一步該做什麼

**不要**從寫編譯器開始。先做這件事：

1. 用手機 Garmin Connect App 手動建三個課表：一個含配速目標的跑步、一個含心率區間的跑步、一個含次數的肌力。
2. 用現有的 `garminconnect` client 對 `GET /workout-service/workouts` 與
   `GET /workout-service/workout/{id}` 打一次，把**真實 JSON 原樣 dump 下來**。
3. 拿真實 JSON 逐項核對本文 §3 的每一個數字，改掉錯的、補上缺的，
   並把本文開頭的警語改成實際驗證日期。

> **這一步不可省略。** 對照 1B-9 的教訓：雲端 agent 產出的 API 解析程式碼，
> 單元測試會過是因為 fixture 跟程式碼**共用同一個錯誤猜測**——實際對真實帳號
> dump 才抓出五處巢狀路徑與單位錯誤（`a07bbf6`）。
> 本文的每一個數字目前都還在「猜測」階段，處境跟當時的雲端程式碼完全相同。

---

## 7. 未查證事項清單

- 全部 endpoint 與 enum **皆未對 Garmin 實測**（見開頭警語）。
- `lap.button` 帶 `endConditionValue` 的語意矛盾（§3.2）未釐清。
- 未確認建立課表的 JSON 是否有必填欄位是該 repo 沒處理到的（例如 `estimatedDurationInSecs`、`ownerId`）。
- 未確認 `GET /workout-service/workouts` 的 `limit` 上限與分頁行為。
- 未確認刪除／覆蓋同名課表時 Garmin 的實際行為（該 repo 有 `deleteSameNameWorkout` 設定，但那是它自己在 client 端做的比對，不是 API 功能）。
- 未確認課表上傳是否有 rate limit，以及與現有同步流程共用連線時的交互影響。
