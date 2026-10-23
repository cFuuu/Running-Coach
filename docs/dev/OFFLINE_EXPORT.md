# 離線 Dashboard 匯出與 iPhone 開啟流程

> **建立日期**：2026-10-24　**狀態**：🔵 W4a 匯出腳本、W4b 離線模式收尾已完成（2026-10-24），W4c 實機待做（TODO **N-30**）
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

**實作結果（W4a，2026-10-24）**
- **URL 清單唯一定義處**：`snapshot_requests()`（依 URL 原樣回應的 `exact`）與 `span_requests()`（`/api/calendar`、`/api/daily-feel`）。前端新增請求時在 `snapshot_requests()` 補一行，例如前期比較的 `custom:` 區間。range 清單取自 `/api/meta` 的 `ranges`，不另寫一份。
- **行事曆與體感**：起訖日由前端當場決定（任意月份、課表週、首頁近 30 天），所以各取一份涵蓋整段期間的資料，頁面內依 `start`／`end` 切片。期間是「最早活動或課表（有 `--detail-months` 時改為截止日）所在月曆格」到「今天所在月曆格／本課表週／最後一筆課表」。
- **單場明細**：行事曆上的全部活動（含非跑步，首頁近況與行事曆都能點進去）加上場次清單，依 `--detail-months` 截止日篩選。逐秒資料存成欄式 `{cols, rows}`，載入程式取用時還原。
- **前端打包**：JS 用 importmap ＋ `data:` URL，import 路徑改寫成 `@rc/js/<路徑>`，保留真正的 ES module 語意（`tooltip.js` 等會重新指定 export 的變數，`build_pace_calculator` 的函式作用域打包做不到）。只重用它的 import 解析工具，遇到動態 `import()`、re-export、多行 import 直接報錯。
- **接縫**：載入程式把自己掛成 `window.MOCK_API`（`isMock: true`、`isOffline: true`、`snapshotAt`），走 `core/api.js` 的 `useMock` 與 `coach.js` 的 `chatRequest`，前端程式碼不用改。寫入請求、同步狀態、AI 教練一律回錯誤或停用狀態。
- **不寫入資料庫**：`create_app()` 啟動時會套 schema 遷移並改寫分析紀錄狀態，所以來源資料庫以 sqlite `mode=ro` 開啟、用 backup API 複製到暫存目錄，API 只打在暫存副本上。學員檔（賽事名稱）預設取來源資料庫同資料夾的 `athlete_profile.md`，可用 `--profile-path` 指定。
- **資安掃描**：快照掃欄位名稱（`userProfileId`、`displayName`、token／cookie／authorization、email 等）、40 字以上的不透明 token 字串，以及資料庫 `athlete_source_identity.external_ref` 的帳號識別值；快照與頁面程式碼都掃 email、JWT、Bearer、Garmin／Cloudflare cookie 名稱。命中就以 exit code 2 中止、不寫檔，訊息只列規則名稱與次數，不回顯內容。
- **實測大小**（2026-10-24 真實資料庫，559 場明細）：gzip＋全部歷史 **2.05 MB**；gzip＋`--detail-months 12` **1.25 MB**；`--no-gzip` 7.31 MB。全部歷史比 §1 試算的 1.3 MB 大，主要是資料本身變多：快照原文 7.0 MB（明細 3.9 MB，`wellness-trend`／`training-load`／`garmin-cards` 的 1 年與全部區間合計約 1.7 MB，行事曆 0.35 MB），gzip 後 1.11 MB，base64 後 1.48 MB；JS 0.48 MB、CSS 0.13 MB。只留跑步明細也只能降到約 1.98 MB，差別不大。

