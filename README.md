# Running Coach

AI 跑步教練：評估訓練品質與身體數據，給出訓練建議與課表調整。資料來自 Garmin 手錶。

## 快速開始 (Quick Start)

1. 建立環境：`uv sync`（見下方「環境設定」）
2. Garmin 一次性認證（互動輸入帳密與 MFA，token 存到 `~/.garminconnect/`）：
   `.venv/Scripts/python.exe -m src.main.python.services.garmin_auth login`
3. 啟動 Dashboard：`scripts/dashboard.cmd`（區網存取、無身分驗證，勿對外網開放）

開發規則見 [CLAUDE.md](CLAUDE.md)；待辦與進度見 [docs/dev/TODO.md](docs/dev/TODO.md)。

## 環境設定 (Python / uv)

本專案使用 **uv** 管理環境：專案根目錄的 `.venv`（Python 3.12，**最低 3.12.16**——安全性更新，不得降版；下限定義於 `pyproject.toml` 的 `requires-python`）。

```bash
uv sync                                   # 依 uv.lock 建立／重建 .venv
uv add <套件>                              # 新增直接依賴
uv lock --upgrade-package <套件> && uv sync  # 升級單一套件
```

- `pyproject.toml` 宣告直接依賴、`uv.lock` 鎖定全部版本（版本唯一真實來源；兩者都進版控，`.venv/` 不進）。
- 執行一律用 `.venv/Scripts/python.exe`（Windows），例如：
  `.venv/Scripts/python.exe -m unittest discover -s src/test/unit -p "test_*.py" -v`

### 本機 Windows 注意事項
- 裸 `python` 指令會指向無關的 venv（`hermes-agent`），沒有 pip 也沒有本專案套件；PATH 上的 `pip` 則指向另一個 Python 3.10，`pip install X` 後 `python -c "import X"` 會莫名失敗。
- **uv 版本需 ≥ 0.13**（舊版內建 Python 清單最新只到 3.12.13，低於下限）。PATH 上 `hermes\bin` 的舊版 uv 排在 winget 版前面，新開的終端機打 `uv` 可能仍是舊版；用 `uv --version` 確認，或直接呼叫 `%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe`。

## 專案結構 (Project Structure)

```
src/main/
├── python/
│   ├── api/       # Dashboard 的 FastAPI 端點（app.py 為進入點）
│   ├── mcp/       # AI 教練的 MCP server
│   ├── models/    # SQLite schema 與資料庫存取
│   └── services/  # Garmin 匯入／同步、訓練負荷、課表、教練分析等業務邏輯
└── resources/
    └── assets/dashboard/  # Dashboard 前端靜態檔
src/test/unit/     # 單元測試
docs/dev/          # TODO、PLAN、Dashboard 契約等開發文件
scripts/           # 啟動與離線匯出腳本（說明見 docs/dev/SCRIPTS.md）
```
