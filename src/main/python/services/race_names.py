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

import re
from pathlib import Path

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


def load_race_names(path: Path | str | None) -> dict[str, str]:
    """讀比賽表的「日期 → 名稱」。檔案不存在或找不到表格時回傳空 dict。"""
    if path is None:
        return {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    in_section = False
    header: list[str] | None = None
    names: dict[str, str] = {}
    for line in lines:
        if line.lstrip().startswith("#"):
            if in_section and header is not None:
                break  # 已讀完比賽表所在的小節
            in_section = RACE_SECTION_HEADING in line
            continue
        if not in_section:
            continue
        if not line.lstrip().startswith("|"):
            if header is not None and names:
                break  # 表格結束
            continue
        cells = _cells(line)
        if header is None:
            if _DATE_COLUMN in cells and _NAME_COLUMN in cells:
                header = cells
            continue
        if set("".join(cells)) <= set("-: "):
            continue  # 分隔列 |---|---|
        row = dict(zip(header, (_clean(c) for c in cells)))
        date, name = row.get(_DATE_COLUMN), row.get(_NAME_COLUMN)
        if date and name and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            names[date] = name
    return names
