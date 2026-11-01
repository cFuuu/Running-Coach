"""憑證與個人識別資訊的值樣式（CLAUDE.md §🔐 第 3 條）——唯一定義處。

離線匯出（scripts/build_dashboard_offline.py）用這組規則決定要不要中止。
命中時呼叫端只回報規則名稱與次數，**不回顯命中的內容**。
"""

from __future__ import annotations

import re

VALUE_RULES: tuple[tuple[str, re.Pattern], ...] = (
    ("email 位址", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.")),
    ("Bearer 憑證", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE)),
    ("cookie 字串", re.compile(
        r"\b(?:JWT_FGP|SESSIONID|GARMIN-SSO(?:-[A-Z]+)*|CASTGC|cf_clearance|__cf_bm|__cflb)\s*=",
        re.IGNORECASE)),
    # 2026-11-01 資安稽核：學員可能把金鑰或 Garmin 的 OAuth1 參數貼進聊天或筆記，
    # 夾在一般文字裡時「整個值就是長亂碼」的規則抓不到
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("OAuth 參數", re.compile(r"\boauth_(?:token|token_secret|consumer_secret)\s*=", re.IGNORECASE)),
)


def value_hits(text: str) -> list[str]:
    """回傳命中的規則（`名稱 ×次數`），沒命中回空清單。"""
    hits = []
    for name, pattern in VALUE_RULES:
        count = len(pattern.findall(text))
        if count:
            hits.append(f"{name} ×{count}")
    return hits
