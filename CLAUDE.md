# CLAUDE.md - Running Coach

> **文件版本**：1.2
> **最後更新**：2026-09-01
> **專案**：Running Coach
> **簡介**：AI 跑步教練 App

本檔案提供本專案的**特定**指引。一般工作習慣——commit 訊息規範、根目錄/重複檔案/命名禁令、工具選用（Grep/Glob/Read）、先讀再改、>30 秒操作用 Task Agent、3+ 步驟任務用 TodoWrite、技術債預防順序、多裝置協作、文字與邏輯變更的驗證原則——已定義在使用者的全域 `CLAUDE.md` 並自動載入每個專案，此處**不重複**。以下只列 Running Coach 專屬內容。

## ❌ 額外禁止事項（全域未涵蓋部分）
- **絕不** hardcode 應可設定的值 → 用設定檔／環境變數
- **絕不**複製貼上程式碼區塊 → 抽成共用工具/函式

## 🔐 憑證與資安（Garmin 帳號存取，不可妥協）

> 本專案會存取 Fu 本人的 Garmin Connect 帳號。帳密外洩是本專案唯一的
> **災難級**風險（帳號含多年健康資料，且 Garmin 帳號常與其他服務共用密碼），
> 而它 100% 可預防。以下每一條都是硬性要求，不是建議。

### 1. 帳密永不進版控、永不進程式碼
- 帳號密碼**只**能存在於本機 `.env`（已列入 `.gitignore`），或由使用者於
  互動式 CLI 當場輸入（用 `getpass`，不回顯）。
- 程式碼、設定檔、測試 fixture、文件範例中**一律不得出現真實帳密**。
- `.env.example` 只放 key 不放 value，且必須進版控供他人 setup 參考。

### 2. 認證與執行解耦（防重複觸發 Garmin 反爬蟲）
- **認證階段**：獨立的一次性 CLI，互動式登入（含 MFA），成功後把 OAuth
  token 存到**專案目錄外**的 `~/.garminconnect/`。
- **執行階段**：匯入器與 MCP server **只**允許讀 token，**嚴禁**執行帳密登入。
  token 失效時應拋出「請重跑一次性認證指令」的明確錯誤，而非自動用帳密重登。
- 理由：Garmin 的 Cloudflare 會對 `/sso/signin` 做 rate limit，自動重登會
  把帳號推向被鎖。

### 3. 憑證絕不進入 log、例外訊息、或 LLM context
- 不得 `print`／`log` 整個 config dict、request headers、cookie jar
  ——token 會藏在 header 裡。
- 例外處理時不可 dump request/response 原文。
- MCP tool 的回傳值與錯誤訊息只能有運動與生理指標，**不得含 token、cookie、
  email、userProfileId**。
- 這條同時涵蓋與 AI 的對話：不要把 `.env` 內容或 token 檔案讀出來貼進對話。

### 4. `.gitignore` 必須涵蓋
`.env`、`.env.*`（除 `.env.example`）、`.garminconnect/`、`*.token`、
`session.json`、`*.session.json`。**新增任何憑證類檔案前先確認 gitignore 有蓋到。**

### 5. Windows 平台注意
`os.chmod(path, 0o700)` 在 Windows 上**幾乎是 no-op**（只影響唯讀位元，
不設 ACL）。本專案主力環境是 Windows，故 token 目錄的保護實際依賴
「放在使用者 profile 內」的預設隔離；若要真正限縮權限需用 `icacls`。
**不要在文件或註解裡宣稱 `0700` 在此平台有效。**

### 6. commit 前自檢
`git status` 確認沒有 `.env`／token 檔被 track。涉及憑證的變更，
commit 前額外掃一次 diff。

