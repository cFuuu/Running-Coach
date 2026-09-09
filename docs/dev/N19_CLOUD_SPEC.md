# N-19 S1：藍色主題與 UI 殼調整（雲端 session spec）

> **建立**：2026-09-10　**對象**：雲端 Claude Code session
> **分支**：`feat/n19-ui-blue-theme`（從 `main` 開）
> **範圍**：本檔 §1～§4 四段，全部是純前端。§5～§7 留地端，**不要做**。

---

## 0. 先讀這些（不可跳過）

1. `CLAUDE.md`（根目錄）— 專案硬規則，特別是「絕不 hardcode 應可設定的值」「絕不複製貼上程式碼區塊」
2. `docs/dev/DASHBOARD_TASKS.md` **Task D**，尤其 **D.1 硬約束表**
3. 本檔全部

### 你的環境限制（重要）

- 雲端**沒有也不得有** `output/running_coach.db` 與 `ANTHROPIC_API_KEY`
- 要看畫面請用 `?mock=1`（`mock-data.js` 提供假資料）或 `src/test/unit/fixtures.py` 的 `build_fixture_db()`
- **不要**嘗試連 Garmin、不要碰 `~/.garminconnect/`、不要寫任何憑證相關程式碼

### D.1 硬約束中本次特別相關的幾條

| 約束 | 本次含義 |
|---|---|
| `charts.js` **零改動** | 圖表高度調整屬 §5（地端做），本次**完全不要動 `charts.js`** |
| `app.js`（狀態）／`charts.js`（純畫圖）解耦 | 新增的設定頁邏輯放 `app.js`；主題邏輯放 `theme.js` |
| 零建置、無 CDN | 不引入任何套件、不加 build step、icon 一律內嵌 SVG |
| RWD | body 不得橫向捲動；觸控目標 ≥44px |
| 圖表／卡片只呈現數據，不做文字解讀 | 設定頁不得出現任何訓練建議或結論句 |

---

## 1. 藍色主題（含深色模式適配）

### 1.1 背景：為什麼色號是給定的而不是讓你挑

地端已用 WCAG 相對亮度公式實測過候選色。**目測會出錯**——原先憑感覺選的
`--accent-border: #2e4a63` 實測對卡片底只有 **1.76:1**，遠低於非文字元件的
3:1 門檻，深色模式下邊框幾乎看不見。

**因此以下色號是量測結果，請原樣採用，不要自行「微調成更好看的藍」。**
若你有理由認為某個值不對，請在 PR 描述說明並附上你自己的對比度計算，
不要直接改。

### 1.2 淺色模式（`:root`，`styles.css` 約 L22）

```css
--accent: #2563a8;
--accent-soft: #e4eef8;
--accent-border: #bcd4ea;
--accent-text: #1b4a7a;
--on-accent: #fff;
--focus-ring: rgba(37, 99, 168, 0.4);
```

### 1.3 深色模式（`:root[data-theme="dark"]`，`styles.css` 約 L2283）

```css
--accent: #6aa9dd;        /* vs 頁面底 7.08:1、vs 卡片底 6.43:1 */
--accent-soft: #233648;   /* vs 頁面底 1.44:1（要看得出有上色） */
--accent-border: #4e7aa0; /* vs 卡片底 3.56:1（達非文字元件 3:1 門檻） */
--accent-text: #9ccbee;   /* vs 頁面底 10.36:1 */
--on-accent: #0b1219;     /* 深色 accent 是亮藍，上面必須配深色字：7.47:1 */
--focus-ring: rgba(106, 169, 221, 0.5);
```

> ⚠️ **深色 `--on-accent` 必須是深色。** 白字在 `#6aa9dd` 上只有 2.52:1，不可用。

### 1.4 三條不可放寬的底線

1. **正文與次要文字 token 完全不動** — `--text: #e4e8eb`、`--text-muted: #99a3ac`
   維持原值。不把正文染藍，才不會出現「一片藍看不清楚」。
