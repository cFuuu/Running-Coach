# 離線 Dashboard 匯出與 iPhone 開啟流程

> **建立日期**：2026-10-24　**狀態**：📋 規劃定案，待實作（TODO **N-30**）
> **目的**：把本機 Dashboard 打包成單一 HTML，經 iCloud Drive 同步到 iPhone，再用捷徑一鍵開啟。電腦不用開著，也不需要網路。
> 本機資料庫**不刪除任何歷年紀錄**，以下的縮減只作用在匯出檔上。

---

## 1. 現況（2026-10-24 盤點）

- `output/dashboard_offline.html`（6.3 MB）是過去臨時產生的，**專案裡沒有可以重跑的產生腳本**。
- 檔內有兩段載入程式：
  - 一段是 `window.__SNAPSHOT__` 的讀取程式，**已經支援 gzip 路徑**（`window.__SNAPSHOT_GZ__` → `atob` → `DecompressionStream('gzip')`），只是那次匯出沒有啟用。
  - 另一段把 JS 模組轉成 `data:` URL，透過 importmap 載入。
- 目前的 iPhone 捷徑是把**整份 HTML 原文貼進「文字」動作**，再經過「以 base64 編碼」→ `data:text/html;base64,…` →「打開」。所以每次更新都要重貼一次，檔案大的時候很難操作。

### 6.3 MB 的組成

| 內容 | 大小 | 說明 |
|---|---|---|
| 288 場單場明細 `/api/sessions/{id}` | 3.0 MB | 逐秒資料**已經**是 10 秒取樣＋欄式儲存，再壓縮空間不大 |
| 趨勢類 API 存了 90 天／1 年／全部 三份 | 約 2.2 MB | `wellness-trend`、`garmin-cards`、`training-load`、`recovery-impact` |
| 其他 API 回應 | 約 0.1 MB | — |
| 打包的 JS（importmap） | 0.46 MB | — |

### 縮減試算（以 2026-10-22 的真實匯出檔實測）

| 做法 | 資料部分 | 會失去什麼 |
|---|---|---|
| 現況 | 5.34 MB | — |
| **① gzip 壓縮** | **0.94 MB** | 不會失去任何資料 |
| ①＋單場明細只留近 12 個月 | 0.42 MB | 更早的場次只剩清單摘要，看不到逐秒圖 |
| ①＋近 6 個月 | 0.32 MB | 同上 |
| ①＋近 12 個月＋不存「全部」區間 | 0.30 MB | 「全部」區間無法切換 |

base64 會讓檔案多 33%。**只做 ①**，整份 HTML 約 1.3 MB，經過捷徑的 base64 後約 1.7 MB，比現在的約 8.4 MB 小很多。

**定案**：預設只做 ①，保留完整歷史。明細月數做成參數（`--detail-months`），檔案以後又變大時再調。

---

## 2. 匯出腳本規劃（`scripts/build_dashboard_offline.py`）

依照 [`scripts/build_pace_calculator.py`](../../scripts/build_pace_calculator.py) 的寫法：不新增依賴、不用打包工具、遇到不支援的寫法直接報錯，不默默打包出壞檔。

**處理流程**
1. 用 FastAPI `TestClient`（或直接呼叫 `dashboard_queries`）依序取得 Dashboard 會用到的 API 回應，組成 `{"exact": {url: response}, ...}`。
   - URL 清單要和前端實際發出的請求一致。可以參考既有離線檔的 key：326 個 `exact` key，以及 `calendar`、`feels`、`generated_at`。
2. 把 JSON gzip 壓縮後轉成 base64，寫進 `window.__SNAPSHOT_GZ__`；`__SNAPSHOT_AT__` 寫入匯出時間。
3. 內嵌 `styles.css` 和 JS 模組（importmap ＋ `data:` URL）。
4. 寫出 `output/dashboard_offline.html`。有指定 iCloud 目錄時，再複製一份到 iCloud。

**CLI 參數**

| 參數 | 預設 | 說明 |
|---|---|---|
| `--db-path` | `output/running_coach.db` | 同其他 CLI，明確指定資料庫 |
| `--out` | `output/dashboard_offline.html` | 本機輸出位置 |
| `--copy-to` | 讀環境變數 `RC_OFFLINE_EXPORT_DIR`；沒設就不複製 | iCloud Drive 裡的目標資料夾。**路徑不寫死在程式碼**，並把 key 加進 `.env.example`（本專案沒有 dotenv 載入器，要在 shell 設定或直接帶參數） |
| `--detail-months` | `0`（全部） | 只保留近 N 個月的單場明細 |
| `--no-gzip` | 關 | 除錯用，輸出未壓縮的 JSON |

**資安要求（CLAUDE.md §🔐 第 3 條）**
- 匯出檔只能有運動與生理指標。
- 寫出前要掃一次輸出內容：出現 token、cookie、email、`userProfileId` 樣式的字串就中止。
- 要有單元測試涵蓋這項檢查。

