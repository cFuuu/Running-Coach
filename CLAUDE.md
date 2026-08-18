# CLAUDE.md - Running Coach

> **文件版本**：1.1
> **最後更新**：2026-08-19
> **專案**：Running Coach
> **簡介**：AI 跑步教練 App

本檔案提供本專案的**特定**指引。一般工作習慣——commit 訊息規範、根目錄/重複檔案/命名禁令、工具選用（Grep/Glob/Read）、先讀再改、>30 秒操作用 Task Agent、3+ 步驟任務用 TodoWrite、技術債預防順序、多裝置協作、文字與邏輯變更的驗證原則——已定義在使用者的全域 `CLAUDE.md` 並自動載入每個專案，此處**不重複**。以下只列 Running Coach 專屬內容。

## ❌ 額外禁止事項（全域未涵蓋部分）
- **絕不** hardcode 應可設定的值 → 用設定檔／環境變數
- **絕不**複製貼上程式碼區塊 → 抽成共用工具/函式

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

## 🎯 開發進度（2026-08-19）

| Phase | 狀態 |
|---|---|
| Phase 0 需求確認 | ✅ 完成 |
| Phase 1A/1C 歷史資料匯入＋統一資料模型 | ✅ 完成 |
| Phase 1E FIT 解析 | ✅ 完成 |
| Phase 1.5 本機 Dashboard | 🔵 進行中（Phase 6 行事曆檢視未開始）|
| Phase 1B 即時同步 | ⏸️ 延後（Garmin 已封鎖自動化登入）|
| Phase 2 訓練科學規則引擎 | ⏸️ 未開始，設計已於 2026-08-18 grill 定案 |
| Phase 3 AI Coach（★ 專案主目標）| ⏸️ 未開始 |

詳細清單見 [docs/dev/TODO.md](docs/dev/TODO.md) 的進度總覽表。

## 📋 需要協助？先看這裡

- 待辦清單、Phase 進度：[docs/dev/TODO.md](docs/dev/TODO.md)
- 願景、架構、決策記錄、課表模板：[docs/dev/PLAN.md](docs/dev/PLAN.md)
- Dashboard API/前端契約：[docs/dev/DASHBOARD_TASKS.md](docs/dev/DASHBOARD_TASKS.md)
- 學員個人資料（不進版控）：`output/athlete_profile.md`、`output/current_training_plan.md`、`output/training_log.md`

## 🚀 常用指令
```bash
RC="C:/Users/cFu/anaconda3/envs/rc/python.exe"

# 跑測試
"$RC" -m unittest discover -s src/test/unit -p "test_*.py" -v

# 啟動 Dashboard API（區網存取，無身分驗證，勿對外網開放）
"$RC" -m src.main.python.api.app --db-path output/running_coach.db --host 0.0.0.0 --port 8000
```
