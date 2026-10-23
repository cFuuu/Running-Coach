"""Running Coach dashboard 的本機 API server（FastAPI + 靜態前端）。

用法：
    python -m src.main.python.api.app --db-path output/running_coach.db
    python -m src.main.python.api.app --db-path output/running_coach.db --host 0.0.0.0 --port 8000

啟動後會等 server 就緒再自動用預設瀏覽器開啟 dashboard；不想開就加 `--no-browser`。

⚠️ 安全性：這個服務**沒有任何身分驗證**，也沒有 HTTPS。
    預設綁 127.0.0.1，只有這台電腦連得到。改成 `--host 0.0.0.0` 之後，
    **同一個區網內的任何裝置**（訪客的手機、共用 Wi-Fi 上的陌生人、
    被入侵的 IoT 裝置）都可以直接讀取這裡的全部個人健康資料——
    心率、HRV、睡眠、體重、每天的行蹤時間，不需要密碼。

    這是**刻意的取捨、不是疏漏**：本專案現階段是單人自用工具，
    要在手機上看 dashboard 就必須綁 0.0.0.0，而為了這個情境去做
    帳號系統是過度設計。相對的，紅線是：

        **絕對不可以把這個 port 對外網開放**
        （不可做 port forwarding、不可放進 DMZ、不可用 ngrok 之類的隧道對外）。

    只在你信任的家用區網裡使用。要讓外部存取，必須先實作身分驗證與 HTTPS，
    不能只是改個 host 參數。

    ⚠️ 兩個例外**另加來源限制**，預設只接受本機——它們的代價不是「被看到」
    而是「被觸發」，故不套用上面那個「區網放行」的取捨：

        `/api/chat/*`  AI 教練。會花錢，也會把個人健康資料送出這台機器。
                       放行私有網段須設 `RC_CHAT_ALLOW_PRIVATE_NETWORK=1`。
        `/api/sync`    Garmin 同步。會打 Garmin 的 API，觸發過頻會撞上對方的
                       rate limit 並把帳號推向被鎖。
                       放行私有網段須設 `RC_SYNC_ALLOW_PRIVATE_NETWORK=1`。

    以上共用同一份來源判斷（見 api/client_access.py）。
    上面「不可對外網開放」的紅線不因此改變。

    唯一的區網可寫端點是 `/api/daily-feel`（每日體感）：只寫本機資料庫、
    不花錢不打外部 API，且主要在手機上使用，故沿用區網放行。

不 hardcode 任何路徑或個人識別資訊：--db-path 必填，athlete 從資料庫查
（見 dashboard_queries.resolve_athlete_id）。
"""

from __future__ import annotations

import argparse
import mimetypes
import socket
import threading
import time
import webbrowser
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.main.python.api import routes_analysis
from src.main.python.api import routes_chat
from src.main.python.api import routes_dashboard
from src.main.python.api import routes_sync
from src.main.python.models.db import get_connection
from src.main.python.services import analysis_report_store
from src.main.python.services.coach_analysis import AnalysisConfig
from src.main.python.services.coach_chat import ChatConfig

# 前端靜態檔（Task B 的產出）。用相對於本檔案的路徑推導，
# 因此從任何工作目錄啟動都能找到。
DASHBOARD_ASSETS_DIR = (
    Path(__file__).resolve().parents[2] / "resources" / "assets" / "dashboard"
)

# 前端是 ES module（<script type="module">），瀏覽器會嚴格檢查 .js 的 Content-Type。
# StaticFiles 依 mimetypes 判斷，而 Windows 上 mimetypes 會讀登錄檔，
# .js 可能被標成 text/plain 導致 module 被拒絕載入——在此明確註冊。
mimetypes.add_type("text/javascript", ".js")

# 學員檔（賽事名稱來源）預設與資料庫放在同一個資料夾，兩者都在不進版控的 output/
DEFAULT_PROFILE_FILENAME = "athlete_profile.md"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
# 等 server 開始接受連線的上限；逾時就放棄開瀏覽器（server 照常跑）
BROWSER_WAIT_TIMEOUT_SEC = 15.0
# Ctrl+C 後等既有連線（keep-alive、分析串流）自行結束的上限；逾時強制斷線，避免無限卡住
GRACEFUL_SHUTDOWN_TIMEOUT_SEC = 3


