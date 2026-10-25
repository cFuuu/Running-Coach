"""把 Claude Code 裡透過 MCP 問教練的問答匯入 SQLite（TODO.md N-33）。

MCP server 看不到學員的提問與教練的回答，只收得到工具呼叫，所以來源改用
Claude Code 逐字存的 session 紀錄（`~/.claude/projects/<專案路徑>/*.jsonl`）。

篩選以**回合**為單位（Fu 2026-10-25 決定）：一回合是學員送出一則訊息到下一則
之間的所有內容。這一回合裡教練呼叫過 running-coach 工具，或用 ToolSearch 載入
過 running-coach 工具，才算教練問答；同一個 session 裡改程式、查 bug 的回合跳過。
⚠️ 只憑上下文回答、沒碰工具的追問回合會被漏掉；開發時為了驗證呼叫工具的回合
會被誤收（可在面板刪掉整則對話）。

只留三樣東西：學員打的字、教練回覆的文字、running-coach 工具的名稱與參數。
**一律丟掉**：Claude Code 插入的標籤段落（瀏覽器指示、IDE 選取、slash command
提示詞等）、system-reminder 與其他 attachment、thinking、工具回傳的數據、所有
非 running-coach 工具（Bash、Read 等可能讀過 `.env` 或 token 路徑）。寫入前再用
secret_patterns 掃一次，命中就整批中止，不寫入任何東西。

匯入的對話存進 coach_conversations／coach_messages（source='claude_code'），
**唯讀**、不進事實層、規則引擎不讀——與面板聊天紀錄同一條邊界。

用法（專案根目錄）：
    python -m src.main.python.services.coach_transcript_import --db-path output/running_coach.db
    python -m src.main.python.services.coach_transcript_import --db-path output/running_coach.db --dry-run
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from src.main.python.models.db import get_connection
from src.main.python.services import coach_chat_store
from src.main.python.services.dashboard_queries import resolve_athlete_id
from src.main.python.services.secret_patterns import value_hits

TOOL_PREFIX = "mcp__running-coach__"
TRANSCRIPTS_DIR_ENV = "RC_CLAUDE_TRANSCRIPTS_DIR"
PROJECT_ROOT = Path(__file__).resolve().parents[4]

# Claude Code 插在學員訊息裡、不是學員打的段落：整段連內容刪掉
_DROP_TAGS = (
    "system-reminder",
    "browser_instruction",
    "ide_opened_file",
    "ide_selection",
    "local-command-caveat",
    "local-command-stdout",
    "command-name",
    "command-message",
    "task-notification",
    "agent-message",
)
# 內容是學員自己的、只拿掉標籤本身
_UNWRAP_TAGS = ("command-args", "pasted_content")

_DROP_RE = re.compile(
    r"<(" + "|".join(map(re.escape, _DROP_TAGS)) + r")\b[^>]*>.*?</\1>", re.DOTALL
)
_UNWRAP_RE = re.compile(r"</?(?:" + "|".join(map(re.escape, _UNWRAP_TAGS)) + r")\b[^>]*>")
# 學員貼圖時 Claude Code 會插一行圖片暫存檔路徑
_IMAGE_LINE_RE = re.compile(r"^\[Image[^\]]*\]\s*$", re.MULTILINE)


class TranscriptSecurityError(RuntimeError):
    """匯入內容命中憑證樣式。訊息只含 session 前綴與規則名稱，不含命中內容。"""


def default_transcripts_dir(project_root: Path = PROJECT_ROOT) -> Path:
    """Claude Code 存本專案 session 的資料夾：路徑中非英數字元一律換成 `-`。"""
    encoded = re.sub(r"[^A-Za-z0-9]", "-", str(project_root))
    return Path.home() / ".claude" / "projects" / encoded


def clean_user_text(content: Any) -> str:
    """學員訊息 → 只剩學員打的字（去掉插入的標籤段落與圖片路徑行）。"""
    if isinstance(content, str):
        blocks = [{"type": "text", "text": content}]
    elif isinstance(content, list):
        blocks = content
    else:
        return ""
    text = "\n".join(
        b.get("text") or ""
        for b in blocks
        if isinstance(b, dict) and b.get("type") == "text"
    )
    text = _DROP_RE.sub("", text)
    text = _UNWRAP_RE.sub("", text)
    text = _IMAGE_LINE_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _iso(timestamp: str) -> str:
    """JSONL 的 `...Z` 時間 → 與 coach_chat_store 一致的 `+00:00` 格式。"""
    return datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).isoformat()


@dataclass
class _Turn:
    events: list[tuple[str, dict[str, Any], str]] = field(default_factory=list)  # (role, block, 時間)
    coaching: bool = False

    def add_user_text(self, text: str, timestamp: str) -> None:
        if text:
            self.events.append(("user", {"type": "text", "text": text}, timestamp))


def _is_coach_tool_search(block: dict[str, Any]) -> bool:
    query = (block.get("input") or {}).get("query")
    return block.get("name") == "ToolSearch" and isinstance(query, str) and TOOL_PREFIX in query


def _turns(records: Iterable[dict[str, Any]]) -> list[_Turn]:
    turns: list[_Turn] = []
    current: _Turn | None = None
    for rec in records:
        if rec.get("isSidechain"):
            continue  # 子代理的內部對話，不是學員與教練之間的
        kind = rec.get("type")
        content = (rec.get("message") or {}).get("content")
        timestamp = rec.get("timestamp") or ""

        if kind == "user":
            if "toolUseResult" in rec or rec.get("isMeta") or rec.get("isCompactSummary"):
                continue
            if isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
            ):
                continue
            current = _Turn()
            turns.append(current)
            current.add_user_text(clean_user_text(content), timestamp)

        elif kind == "attachment" and current is not None:
            # 教練回覆途中學員插話（Claude Code 存成 queued_command）
            att = rec.get("attachment") or {}
            if att.get("type") == "queued_command":
                current.add_user_text(clean_user_text(att.get("prompt")), timestamp)

        elif kind == "assistant" and current is not None and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and (block.get("text") or "").strip():
                    current.events.append(("assistant", {"type": "text", "text": block["text"]}, timestamp))
                elif block.get("type") == "tool_use":
                    name = block.get("name") or ""
                    if name.startswith(TOOL_PREFIX):
                        current.coaching = True
                        current.events.append((
                            "assistant",
                            {
                                "type": "tool_use",
                                "id": block.get("id"),
                                "name": name[len(TOOL_PREFIX):],
                                "input": block.get("input") or {},
                            },
                            timestamp,
                        ))
                    elif _is_coach_tool_search(block):
                        current.coaching = True
    return turns


def coach_messages(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """一個 session 的紀錄 → 教練回合的訊息（同角色相鄰的區塊併成一則）。

    每則為 `{"role", "content": [blocks], "created_at"}`，格式同 coach_messages
    表，面板沿用同一套顯示程式。
    """
    messages: list[dict[str, Any]] = []
    for turn in _turns(records):
        if not turn.coaching:
            continue
        for role, block, timestamp in turn.events:
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"].append(block)
            else:
                messages.append({"role": role, "content": [block], "created_at": _iso(timestamp)})
    return messages


def _read_records(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 寫到一半的最後一行（session 進行中）
    return records


def collect_sessions(transcripts_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """{session id: 教練回合訊息}，只含至少有一個教練回合的 session。"""
    sessions: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(transcripts_dir.glob("*.jsonl")):
        # 先粗篩：連工具名稱前綴都沒出現的 session 不必解析
        if TOOL_PREFIX.encode() not in path.read_bytes():
            continue
        messages = coach_messages(_read_records(path))
        if messages:
            sessions[path.stem] = messages
    return sessions


def security_check(sessions: dict[str, list[dict[str, Any]]]) -> None:
    """任一 session 命中憑證樣式就整批中止。"""
    problems = []
    for session_id, messages in sessions.items():
        hits = value_hits(json.dumps(messages, ensure_ascii=False))
        if hits:
            problems.append(f"session {session_id[:8]}：" + "、".join(hits))
    if problems:
        raise TranscriptSecurityError(
            "匯入內容疑似含憑證或個人識別資訊，已中止，未寫入任何資料。" + "；".join(problems)
        )


def import_transcripts(
    conn: sqlite3.Connection,
    transcripts_dir: Path,
    athlete_id: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """匯入並回傳各結果的 session 數（created／updated／unchanged／dismissed）與回合訊息數。"""
    resolved = resolve_athlete_id(conn, athlete_id)
    if resolved is None:
        raise SystemExit("資料庫中沒有任何學員資料")
    if not transcripts_dir.is_dir():
        raise SystemExit(f"找不到 Claude Code 的 session 資料夾：{transcripts_dir}")

    sessions = collect_sessions(transcripts_dir)
    security_check(sessions)

    summary = {"created": 0, "updated": 0, "unchanged": 0, "dismissed": 0, "messages": 0}
    for session_id, messages in sessions.items():
        if coach_chat_store.is_import_dismissed(conn, session_id):
            summary["dismissed"] += 1
            continue
        summary["messages"] += len(messages)
        if dry_run:
            summary["created"] += 1
            continue
        result = coach_chat_store.save_imported_conversation(conn, resolved, session_id, messages)
        summary[result] += 1
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把 Claude Code 裡的 MCP 教練問答匯入 SQLite（唯讀對話）。")
    ap.add_argument("--db-path", required=True, help="SQLite 資料庫路徑")
    ap.add_argument(
        "--transcripts-dir",
        type=Path,
        default=os.environ.get(TRANSCRIPTS_DIR_ENV) or None,
        help=f"Claude Code 的 session 資料夾（預設依專案路徑推算，或讀環境變數 {TRANSCRIPTS_DIR_ENV}）",
    )
    ap.add_argument("--athlete-id", type=int, default=None, help="學員 ID（預設第一位）")
    ap.add_argument("--dry-run", action="store_true", help="只統計，不寫入")
    args = ap.parse_args(argv)

    transcripts_dir = args.transcripts_dir or default_transcripts_dir()
    conn = get_connection(args.db_path)
    try:
        summary = import_transcripts(conn, Path(transcripts_dir), args.athlete_id, args.dry_run)
    except TranscriptSecurityError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        conn.close()

    prefix = "（試跑，未寫入）" if args.dry_run else ""
    print(
        f"{prefix}教練問答 session：新增 {summary['created']}、更新 {summary['updated']}、"
        f"未變 {summary['unchanged']}、已刪除略過 {summary['dismissed']}；共 {summary['messages']} 則訊息"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
