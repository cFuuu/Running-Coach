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
"""

from __future__ import annotations

import ipaddress
import os

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