**W4b 離線模式（2026-10-24）**
- **唯一判斷**：`core/api.js` 的 `isOfflineSnapshot()`（＝`window.MOCK_API.isOffline`）與 `offlineSnapshotInfo()`（`{at, today}`），其他地方不直接讀 `window.MOCK_API`。
- **隱藏線上功能**：`main.js` 離線時在 `<html>` 加 `.is-offline`，`index.html` 中需要伺服器的元素標 `data-online-only`（同步鈕兩顆、分析近況、AI 教練面板／開關／直條／FAB／遮罩、自訂區間、設定頁的 AI 教練區塊與立即同步），`styles.css` 一條規則隱藏。動態產生的今日體感區塊與行事曆「這場是課表」／「取消」按鈕在 JS 不產生（行事曆仍顯示「已確認為課表」／「待確認」狀態文字）。AI 教練不啟動、同步狀態不查詢，所以離線時沒有任何寫入或聊天請求。網址或偏好裡的 `custom:` 區間離線時不還原。後端的區網警語（`meta.notice`）離線不顯示。
- **資料截至**：側邊列的「開發用假資料」徽章改為「資料截至 YYYY-MM-DD HH:MM」；手機與平板看不到側邊列的 meta，頂列另顯示一份（`#offline-stamp`，≥1280px 隱藏）。載入程式的臨時浮動徽章已移除。
- **今天＝匯出日期**：匯出腳本寫入 `window.__SNAPSHOT_TODAY__`（快照的 `today`），掛成 `MOCK_API.today`；`format.js` 的 `todayISO()` 離線時回傳它，`garmin.js` 原本自己的 `todayIso()` 刪掉改用 `todayISO()`。行事曆的今天、連續週數、資料新鮮度本來就取自匯出當下的 API 回應，與此一致。
- **缺明細的場次**：訊息改為「離線檔未包含這場的明細（只保留 YYYY-MM-DD 之後的單場明細）」，訓練紀錄展開列與完整單場以說明框（非「讀取失敗」）顯示，完整單場的各圖收掉載入中、隔天恢復照常顯示。
- **請求稽核**（真實資料庫副本，390×844 與 1280 各一輪）：六個 view、五個區間、趨勢開關全部切換、行事曆往前 14 個月再往後 16 個月並點選日期、訓練紀錄展開 5 場與完整單場，共 169 個請求、111 個不同 URL，**快照答不出的請求 0 個**、console 0 錯誤、無橫向捲動、無可見的線上功能。W1～W3 新增的請求（前期比較的 `custom:`、課表週、連續週數的行事曆區段）都已被 `snapshot_requests()`／`span_requests()` 涵蓋，不需要新增 URL。

**CLI 參數**

| 參數 | 預設 | 說明 |
|---|---|---|
| `--db-path` | `output/running_coach.db` | 同其他 CLI，明確指定資料庫 |
| `--out` | `output/dashboard_offline.html` | 本機輸出位置 |
| `--copy-to` | 讀環境變數 `RC_OFFLINE_EXPORT_DIR`；沒設就不複製 | iCloud Drive 裡的目標資料夾。**路徑不寫死在程式碼**，並把 key 加進 `.env.example`（本專案沒有 dotenv 載入器，要在 shell 設定或直接帶參數） |
| `--detail-months` | `0`（全部） | 只保留近 N 個月的單場明細 |
| `--no-gzip` | 關 | 除錯用，輸出未壓縮的 JSON |
| `--athlete-id` | 第一位學員 | 同其他 CLI |
| `--profile-path` | 資料庫同資料夾的 `athlete_profile.md` | 學員檔（賽事名稱來源），與 dashboard server 相同預設 |

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

- [x] 匯出腳本有單元測試：用 fixture DB 產生輸出、gzip 能還原、資安掃描能擋下假 token、`--detail-months` 生效（`src/test/unit/test_build_dashboard_offline.py`）。
- [x] 用 Playwright 以 `file://` 開啟產出的 HTML：五個 view 都能顯示，console 沒有錯誤；390px 寬度沒有橫向捲動（W4a 以 fixture 與真實資料各驗一次；W4b 改完前端後以真實資料在 390 與 1280 再驗，見 §2「W4b 離線模式」）。
- [ ] 檔案大小約 1.3 MB（全部歷史＋gzip）。實測 2.05 MB，`--detail-months 12` 為 1.25 MB，見 §2「實測大小」，預設值待 Fu 決定。
- [ ] 真機測試：Fu 用 iPhone 經 iCloud 加捷徑開啟成功。這項只能由 Fu 本人確認。