## 🐙 本專案 GitHub 設定
**狀態**：已連接 — `origin` 指向 [cFuuu/Running-Coach](https://github.com/cFuuu/Running-Coach)。

### Auto-Push 機制
- 目前**未啟用**自動 push：commit 為必要動作，但 push 時機由使用者決定（見全域「Push Timing」規則），**不主動 push**
- 若未來要啟用：於 `.git/hooks/post-commit` 加入 `git push origin main`

### 常用指令
```bash
gh auth status && git remote -v      # 檢查連接狀態
git push origin main
gh repo view
```

## 🐍 Python 環境（重要）

本專案使用專屬 conda 環境 **`rc`**（Python 3.12）。

**不可用裸 `python` 指令**——這台機器上它會指向無關的 venv（`hermes-agent`），沒有 pip 也沒有本專案任何套件。PATH 上的 `pip` 也指向另一個 Python 3.10 安裝，`pip install X` 後 `python -c "import X"` 會莫名失敗。

一律用絕對路徑呼叫該環境的直譯器：

```bash
# Bash tool
RC="C:/Users/cFu/anaconda3/envs/rc/python.exe"
"$RC" -m unittest discover -s src/test/unit -p "test_*.py" -v
"$RC" -m src.main.python.services.garmin_import_runner --help
```

```powershell
# PowerShell tool（conda 不在 PATH 上，同樣用絕對路徑呼叫）
& "C:\Users\cFu\anaconda3\envs\rc\python.exe" --version
& "C:\Users\cFu\anaconda3\Scripts\conda.exe" env list
```

`conda activate rc` 在使用者自己的互動式終端機有效，但**跨工具呼叫不會保留**——每次 Bash/PowerShell 呼叫都是全新 shell，activate 不會持續生效，改用絕對路徑。

套件依賴：`requirements.txt`（版本唯一真實來源）與 `environment.yml`（conda 包裝，內部用 pip 安裝 requirements.txt）。重建環境：`conda env create -f environment.yml`。

## ✅ 本專案驗證方式
執行 `src/test/` 下的單元/整合測試；若有 CLI/API，實際呼叫一次確認回應正確。若前端功能有變動，啟動 dev server 並在瀏覽器實際確認，不能只憑閱讀程式碼判斷。

```bash
RC="C:/Users/cFu/anaconda3/envs/rc/python.exe"
"$RC" -m unittest discover -s src/test/unit -p "test_*.py" -v
```

## 🏗️ 專案概況

打造一套 AI 跑步教練，能評估「訓練品質」與「身體數據」，並據此給出訓練建議與課表調整。Garmin 手錶資料串接是支撐主目標的工作項目之一，不是專案本身的目的。

願景、架構、決策記錄、課表模板等穩定內容見 [docs/dev/PLAN.md](docs/dev/PLAN.md)；目前待辦與 Phase 進度見 [docs/dev/TODO.md](docs/dev/TODO.md)。

## 🎯 開發進度（2026-09-07）

| Phase | 狀態 |
|---|---|
| Phase 0 需求確認 | ✅ 完成 |
| Phase 1A/1C 歷史資料匯入＋統一資料模型 | ✅ 完成 |
| Phase 1E FIT 解析 | ✅ 完成 |
| Phase 1B 即時同步（Garmin 匯入器）| ✅ 主體完成（剩 1B-6 Strava 備援層）|
| Phase 1.5 本機 Dashboard | ✅ 主體完成（今日卡／行事曆／訓練負荷面板已上，剩手機實機測試）|
| Phase 2 訓練科學規則引擎 | 🔵 進行中（⚠️ 課表週跑量曲線倒掛，見 TODO N-12）|
| Phase 3 AI Coach（★ 專案主目標）| 🔵 進行中（14 個 MCP tools 與行為約束層已可用；外部課表與軟性輸入已落地。**剩 N-7 課表調整粒度、N-8 跨對話記憶兩個缺口，皆需先決策**）|

⚠️ **此表為粗略概況，會落後於實際進度。**
**進度的單一真實來源是 [docs/dev/TODO.md](docs/dev/TODO.md) 的進度總覽表**，
兩者不一致時一律以 TODO.md 為準。

## 📋 需要協助？先看這裡

- 待辦清單、Phase 進度：[docs/dev/TODO.md](docs/dev/TODO.md)
- 願景、架構、決策記錄、課表模板：[docs/dev/PLAN.md](docs/dev/PLAN.md)
- Dashboard API/前端契約：[docs/dev/DASHBOARD_TASKS.md](docs/dev/DASHBOARD_TASKS.md)
- 學員個人資料（不進版控）：`output/athlete_profile.md`、`output/current_training_plan.md`、`output/training_log.md`

## Agent skills

### Issue tracker

Issue 存於 GitHub Issues（cFuuu/Running-Coach），用 gh CLI 操作。見 `docs/agents/issue-tracker.md`。

### Triage labels

沿用五個標準標籤（needs-triage / needs-info / ready-for-agent / ready-for-human / wontfix）。見 `docs/agents/triage-labels.md`。

### Domain docs

單一 context：`CONTEXT.md` + `docs/adr/` 於根目錄。見 `docs/agents/domain.md`。

## 🚀 常用指令
```bash
RC="C:/Users/cFu/anaconda3/envs/rc/python.exe"

# 跑測試
"$RC" -m unittest discover -s src/test/unit -p "test_*.py" -v

# 啟動 Dashboard API（區網存取，無身分驗證，勿對外網開放）
"$RC" -m src.main.python.api.app --db-path output/running_coach.db --host 0.0.0.0 --port 8000
```