2. **大面積背景維持中性深灰** — `--bg`、`--surface`、`--surface-sunken` **不動**。
   藍色深色主題最常見的失敗就是把背景整片染藍。
3. **圖表色系 `--c-*` 全部不動** — 那是為色盲友善與列印辨識度挑的，不是品牌色。
   特別注意：淺色 `--c-pace` 是綠色 `#2f6f4f`，**不要**為了「統一」把它改藍，
   否則會與已是藍色的 `--c-distance` 撞色，同一張圖上兩條線分不出來。
   `--activity-running: var(--c-pace)` 同理維持綠色。

### 1.5 必寫的測試（本次交付的重點之一）

新增 `src/test/unit/test_dashboard_theme_contrast.py`：

- 從 `src/main/resources/assets/dashboard/styles.css` **實際解析**出
  `:root` 與 `:root[data-theme="dark"]` 兩個區塊的 accent 相關 token
  （用正規表示式即可，不要另外複製一份色號到測試裡——那樣測試就永遠會過）
- 實作 WCAG 相對亮度與對比度公式（sRGB → 線性化 → 0.2126/0.7152/0.0722 加權）
- 斷言（深色模式）：
  - `--accent` vs `--bg` ≥ 4.5
  - `--accent-text` vs `--bg` ≥ 4.5
  - `--accent-border` vs `--surface` ≥ 3.0
  - `--on-accent` vs `--accent` ≥ 4.5
  - `--accent-soft` vs `--bg` ≥ 1.2（要能看出有上色）
- 淺色模式同樣斷言一次（底色換成 `--bg` / `--surface` 的淺色值）
- 另加一條：`--accent`（深色）≠ `--c-distance`（深色），防撞色

**這支測試的價值在於日後任何人調色時會直接失敗**，而不是等眼睛看出來。
請確保它是「讀 CSS 再算」，不是「比對硬編碼字串」。

---

## 2. 品牌名與右上角工具列

### 2.1 左上角品牌名

`index.html` 約 L44：`🏃 跑步儀表板` → **`Running Coach`**

- `🏃` emoji **保留**（側邊列縮到 72px 時文字隱藏，只剩圖示，拿掉會變空白）
- `<title>` 改成 `Running Coach 訓練儀表板`
- `app.js` 約 L1888 動態組的 `document.title` 一併改成 `<view 標題>｜Running Coach`

### 2.2 右上角工具列（新增）

在 `.content-topbar`（`index.html` 約 L126）最右側、`#coach-toggle` **之前**，
新增一組工具列 `.topbar-tools`，含三顆 icon 按鈕：

| 按鈕 | id | icon | 行為 |
|---|---|---|---|
| 資料同步 | `sync-btn` | 循環箭頭 | **本次只做 UI 外殼**，點擊呼叫 `window.DashboardApp.requestSync?.()`，該函式本次不存在也不報錯（§7 地端實作後端）。預設 `disabled` 並加 `title="資料同步（後端尚未接上）"` |
| 主題切換 | `theme-cycle-btn` | ☀/🌙/🖥 依狀態 | 循環 淺色 → 深色 → 跟隨系統 |
| 系統設定 | `settings-btn` | 齒輪 | 導向 `#/settings` |

要求：
- 全部用**內嵌 SVG**（風格對齊側邊列既有導覽 icon：`fill="none"`、
  `stroke="currentColor"`、`stroke-width="2"`、`stroke-linecap="round"`）
- 每顆 ≥44×44px、有 `title` 與 `aria-label`
- 主題鈕的 `aria-label` 要說明**下一個**狀態（例：「切換到深色模式」）

### 2.3 移除側邊列的舊主題切換

- 刪掉 `index.html` 約 L97～L102 的整組 `.theme-switch`（三顆 `☀ 🌙 🖥`）
- 一併刪掉 `styles.css` 約 L819 那條「三顆 44px 鈕塞不進 72px 欄要換行」的
  補丁規則 —— 問題來源消失了，補丁不該留著
