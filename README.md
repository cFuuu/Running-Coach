# Running Coach

## 快速開始 (Quick Start)

1. **首先閱讀 AGENTS.md** - 包含 AIAgent 的基本規則
2. 在開始任何工作之前，遵循任務前合規檢查表
3. 在 `src/main/python/` 下使用正確的模組結構
4. 在每個已完成的功能之後提交

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

標準專案結構，具有模組化組織：

```
src/main/python/
├── core/      # 核心業務邏輯
├── utils/     # 工具函數
├── models/    # 資料模型
├── services/  # 服務層
└── api/       # API 端點
```
