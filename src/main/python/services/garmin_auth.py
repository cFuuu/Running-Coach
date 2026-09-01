"""Garmin Connect 認證：一次性互動式登入，與執行階段完全解耦。

用法（**由使用者手動執行一次**，需要互動輸入）：
    python -m src.main.python.services.garmin_auth login
    python -m src.main.python.services.garmin_auth status
    python -m src.main.python.services.garmin_auth logout

## 為何認證與執行必須分開（CLAUDE.md §🔐 第 2 條）

本模組是**唯一**允許以帳密登入 Garmin 的地方。匯入器與 MCP server 一律
只能呼叫 `load_client()` 讀既有 token；token 失效時 `load_client()` 會拋出
`GarminAuthRequired`，要求使用者重跑一次 `login`，**不會**自動用帳密重登。

這不是保守的預防措施，而是有實測依據的：2026-09-01 驗證時，單次帳密登入
會讓 `garminconnect` 依序嘗試多種策略——`mobile+cffi`（TLS
`WRONG_VERSION_NUMBER` 失敗）→ `mobile+requests`（**429 IP rate limited**）
→ 再換策略才成功。也就是**一次登入等於連打數次 SSO**。若執行階段允許
自動重登，失敗重試會快速累積 429，把帳號推向被鎖。改為只讀 token 後
重跑，零登入請求、零錯誤。

## 憑證處理

- 帳密以 `getpass` 當場輸入、不回顯，只存在記憶體，**不寫檔、不進 log**
- 登入成功後 token 由 `garminconnect` 存於 `~/.garminconnect/`（**專案目錄外**，
  預設路徑，可用環境變數 `GARMINTOKENS` 覆寫）
- 本模組不 print token、cookie、完整 email 或 userProfileId

⚠️ Windows 平台：`os.chmod(path, 0o700)` 幾乎是 no-op（只影響唯讀位元、
不設 ACL）。token 目錄的保護實際依賴「位於使用者 profile 內」的預設隔離。
本模組因此**不宣稱**已將權限限縮，僅在 `status` 提示此事實。
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

# token 儲存位置：預設 ~/.garminconnect，可用 GARMINTOKENS 覆寫（不 hardcode）。
DEFAULT_TOKENSTORE = "~/.garminconnect"


class GarminAuthError(Exception):
    """Garmin 認證相關錯誤的基底類別。"""


class GarminAuthRequired(GarminAuthError):
    """尚未認證，或 token 已失效——需要使用者手動重跑一次性登入。

    執行階段（匯入器／MCP）攔到此例外時，應把訊息原樣呈現給使用者，
    **不可**自行改用帳密登入重試。
    """


def get_tokenstore(tokenstore: str | None = None) -> str:
    """決定 token 儲存路徑：參數 > 環境變數 GARMINTOKENS > 預設值。"""
    return tokenstore or os.getenv("GARMINTOKENS") or DEFAULT_TOKENSTORE


def _mask_email(email: str) -> str:
    """遮蔽 email，只留首字與網域——避免完整帳號進入輸出或 log。"""
    if "@" not in email:
        return email[:1] + "***"
    user, _, domain = email.partition("@")
    return f"{user[:1]}***@{domain}"


def _import_garmin():
    """延遲載入 garminconnect，讓未安裝時給出可行動的錯誤訊息。"""
    try:
        from garminconnect import Garmin
    except ImportError as e:
        raise GarminAuthError(
            "未安裝 garminconnect。請執行："
            'pip install "garminconnect>=0.3.11"'
        ) from e
    return Garmin


def load_client(tokenstore: str | None = None):
    """以既有 token 建立已認證的 Garmin client（**執行階段唯一入口**）。

    ⚠️ 本函式**不會**、也**不得**以帳密登入。token 不存在或已失效時拋出
    `GarminAuthRequired`，由使用者手動重跑 `login`。

    Raises:
        GarminAuthRequired: token 不存在或已失效。
        GarminAuthError: garminconnect 未安裝。
    """
    Garmin = _import_garmin()
    store = get_tokenstore(tokenstore)
    path = Path(store).expanduser()

    if not path.exists():
        raise GarminAuthRequired(
            f"尚未認證（找不到 {path}）。請先執行一次：\n"
            "    python -m src.main.python.services.garmin_auth login"
        )

    try:
        client = Garmin()
        client.login(store)
        return client
    except Exception as e:
        # 不把原始例外訊息直接外流——它可能含 request 細節。
        raise GarminAuthRequired(
            f"token 載入失敗（{type(e).__name__}），可能已過期。請重新執行一次：\n"
            "    python -m src.main.python.services.garmin_auth login\n"
            "⚠️ 請勿連續重試，Garmin 對登入端點有 rate limit。"
        ) from None


def login(tokenstore: str | None = None) -> int:
    """互動式登入並儲存 token。**只應由使用者手動執行。**"""
    Garmin = _import_garmin()
    store = get_tokenstore(tokenstore)

    print("Garmin Connect 一次性認證")
    print("（密碼輸入不會顯示；帳密只存在記憶體，不寫檔、不進 log）")
    print()

    email = input("Email: ").strip()
    if not email:
        print("[!] Email 不可為空")
        return 1
    password = getpass.getpass("Password: ")
    if not password:
        print("[!] Password 不可為空")
        return 1

    def prompt_mfa() -> str:
        return input("MFA 驗證碼: ").strip()

    try:
        client = Garmin(email, password, prompt_mfa=prompt_mfa)
        needs_mfa, _ = client.login(store)
        if needs_mfa:
            print("[i] 需要 MFA，已透過提示處理")
    except Exception as e:
        print(f"[!] 登入失敗：{type(e).__name__}")
        print()
        print("可能原因：")
        print("  - 帳密錯誤，或 MFA 驗證碼已過期")
        print("  - 觸發 Garmin 的 IP rate limit（429）")
        print("  - Garmin 改變了登入機制（結構性失效，需評估備援來源）")
        print()
        print("⚠️ 請勿連續重試——單次登入即會嘗試多種策略、連打數次 SSO，")
        print("   連續重試會快速累積 429。請等待數分鐘後再試。")
        return 1
    finally:
        del password  # 盡早移除記憶體中的明文密碼

    path = Path(store).expanduser()
    print()
    print(f"[OK] 登入成功（帳號 {_mask_email(email)}）")
    print(f"[OK] token 已儲存於 {path}（內容不顯示）")
    print()
    print("之後匯入器與 MCP 會自動讀取此 token，不需再輸入帳密。")
    print("token 會自動 refresh；若日後提示失效，再重跑本指令一次即可。")
    return 0


def status(tokenstore: str | None = None) -> int:
    """檢查認證狀態（不發出登入請求）。"""
    store = get_tokenstore(tokenstore)
    path = Path(store).expanduser()

    print(f"token 路徑：{path}")
    if not path.exists():
        print("狀態：❌ 尚未認證")
        print()
        print("請執行：python -m src.main.python.services.garmin_auth login")
        return 1

    files = sorted(f.name for f in path.iterdir() if f.is_file())
    print(f"檔案：{files}（內容不顯示）")

    try:
        load_client(store)
    except GarminAuthRequired as e:
        print(f"狀態：❌ token 存在但無法使用\n{e}")
        return 1
    except GarminAuthError as e:
        print(f"狀態：⚠️ 無法檢查——{e}")
        return 1

    print("狀態：✅ 已認證，token 可用")
    if sys.platform == "win32":
        print()
        print("⚠️ Windows 平台：chmod 0700 對目錄權限幾乎無效（不設 ACL）。")
        print("   此 token 的保護依賴其位於使用者 profile 內的預設隔離。")
    return 0


def logout(tokenstore: str | None = None) -> int:
    """清除本機 token（不會撤銷 Garmin 端已發出的 token）。"""
    Garmin = _import_garmin()
    store = get_tokenstore(tokenstore)
    path = Path(store).expanduser()

    if not path.exists():
        print(f"[i] token 目錄不存在，無需清除：{path}")
        return 0

    try:
        Garmin().logout(store)
        print(f"[OK] 已清除本機 token：{path}")
        print("[i] 注意：這不會撤銷 Garmin 端已發出的 token。")
        return 0
    except Exception as e:
        print(f"[!] 清除失敗：{type(e).__name__}")
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Garmin Connect 一次性認證。這是專案中唯一允許帳密登入的入口——"
            "匯入器與 MCP 一律只讀 token（見 CLAUDE.md §🔐 第 2 條）。"
        )
    )
    ap.add_argument(
        "command",
        choices=["login", "status", "logout"],
        help="login：互動式登入並存 token；status：檢查狀態；logout：清除本機 token",
    )
    ap.add_argument(
        "--tokenstore",
        default=None,
        help=f"token 儲存路徑（預設：環境變數 GARMINTOKENS，或 {DEFAULT_TOKENSTORE}）",
    )
    args = ap.parse_args()

    try:
        return {"login": login, "status": status, "logout": logout}[args.command](
            args.tokenstore
        )
    except GarminAuthError as e:
        print(f"[!] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