def create_app(
    db_path: str,
    assets_dir: Path | None = None,
    chat_config: ChatConfig | None = None,
    analysis_config: AnalysisConfig | None = None,
    profile_path: Path | None = None,
) -> FastAPI:
    """建立 FastAPI 應用程式。db_path 由呼叫端提供，不預設任何路徑。

    chat_config 省略時從環境變數讀（見 services/coach_chat.py 的設定表）。
    未設 ANTHROPIC_API_KEY 時 app 仍正常啟動，只有 /api/chat/* 回 503——
    dashboard 的其餘功能不該因為沒設聊天金鑰就整個掛掉。

    profile_path：學員檔（總覽個人最佳的賽事名稱來源），省略時取資料庫同資料夾的
    athlete_profile.md；檔案不存在不會報錯，賽事名稱退回活動名稱。
    """
    app = FastAPI(
        title="Running Coach Dashboard API",
        description=(
            "個人跑步訓練 dashboard 的本機 API。"
            "無身分驗證，僅供本機或受信任區網使用，切勿對外網開放。"
        ),
    )
    # 確保聊天用的兩張表存在：本進程原本從不執行 schema.sql（各請求用裸
    # sqlite3.connect），資料庫是 CLI 匯入器建的，新表不會自己長出來。
    # get_connection() 會套用 schema.sql 與欄位遷移，開一次關掉即可。
    conn = get_connection(db_path)
    # 上次 server 結束時還在跑的分析（子程序已隨舊進程消失）標成 error，不留 running
    analysis_report_store.mark_stale_running_as_error(conn)
    conn.close()

    app.include_router(routes_dashboard.router)
    app.include_router(routes_chat.router)
    app.include_router(routes_sync.router)
    app.include_router(routes_analysis.router)
    # 路由層宣告的預設 dependency 只是佔位，這裡換成真正連到 db_path 的版本
    app.dependency_overrides[routes_dashboard.get_conn] = (
        routes_dashboard.make_connection_dependency(db_path)
    )
    resolved_profile = profile_path or Path(db_path).with_name(DEFAULT_PROFILE_FILENAME)
    app.dependency_overrides[routes_dashboard.get_profile_path] = lambda: resolved_profile
    config = chat_config if chat_config is not None else ChatConfig.from_env()
    app.dependency_overrides[routes_chat.get_chat_config] = lambda: config
    a_config = analysis_config if analysis_config is not None else AnalysisConfig.from_env()
    app.dependency_overrides[routes_analysis.get_analysis_config] = lambda: a_config

    # 靜態前端掛在根路徑，必須在 /api 路由之後掛載才不會蓋掉它們。
    # Task B 尚未建立目錄時就跳過，讓後端仍可單獨啟動測 API。
    static_dir = assets_dir or DASHBOARD_ASSETS_DIR
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="dashboard")
    else:
        print(f"[warn] 找不到前端目錄 {static_dir}，僅提供 /api 端點")

    return app


def _local_host(host: str) -> str:
    """綁 0.0.0.0／:: 時本機仍要連 127.0.0.1，萬用位址本身不是可連線的目標。"""
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host


def _open_browser_when_ready(url: str, host: str, port: int) -> None:
    """背景輪詢 port，等 uvicorn 真的在聽了才開瀏覽器，避免開到連線失敗頁。"""
    deadline = time.monotonic() + BROWSER_WAIT_TIMEOUT_SEC
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.3)
    print(f"[warn] {BROWSER_WAIT_TIMEOUT_SEC:.0f} 秒內 server 未就緒，未自動開啟瀏覽器；請手動開 {url}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="啟動 Running Coach dashboard 的本機 API server。"
    )
    ap.add_argument("--db-path", required=True, help="SQLite 資料庫路徑")
    ap.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=(
            f"綁定位址，預設 {DEFAULT_HOST}（僅本機）。"
            "改成 0.0.0.0 可讓同區網裝置存取，但服務無身分驗證，切勿對外網開放。"
        ),
    )
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"連接埠，預設 {DEFAULT_PORT}")
    ap.add_argument(
        "--profile-path",
        type=Path,
        default=None,
        help=f"學員檔路徑（賽事名稱來源），預設為資料庫同資料夾的 {DEFAULT_PROFILE_FILENAME}",
    )
    ap.add_argument(
        "--no-browser",
        action="store_true",
        help="啟動後不要自動開啟瀏覽器（預設會在 server 就緒後開啟 dashboard）",
    )
    args = ap.parse_args()

    if not Path(args.db_path).exists():
        raise SystemExit(f"找不到資料庫檔案：{args.db_path}")

    if args.host not in ("127.0.0.1", "localhost"):
        print(
            f"[warn] 綁定於 {args.host}：同區網的任何裝置都能無密碼讀取這些個人健康資料。"
            "僅在受信任的區網使用，切勿對外網開放。"
        )

    import uvicorn

    local_host = _local_host(args.host)
    url = f"http://{local_host}:{args.port}/"
    print(f"Dashboard：{url}")
    if not args.no_browser:
        threading.Thread(
            target=_open_browser_when_ready,
            args=(url, local_host, args.port),
            daemon=True,
        ).start()

    uvicorn.run(
        create_app(args.db_path, profile_path=args.profile_path),
        host=args.host,
        port=args.port,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_TIMEOUT_SEC,
    )


if __name__ == "__main__":
    main()
