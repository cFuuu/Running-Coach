"""從學員檔（`output/athlete_profile.md`）的「比賽成績」表讀出賽事名稱。

賽事名稱由 Fu 在學員檔裡手動維護：Garmin 的活動名稱多半是自動產生的
「信義區 跑步」，沒有意義，而學員檔本來就有一張人工整理過的比賽表。
讓那張表當名稱的唯一來源，就不必在資料庫再存一份、兩邊對不上。

只讀「日期」與「名稱」兩欄，以日期對應比賽場次；表格欄序調整不影響，
但標題或欄名改掉就讀不到——讀不到時回傳空 dict，呼叫端退回活動名稱，
不讓整個總覽因為一份個人筆記的格式而失敗。

本模組不碰資料庫，吃檔案路徑回傳 {日期 ISO 字串: 名稱}。
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any

# 學員檔裡比賽表所在小節的標題文字（含即可，不比對 # 的層級）
RACE_SECTION_HEADING = "比賽成績"
_DATE_COLUMN = "日期"
_NAME_COLUMN = "名稱"

# 名稱含這些字的場次是自己安排的計時測驗，不是報名的賽事
_PERSONAL_TEST_PATTERN = re.compile(r"測驗|測試")


def is_personal_test(name: str | None) -> bool:
    return bool(name and _PERSONAL_TEST_PATTERN.search(name))


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _clean(cell: str) -> str:
    """去掉粗體標記與註腳上標（¹²³…），保留使用者寫的其他文字。"""
    return re.sub(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]", "", cell.replace("**", "")).strip()


def _read_lines(path: Path | str | None) -> list[str]:
    if path is None:
        return []
    try:
        return Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def _read_table(
    lines: list[str], heading: str, required_columns: tuple[str, ...]
) -> tuple[list[str], list[list[str]]]:
    """讀標題含 heading 的小節裡、表頭含 required_columns 的第一張表。

    回傳 (表頭欄名, 資料列)；找不到時回傳 ([], [])。儲存格已去掉粗體與註腳上標。
    """
    in_section = False
    header: list[str] | None = None
    rows: list[list[str]] = []
    for line in lines:
        if line.lstrip().startswith("#"):
            if in_section and header is not None:
                break  # 已讀完該表所在的小節
            in_section = heading in line
            continue
        if not in_section:
            continue
        if not line.lstrip().startswith("|"):
            if header is not None and rows:
                break  # 表格結束
            continue
        cells = _cells(line)
        if header is None:
            if all(col in cells for col in required_columns):
                header = cells
            continue
        if set("".join(cells)) <= set("-: "):
            continue  # 分隔列 |---|---|
        rows.append([_clean(c) for c in cells])
    return (header or []), rows


def load_race_names(path: Path | str | None) -> dict[str, str]:
    """讀比賽表的「日期 → 名稱」。檔案不存在或找不到表格時回傳空 dict。"""
    header, rows = _read_table(
        _read_lines(path), RACE_SECTION_HEADING, (_DATE_COLUMN, _NAME_COLUMN)
    )
    names: dict[str, str] = {}
    for cells in rows:
        row = dict(zip(header, cells))
        date, name = row.get(_DATE_COLUMN), row.get(_NAME_COLUMN)
        if date and name and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            names[date] = name
    return names


# 學員檔裡「目標賽事」小節（項目｜值 的鍵值表）與「賽程」小節（日期｜名稱 的清單）
TARGET_SECTION_HEADING = "目標賽事"
UPCOMING_SECTION_HEADING = "賽程"
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _valid_date(text: str | None) -> str | None:
    """從儲存格文字取出 ISO 日期（容許「2026-12-20（日）」這類後綴），無效回傳 None。"""
    match = _ISO_DATE.search(text or "")
    if not match:
        return None
    try:
        datetime.date.fromisoformat(match.group())
    except ValueError:
        return None
    return match.group()


_TRAILING_DISTANCE = re.compile(r"^(.*?)\s*(\d+(?:\.\d+)?\s*[Kk])$")


def _split_distance(name: str) -> tuple[str, str | None]:
    """把名稱結尾的距離（例如「2026 臺北馬拉松 42K」的 42K）拆成獨立欄位。

    學員檔的慣例是距離寫在名稱最後；沒有就回傳 (原名稱, None)。
    """
    match = _TRAILING_DISTANCE.match(name)
    if not match or not match.group(1):
        return name, None
    return match.group(1), match.group(2).replace(" ", "").upper()


def _race(name: str, date: str) -> dict[str, Any]:
    base_name, distance = _split_distance(name)
    return {
        "name": base_name,
        "distance": distance,
        # 數值公里數（「21.0975K」→ 21.0975），前端依此分級上色，不必再解析字串
        "distance_km": float(distance[:-1]) if distance else None,
        "date": date,
    }


def load_race_schedule(path: Path | str | None) -> dict[str, Any]:
    """讀學員檔的目標賽事與賽程。

    回傳 {"target": 賽事 | None, "upcoming": [賽事, ...]}，賽事為
    {"name", "distance", "distance_km", "date"}；distance 是從名稱結尾拆出的「42K」
    這類字串、distance_km 是對應的公里數，沒有距離則兩者皆為 None。
    upcoming 只放目標賽事以外的場次（暖身賽等），目標賽事不必在賽程重複列一次。
    檔案不存在或找不到表格時 target 為 None、upcoming 為空。
    """
    lines = _read_lines(path)

    target = None
    _, target_rows = _read_table(lines, TARGET_SECTION_HEADING, ("項目", "值"))
    fields = {cells[0]: cells[1] for cells in target_rows if len(cells) >= 2}
    target_date = _valid_date(fields.get("日期"))
    if fields.get("賽事") and target_date:
        target = _race(fields["賽事"], target_date)

    upcoming = []
    header, rows = _read_table(
        lines, UPCOMING_SECTION_HEADING, (_DATE_COLUMN, _NAME_COLUMN)
    )
    for cells in rows:
        row = dict(zip(header, cells))
        date = _valid_date(row.get(_DATE_COLUMN))
        if date and row.get(_NAME_COLUMN):
            upcoming.append(_race(row[_NAME_COLUMN], date))
    return {"target": target, "upcoming": upcoming}


def race_countdown(schedule: dict[str, Any], today: datetime.date) -> dict[str, Any]:
    """由賽程算出「下場賽事」與「目標賽事」各還有幾天（比賽當天為 0）。

    下場賽事＝目標賽事與賽程裡、日期不早於 today 的最近一場；已過期的場次不顯示。
    兩者皆為 {"name", "distance", "distance_km", "date", "days_left", "is_target"} 或 None。
    """
    target = schedule.get("target")
    candidates = [
        {**race, "is_target": race is target}
        for race in [*schedule.get("upcoming", []), *([target] if target else [])]
    ]

    def shown(race: dict[str, Any]) -> dict[str, Any] | None:
        days_left = (datetime.date.fromisoformat(race["date"]) - today).days
        if days_left < 0:
            return None
        return {
            "name": race["name"],
            "distance": race.get("distance"),
            "distance_km": race.get("distance_km"),
            "date": race["date"],
            "days_left": days_left,
            "is_target": race["is_target"],
        }

    upcoming = [r for r in (shown(c) for c in candidates) if r is not None]
    return {
        "next": min(upcoming, key=lambda r: r["date"], default=None),
        "target": next((r for r in upcoming if r["is_target"]), None),
    }
