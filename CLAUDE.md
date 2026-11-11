# CLAUDE.md - Running Coach

## ❌ 額外禁止事項
- **絕不** hardcode 應可設定的值 → 用設定檔／環境變數

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
- **認證階段**：獨立的一次性 CLI（`garmin_auth login`，指令見「🚀 常用指令」），
  互動式登入（含 MFA），成功後把 OAuth token 存到**專案目錄外**的 `~/.garminconnect/`。
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

## 🐍 Python 環境
- **不可用裸 `python`**（本機 Windows 上會指向無關的 `hermes-agent` venv），一律用專案 `.venv` 的直譯器 `.venv/Scripts/python.exe`（從專案根目錄執行，不靠 activate）
- Python 下限 3.12.16（`pyproject.toml` 的 `requires-python`，安全性更新），不得降版
- 環境安裝與 uv 用法見 [README.md](README.md)

## ✅ 本專案驗證方式
執行 `src/test/` 下的單元/整合測試；若有 CLI/API，實際呼叫一次確認回應正確。若前端功能有變動，用 `scripts/dashboard.cmd` 啟動 Dashboard 後在瀏覽器實際確認。

## 📋 需要協助？先看這裡

- 待辦清單、Phase 進度（進度的單一真實來源）：[docs/dev/TODO.md](docs/dev/TODO.md)
- 願景、架構、決策記錄、課表模板：[docs/dev/PLAN.md](docs/dev/PLAN.md)
- Dashboard API/前端契約：[docs/dev/DASHBOARD_TASKS.md](docs/dev/DASHBOARD_TASKS.md)
- 學員個人資料（不進版控）：`output/athlete_profile.md`、`output/training_plan.md`

## Agent skills

### Issue tracker

Issue 存於 GitHub Issues（cFuuu/Running-Coach），用 gh CLI 操作。見 `docs/agents/issue-tracker.md`。

### Triage labels

沿用五個標準標籤（needs-triage / needs-info / ready-for-agent / ready-for-human / wontfix）。見 `docs/agents/triage-labels.md`。

## 🚀 常用指令
```bash
RC=".venv/Scripts/python.exe"

# 跑測試
"$RC" -m unittest discover -s src/test/unit -p "test_*.py" -v

# 啟動 Dashboard API（區網存取，無身分驗證，勿對外網開放；就緒後自動開瀏覽器）
scripts/dashboard.cmd                  # 捷徑：同下行，額外參數原樣轉傳
"$RC" -m src.main.python.api.app --db-path output/running_coach.db --host 0.0.0.0 --port 8000

# Garmin 一次性認證（token 失效時請使用者親自執行；需互動輸入帳密，Claude 不代跑 login）
"$RC" -m src.main.python.services.garmin_auth status
"$RC" -m src.main.python.services.garmin_auth login
```

## 協作規範

### Communication
- **Language**: Output in **Traditional Chinese (Taiwan)/台灣繁體中文** only. Strictly forbid Simplified Chinese and Mainland terms (e.g., `质量`→`品質`, `默认`→`預設`, `支持`→`支援`).
- **Code & Terms**: Keep all technical terminology, commands, and code snippets in original English/raw format.
- **Code Comments**: Write code comments in Traditional Chinese.

### Git
- Commit format: `Type: summary` (capitalized Type, a space after the colon, no scope)
  - Type: `Feat` / `Fix` / `Docs` / `Style` / `Refactor` / `Perf` / `Test` / `Build` / `CI` / `Chore` / `Revert`
  - Subject in Traditional Chinese, imperative mood, ≤50 characters, no trailing period; with a task number, write `Type: Task X.Y.Z summary`
  - Body must list the actual changes per file (`- file path: what changed`), never just "see XXX.md"
- Commit after each completed task/stage; **never push unless I ask**
- Before starting work: `git status` to confirm clean, then `git pull --rebase` (multi-device sync)

### Prohibitions
- Without my approval: delete existing files, make major architectural changes, or significantly upgrade dependencies
- No duplicate implementations or `v2_` / `new_` / `enhanced_` style files → extend the existing code, keep a single source of truth
- Never write secrets (tokens, `.env` contents, internal addresses, credentials) into logs, commits, or the conversation

### Execution
- Surgical edits: touch only code relevant to the task — no drive-by refactoring or reformatting
- When a fix breaks something, revert to the last clean state and find the root cause — don't stack workarounds

### Verification
- Text/formatting/comment-only changes: reading it over is enough. Logic changes: actually run it after the task is done (run tests if they exist) — reading the code alone doesn't count as done