- `.theme-switch` / `.theme-btn` 的樣式若已無人使用，一併刪除（不要留死 CSS）
- **`#sidebar-sync`（最後同步時間）留在側邊列**，不要動

### 2.4 不被 AI 面板遮擋（關鍵）

面板在三種寬度的形態不同，工具列的處理也不同：

| 寬度 | 面板形態 | 處理 |
|---|---|---|
| ≥1280px | grid 第三欄（`position: sticky`） | 面板本來就在內容欄右邊，**天然不重疊**，無需處理 |
| 768～1279px | `position: fixed` 右側抽屜（`z-index: 120`） | 面板開啟時（`body.coach-open`）給 `.content` 加 `padding-right`，把工具列推到面板左邊 |
| <768px | 全螢幕 sheet | 頂列整個看不到，這是正確行為，不處理 |

另外給 `.topbar-tools` 設 `z-index: 130`（高於面板的 120）作為保險。

實作 768～1279px 那段時，`padding-right` 的值要與面板寬度一致；面板寬度在
§3.2 會改成 `clamp()`，請用同一個 CSS 變數（例如 `--coach-panel-w`）驅動兩者，
**不要在兩個地方各寫一次數值**（違反「絕不複製貼上」）。

---

## 3. AI 教練面板 icon 重新設計

### 3.1 現況問題

開關是純文字字元 `—` 與 `×`（`index.html` 約 L246），收合後是一條 48px 寬、
`writing-mode: vertical-rl` 的直排文字灰長條。四種形態（直排文字／純文字鈕／
泡泡 FAB／文字 toggle）**各長各樣**，沒有統一的視覺語言。

### 3.2 要改成

**核心原則：「AI 教練」在四種形態下都用同一個「對話泡泡」符號。**

| 位置 | 現在 | 改成 |
|---|---|---|
| 面板標題列 `#coach-collapse` | 字元 `—` | SVG：指向右側的雙箭頭 `»`（語意＝收合到右邊），36×36px，hover 有 `--surface-sunken` 底色 |
| 面板標題列 `#coach-close` | 字元 `×` | SVG：線條 `×`，同尺寸與 hover 處理 |
| 收合直條 `.coach-rail` | 灰底直排文字 | 改成**圓形浮動按鈕**貼齊右緣（約 48px 圓、`--accent` 藍底、`--on-accent` 前景），內容為對話泡泡 icon。移除 `writing-mode` 直排文字 |
| 手機 FAB `.coach-fab` | 已是泡泡 SVG | icon 保留，統一成與上面同一份 path |
| 頂列 `#coach-toggle` | 純文字「AI 教練」 | 泡泡 icon ＋文字；`aria-expanded="true"` 時帶 `--accent-soft` 底色 |

- 對話泡泡 path 只寫一次，其餘引用（可用 `<symbol>` + `<use>`，或 JS 產生）
  —— **不要複製四份一樣的 SVG path**
- `.coach-rail` 改圓鈕後，原本 `width: 48px` 的 grid 欄位處理要一併調整，
  確認 ≥1280px 面板收合時內容欄能正確佔滿

### 3.3 面板寬度改 `clamp()`

`styles.css` 約 L894，≥1280px 的 `.coach-panel` 寬度：

```css
--coach-panel-w: clamp(360px, 40vw, 640px);
```

大螢幕（2K/4K）上面板更寬敞，小桌機不至於把內容欄擠爆。
grid 第三欄本來就是 `auto`，改寬度不會壓到內容欄的 `minmax(0, 1fr)`。
768～1279px 的抽屜寬度沿用同一變數（見 §2.4）。

---

## 4. 系統設定頁（新 view）

### 4.1 路由

側邊列與手機分頁列各新增一項「系統設定」（齒輪 icon），路由 `#/settings`。

走既有機制，不要另建架構：
- `app.js` 的 `VIEWS`（約 L53）加一筆 `settings: { title: '系統設定', hasRange: false }`
- `index.html` 加 `<section class="view" id="view-settings" hidden>`
- 側邊列 `.nav-link` 與 `.tabbar` 的 `.tab-link` 各加一項

