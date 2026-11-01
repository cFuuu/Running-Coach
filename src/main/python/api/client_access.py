"""端點的來源位址限制——`/api/chat/*` 與 `/api/sync` 共用的那一份判斷。

【為什麼要有這一層】
    dashboard 其餘端點採「區網無驗證」的取捨（見 `app.py` docstring）：
    唯讀的健康數據，為了手機看得到而放行同一個區網。但有兩類端點不適用
    那個取捨，因為它們的代價不是「被看到」而是「被觸發」：

      `/api/chat/*`  會花錢，且把個人健康資料送出這台機器
      `/api/sync`    會打 Garmin 的 API，觸發過頻會撞上對方的 rate limit
                     並把帳號推向被鎖（見 CLAUDE.md 憑證規範第 2 條）

    兩者因此**預設只接受本機**，要放行私有網段必須明確設環境變數。

【為什麼抽成共用模組】
    這段判斷原本只在 `routes_chat.py`。`/api/sync` 需要一模一樣的規則，
    複製第二份就會有「改了一邊忘了另一邊」的漂移風險——而漂移的後果是
    某個端點意外對整個區網開放。故抽成單一實作，兩邊都引用這裡。

    刻意收 `allow_private_network: bool` 而非某個 config 物件：這一層只關心
    「私有網段放不放行」，不該知道聊天或同步各自的設定長什麼樣。

【來源位址擋不住的兩種請求：`LocalRequestGuard`】
    瀏覽器在這台電腦上開的任何網頁，連線來源都是 127.0.0.1，上面的來源判斷
    分不出「Fu 自己的 dashboard」與「惡意網頁」（2026-11-01 資安稽核 NV-1／NV-2）：

      跨站請求（CSRF）  惡意網頁送一個空表單到 /api/sync，就觸發真的 Garmin 同步
      DNS rebinding     惡意網域改解析到 127.0.0.1 後與 API 同源，可讀全部資料

    故整個 app 外面再包一層：
      - **Host 必須是 IP 字面值、localhost 或 `RC_ALLOWED_HOSTS` 列的名稱**。
        rebinding 一定經過網域名稱，IP 直連（手機用區網 IP、Tailscale IP）不受影響。
      - **寫入類請求（非 GET／HEAD／OPTIONS）帶了 Origin 就必須與 Host 同源**，
        `Sec-Fetch-Site: cross-site` 一律拒絕。沒帶 Origin 的是 curl 之類的非瀏覽器
        客戶端，不在這條威脅模型裡，放行。
"""

from __future__ import annotations

import ipaddress
import json
import os
from collections.abc import Iterable
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

# Tailscale 的 CGNAT 網段。Python 3.12 的 ip_address.is_private 對這一段回 False
# （它不在 RFC1918 私有位址裡），所以必須明列，否則從 Tailscale 連進來會被擋。
# Tailscale 的 IPv6 位址落在 fd7a:115c:a1e0::/48，屬 unique-local，is_private
# 本來就為 True，不必另外列。
TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def env_flag(name: str) -> bool:
    """環境變數的布林解析。空值或無法辨識的字串一律視為 False（fail closed）。"""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def is_client_allowed(request: Request, allow_private_network: bool) -> bool:
    """判斷這個請求的來源位址可不可以用受限端點。

    **Fail closed**：位址解析不出來（沒有 client、或不是合法 IP）一律拒絕。
    Starlette 的 `TestClient` 預設 host 是字串 `"testclient"`，因此測試必須傳
    `client=("127.0.0.1", 1)` 才會被放行——這正是這條規則該有的行為。

    uvicorn 只信任來自 127.0.0.1 的 `X-Forwarded-For`，區網裝置無法靠偽造
    標頭把自己講成本機。
    """
    client = request.client
    if client is None or not client.host:
        return False
    try:
        ip = ipaddress.ip_address(client.host)
    except ValueError:
        return False

    if ip.is_loopback:  # 涵蓋 127.0.0.0/8 與 ::1
        return True
    if not allow_private_network:
        return False
    return ip.is_private or ip in TAILSCALE_CGNAT


def require_client(request: Request, allow_private_network: bool, detail: str) -> None:
    """不允許就丟 403。`detail` 由呼叫端提供，因為兩個端點要說的話不同
    （各自的放行環境變數名稱不一樣）。"""
    if not is_client_allowed(request, allow_private_network):
        raise HTTPException(status_code=403, detail=detail)


# --- Host／Origin 守門（見模組說明【來源位址擋不住的兩種請求】） ---

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ALLOWED_HOSTS_ENV = "RC_ALLOWED_HOSTS"
INVALID_HOST_DETAIL = (
    f"不接受這個 Host。用 IP 或 localhost 連線；要用主機名稱請加進 {ALLOWED_HOSTS_ENV}。"
)
CROSS_SITE_DETAIL = "拒絕來自其他網站的寫入請求。"


def allowed_host_names_from_env() -> frozenset[str]:
    """`RC_ALLOWED_HOSTS`（逗號分隔）額外放行的主機名稱，例如 Tailscale MagicDNS 名稱。"""
    raw = os.environ.get(ALLOWED_HOSTS_ENV, "")
    return frozenset(n.strip().lower() for n in raw.split(",") if n.strip())


def _host_name(host_header: str) -> str:
    """Host 標頭去掉 port：`127.0.0.1:8000`→`127.0.0.1`、`[::1]:8000`→`::1`。"""
    host = host_header.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end > 0 else ""
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def is_host_allowed(host_header: str | None, extra_names: Iterable[str] = ()) -> bool:
    """Host 是 IP 字面值、localhost 或額外放行的名稱才接受；缺 Host 一律拒絕。"""
    name = _host_name(host_header or "")
    if not name:
        return False
    if name == "localhost" or name in extra_names:
        return True
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def is_cross_site_write(method: str, host_header: str | None, origin: str | None,
                        fetch_site: str | None) -> bool:
    """寫入類請求是否來自別的網站。Origin 為 `null`（沙箱 iframe、file://）也算跨站。"""
    if method.upper() in SAFE_METHODS:
        return False
    if (fetch_site or "").lower() == "cross-site":
        return True
    if origin is None:
        return False
    return urlsplit(origin).netloc.lower() != (host_header or "").strip().lower()


class LocalRequestGuard:
    """純 ASGI middleware：Host 不合法回 400、跨站寫入回 403。

    不用 BaseHTTPMiddleware：聊天與分析是 SSE 串流，純 ASGI 不會插手回應本體。
    """

    def __init__(self, app, extra_host_names: Iterable[str] = ()) -> None:
        self.app = app
        self.extra_host_names = frozenset(n.lower() for n in extra_host_names)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host")
        if not is_host_allowed(host, self.extra_host_names):
            await _send_json(send, 400, INVALID_HOST_DETAIL)
            return
        if is_cross_site_write(scope["method"], host, headers.get("origin"),
                               headers.get("sec-fetch-site")):
            await _send_json(send, 403, CROSS_SITE_DETAIL)
            return
        await self.app(scope, receive, send)


async def _send_json(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode())],
    })
    await send({"type": "http.response.body", "body": body})
