"""Running Coach dashboard 的本機 API server（FastAPI + 靜態前端）。

用法：
    python -m src.main.python.api.app --db-path output/running_coach.db
    python -m src.main.python.api.app --db-path output/running_coach.db --host 0.0.0.0 --port 8000

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

不 hardcode 任何路徑或個人識別資訊：--db-path 必填，athlete 從資料庫查
（見 dashboard_queries.resolve_athlete_id）。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.main.python.api import routes_dashboard

# 前端靜態檔（Task B 的產出）。用相對於本檔案的路徑推導，
# 因此從任何工作目錄啟動都能找到。
DASHBOARD_ASSETS_DIR = (
    Path(__file__).resolve().parents[2] / "resources" / "assets" / "dashboard"
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def create_app(db_path: str, assets_dir: Path | None = None) -> FastAPI:
    """建立 FastAPI 應用程式。db_path 由呼叫端提供，不預設任何路徑。"""
    app = FastAPI(
        title="Running Coach Dashboard API",
        description=(
            "個人跑步訓練 dashboard 的本機 API。"
            "無身分驗證，僅供本機或受信任區網使用，切勿對外網開放。"
        ),
    )
    app.include_router(routes_dashboard.router)
    # 路由層宣告的預設 dependency 只是佔位，這裡換成真正連到 db_path 的版本
    app.dependency_overrides[routes_dashboard.get_conn] = (
        routes_dashboard.make_connection_dependency(db_path)
    )

    # 靜態前端掛在根路徑，必須在 /api 路由之後掛載才不會蓋掉它們。
    # Task B 尚未建立目錄時就跳過，讓後端仍可單獨啟動測 API。
    static_dir = assets_dir or DASHBOARD_ASSETS_DIR
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="dashboard")
    else:
        print(f"[warn] 找不到前端目錄 {static_dir}，僅提供 /api 端點")

    return app


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
    args = ap.parse_args()

    if not Path(args.db_path).exists():
        raise SystemExit(f"找不到資料庫檔案：{args.db_path}")

    if args.host not in ("127.0.0.1", "localhost"):
        print(
            f"[warn] 綁定於 {args.host}：同區網的任何裝置都能無密碼讀取這些個人健康資料。"
            "僅在受信任的區網使用，切勿對外網開放。"
        )

    import uvicorn

    uvicorn.run(create_app(args.db_path), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