> ⚠️ 手機底部分頁列目前是 `grid-template-columns: repeat(5, 1fr)`
> （`styles.css` 約 L698）。加到第 6 項會擠。**建議手機分頁列維持 5 項**
> （設定從右上角齒輪進入即可），只在側邊列加第 6 項。若你有更好的做法請說明。

### 4.2 頁面內容（四區）

**① 外觀**
- 主題三選一（淺色／深色／跟隨系統）—— 明確的 radio 或分段控制
- 「主題背景與系統同步」開關（即 `system` 選項的明確版本）
- 與右上角循環 icon **雙向連動**

**② AI 教練**
- 「面板預設開啟」開關 → 存 `settings.coachPanel`

**③ 今日頁指標**
- 本次**只做外殼與說明文字**，實際的指標勾選 UI 在 §6（地端）
- 放一段 placeholder 說明即可，不要做假的勾選清單

**④ 資料同步**
- 顯示「最後同步時間」（讀 `state.today.freshness`，與 `#sidebar-sync` 同源）
- 同步按鈕（與右上角同一個行為，`disabled` 直到 §7 接上後端）
- 一段說明：token 失效時請在終端機重跑一次性認證指令

### 4.3 設定的儲存

**一律走 `theme.js` 的 `Theme.getSettings()` / `Theme.saveSettings()`。**

該檔 docstring 已明講它是 localStorage key `rc-dashboard-settings` 的
**唯一讀寫入口**，之後的偏好都往同一個物件加欄位、不另開新 key。
設定頁正是它設計時預期的用途 —— **不要新增 localStorage key**。

- `SETTINGS_VERSION` 維持 `1`，加欄位不需升版（舊設定缺欄位走預設值）
- `theme.js` 的 `updateButtons()` 要能同時處理右上角循環 icon 與設定頁的三選一，
  兩邊狀態連動

---

## 5～7. 不在本次範圍（**不要做**）

| 段 | 內容 | 為什麼留地端 |
|---|---|---|
| §5 | 圖表尺寸、超寬斷點（1080P/2K/4K） | 「圖表太大」是視覺判斷，須在真實 4K 螢幕上確認 |
| §6 | 行事曆下方詳情、今日頁身體狀況卡、`get_today()` 擴充 | 需真實 DB 的欄位形狀。曾發生「fixture 與程式碼共用同一個錯誤猜測，測試全過但實際是錯的」 |
| §7 | `POST /api/sync` 後端 | 需 Garmin OAuth token，**絕不可進雲端環境** |

§2.2 的同步按鈕**只做 UI 外殼**（disabled），不要試著實作後端或去碰
`garmin_sync_runner`。

---

## 驗收標準

1. `"$RC" -m unittest discover -s src/test/unit -p "test_*.py"` 全部通過
   （目前基線 **704 項**，你會新增對比度測試，總數應 > 704）
2. 新增的對比度測試是「讀 CSS 再算」，不是比對硬編碼字串
3. `charts.js` 的 diff 為**空**
4. 未新增任何 localStorage key、未引入任何 CDN 或建置步驟
5. 若環境有 Playwright + chromium：`?mock=1` 下對
   **390 / 768 / 1280 / 1920 / 2560** 五個寬度、**淺色與深色各一輪**截圖，
   確認 body 無橫向捲動、右上角工具列未被 AI 面板遮擋
   （沒有 Playwright 就跳過，於 PR 註明，視覺交給地端）

## Commit 規範

見根目錄 `CLAUDE.md`：`Type: 中文摘要`（≤50 字、祈使句、無句點），
body 逐檔列出實際變更（`- 檔案路徑：改了什麼`），不可只寫「見 XXX.md」。
建議分 4 個 commit（對應 §1～§4），不要全部擠成一個。

**不要 push 到 `main`**，只推 `feat/n19-ui-blue-theme` 並開 PR。