**離線模式下要隱藏或停用的功能**（需要連到本機伺服器才能用）
- 同步資料、分析近況、AI 教練面板、今日體感按鈕、自訂區間
- 自訂區間在快照裡沒有對應資料，只保留預先匯出的區間

**頁首**：固定顯示「資料截至 YYYY-MM-DD HH:MM」。行事曆的「今天」標記改用匯出日期，避免隔幾天打開時產生誤會。

---

## 3. iCloud Drive 設定（電腦端，一次性）

> ⚠️ 2026-10-24 檢查：這台電腦有 iCloud 程式在執行（`iCloudHome`），但使用者目錄下**找不到 `iCloudDrive` 資料夾**。可能是 iCloud for Windows 裡沒有勾選「iCloud 雲碟」。

1. 開啟 iCloud for Windows，勾選 **iCloud 雲碟**，套用後等它出現在檔案總管。資料夾通常是 `%USERPROFILE%\iCloudDrive`。
2. 在 iCloud 雲碟裡建立資料夾，例如 `RunningCoach`。
3. 在要執行匯出的 shell 設定環境變數（路徑換成實際位置）：
   ```powershell
   $env:RC_OFFLINE_EXPORT_DIR = "$env:USERPROFILE\iCloudDrive\RunningCoach"
   ```
   也可以每次帶 `--copy-to "<路徑>"` 參數。
4. 在 iPhone 的「檔案」App → iCloud 雲碟，確認看得到這個資料夾。

---

## 4. iPhone 捷徑（一次性建立）

**為什麼不能直接產生捷徑檔**：iOS 15 起只能匯入經過簽署的 `.shortcut`，而簽署需要在 macOS 上執行 `shortcuts sign`，Windows 做不到。所以捷徑要手動建一次，共四個動作。之後更新資料**不用再改捷徑**。

在「捷徑」App 新增捷徑，例如命名「跑步」，依序加入：

| # | 動作 | 設定 |
|---|---|---|
| 1 | **取得檔案** | 服務：iCloud 雲碟；路徑：`RunningCoach/dashboard_offline.html`；**關閉「顯示文件選擇器」**；開啟「找不到時發生錯誤」 |
| 2 | **以 base64 編碼** | 輸入：上一步的「檔案」；**「換行」設為「無」**（否則 data URL 會斷掉） |
| 3 | **文字** | `data:text/html;base64,` 後面接「已 Base64 編碼」變數，中間不要有空白或換行 |
| 4 | **打開** | 輸入：上一步的「文字」（和現有捷徑相同，會用 Safari 開啟） |

做法和現在的捷徑一樣，只是把第 1 步從「貼上的文字」換成「取得檔案」。可以直接改現有捷徑，刪掉原本貼 HTML 的那個文字動作。

想要一鍵開啟的話，可以把捷徑加到主畫面，或放進小工具。

---

## 5. 每次更新的流程

```powershell
# 電腦端（專案根目錄）。先同步最新資料（dashboard 的「同步資料」或 sync CLI）
& "C:\Users\cFu\anaconda3\envs\rc\python.exe" scripts/build_dashboard_offline.py --db-path output/running_coach.db
# 有設 RC_OFFLINE_EXPORT_DIR 時，會自動複製到 iCloud
```

1. 執行上面的指令，等 iCloud 同步完成（檔案總管的圖示變成已同步）。
2. iPhone 點捷徑，就會開啟最新版本。頁首會顯示資料時間。

---

## 6. 手機上的已知限制

| 項目 | 影響 | 處理方式 |
|---|---|---|
| iOS 版本 | importmap 和 `DecompressionStream` 需要 **iOS 16.4 以上**，太舊會整頁空白 | 只需確認 iOS 版本 |
| 設定不會被記住 | 每次從 `data:` URL 開啟都是新的來源，`localStorage` 不會保留，深淺色、首頁排序、趨勢開關都會回到預設 | 預設值要設定成適合手機閱讀；讀寫 localStorage 一律包 try/catch（`theme.js` 已有） |
| 線上功能 | 需要連到本機伺服器才能用 | 離線模式隱藏（見 §2） |
| 觸控 | 圖表提示偏向滑鼠設計 | 改成點一下顯示提示；趨勢頁的開關列可換行；數字卡在手機上改 2 欄 |
| 「今天」 | 匯出後隔天打開，日期會對不上 | 改用匯出日期，並在頁首標示資料時間 |
| 隱私 | 檔案含健康資料（不含帳密），存放在 Fu 本人的 iCloud | 不要把檔案或 iCloud 分享連結轉給他人 |

---

## 7. 驗收

- [ ] 匯出腳本有單元測試：用 fixture DB 產生輸出、gzip 能還原、資安掃描能擋下假 token、`--detail-months` 生效。
- [ ] 用 Playwright 以 `file://` 開啟產出的 HTML：五個 view 都能顯示，console 沒有錯誤；390px 寬度沒有橫向捲動。
- [ ] 檔案大小約 1.3 MB（全部歷史＋gzip）。
- [ ] 真機測試：Fu 用 iPhone 經 iCloud 加捷徑開啟成功。這項只能由 Fu 本人確認。
