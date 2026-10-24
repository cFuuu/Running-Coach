"""把整個 Dashboard 連同資料快照打包成單一離線 HTML（iPhone 經 iCloud 開啟用，見 docs/dev/OFFLINE_EXPORT.md）。

做法：
1. 資料：在本進程內用 FastAPI `TestClient` 打真正的 API（`create_app`），依
   `snapshot_requests()` 的 URL 清單取回應，組成快照。行事曆與每日體感的請求起訖日
   是前端當場算的（任意月份、任意週），無法逐一列舉，所以各取一份涵蓋整段期間的資料，
   由頁面內的載入程式依 start/end 切片回應。
2. 快照預設 gzip＋base64 寫進 `window.__SNAPSHOT_GZ__`（`--no-gzip` 改寫明文
   `window.__SNAPSHOT__`，除錯用）；`window.__SNAPSHOT_AT__` 是匯出時間。
   載入程式把自己掛成 `window.MOCK_API`——與 `?mock=1` 假資料同一個接縫
   （core/api.js 的 useMock），前端程式碼不用改。
3. 前端：styles.css 內嵌；JS 模組轉成 `data:` URL，以 importmap 對應
   （import 路徑改寫成 `@rc/js/<路徑>`）。保留真正的 ES module 語意——
   例如 tooltip.js 會重新指定 export 的變數，函式作用域打包（build_pace_calculator
   的寫法）做不到 live binding，所以這裡不用那種打包，只重用它的 import 解析工具。
   遇到不支援的寫法（動態 import、re-export、多行 import）直接報錯。
4. 寫出前做資安掃描（CLAUDE.md §🔐 第 3 條）：出現 token、cookie、email、
   Garmin 帳號識別欄位、或資料庫 athlete_source_identity 裡的帳號識別值就中止，
   錯誤訊息只寫規則名稱與次數，**不回顯命中的內容**。

**絕不寫入資料庫**：`create_app()` 啟動時會套 schema 遷移並改寫分析紀錄狀態，
所以先以唯讀模式（sqlite `mode=ro`）開啟來源資料庫，用 backup API 複製到暫存目錄，
API 一律打在這份暫存副本上，結束後刪除。

用法（專案根目錄）：
    python scripts/build_dashboard_offline.py --db-path output/running_coach.db
    python scripts/build_dashboard_offline.py --detail-months 12 --copy-to "<iCloud 資料夾>"
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import datetime
import gzip
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_pace_calculator as _bpc  # noqa: E402  重用 import 解析工具，不另寫一份

DASHBOARD_DIR = _bpc.DASHBOARD_DIR
INDEX_HTML = DASHBOARD_DIR / "index.html"
ENTRY = DASHBOARD_DIR / "js" / "main.js"
STYLES = _bpc.STYLES
DEFAULT_DB = ROOT / "output" / "running_coach.db"
DEFAULT_OUT = ROOT / "output" / "dashboard_offline.html"
COPY_TO_ENV = "RC_OFFLINE_EXPORT_DIR"
DEFAULT_KEEP_LONG_KM = 21.0  # 半馬 21.0975 km 以上；--detail-months 時舊場次的長跑仍保留明細
MODULE_PREFIX = "@rc/js/"
# 與 app.py 的 DEFAULT_PROFILE_FILENAME 相同語意：學員檔與資料庫放同一個資料夾
from src.main.python.api.app import DEFAULT_PROFILE_FILENAME  # noqa: E402


class SecurityScanError(RuntimeError):
    """資安掃描命中。訊息只含規則名稱與次數，不含命中內容。"""


# --------------------------------------------------------------------------
# 快照要取的 URL（唯一定義處）
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class SnapshotContext:
    """產生 URL 清單所需的資訊（都在取得 /api/meta 與行事曆後才確定）。"""

    today: datetime.date
    ranges: tuple[str, ...]          # /api/meta 的 ranges（後端 RANGE_LABELS 是唯一來源）
    detail_ids: tuple[int, ...]      # 要附上單場明細的活動 id（受 --detail-months 限制）
    span_start: datetime.date        # 行事曆／體感資料涵蓋的起日
    span_end: datetime.date          # 行事曆／體感資料涵蓋的訖日
    prev_ranges: tuple[str, ...] = ()  # 趨勢 KPI 前期比較的 custom:起:訖（每個有起日的 range 一個）


# 首頁固定用 30d 的請求（views/home.js：loadHomeOverview、loadTodayKpi），與頂列 range 無關
HOME_FIXED_RANGE = "30d"

# 依 range 變化的端點（shell/loader.js loadRangeData、views/load.js、views/overview.js）
RANGE_ENDPOINTS = (
    "/api/sessions",
    "/api/wellness-trend",
    "/api/training-days",
    "/api/recovery-impact",
    "/api/garmin-cards",
    "/api/training-load",
    "/api/overview",
)

# 趨勢頁數字卡的前期比較（views/trends.js ensurePrevious）：只抓這四支
PREV_ENDPOINTS = ("/api/training-load", "/api/wellness-trend", "/api/sessions", "/api/overview")


def previous_range(start: str | None, end: str) -> str | None:
    """前一段等長期間的 custom range（與 views/trends.js previousWindow 同規則）；沒有起日回 None。"""
    if not start:
        return None
    s, e = datetime.date.fromisoformat(start), datetime.date.fromisoformat(end)
    prev_end = s - datetime.timedelta(days=1)
    prev_start = prev_end - (e - s)
    return f"custom:{prev_start.isoformat()}:{prev_end.isoformat()}"


def snapshot_requests(ctx: SnapshotContext) -> list[str]:
    """離線快照要預先取得、依 URL 原樣回應的請求清單（`exact`）。

    **前端新增請求時，在這裡補一行**（例如趨勢 KPI 的前期比較：
    `urls.append(f"/api/overview?range=custom:{a}:{b}")`）。
    URL 寫成未編碼的 path?k=v&k=v，順序要和前端送出的一致；athlete_id 不用寫，
    匯出與頁面載入程式都會忽略它。
    """
    urls = [
        "/api/meta",                                       # main.js
        "/api/today",                                      # views/home.js loadToday
        f"/api/overview?range={HOME_FIXED_RANGE}",         # views/home.js loadHomeOverview
        f"/api/training-load?range={HOME_FIXED_RANGE}",    # views/home.js loadTodayKpi
        f"/api/garmin-cards?range={HOME_FIXED_RANGE}",     # views/home.js loadTodayKpi
    ]
    for range_key in ctx.ranges:
        urls += [f"{endpoint}?range={range_key}" for endpoint in RANGE_ENDPOINTS]
    for prev in ctx.prev_ranges:  # views/trends.js ensurePrevious
        urls += [f"{endpoint}?range={prev}" for endpoint in PREV_ENDPOINTS]
    urls += [f"/api/sessions/{sid}" for sid in ctx.detail_ids]  # shell/loader.js loadSessionDetail
    return list(dict.fromkeys(urls))


def span_requests(ctx: SnapshotContext) -> dict[str, str]:
    """起訖日由前端當場決定的端點：各取一份涵蓋整段期間的資料，頁面內依 start/end 切片。

    /api/calendar：views/calendar.js（月曆格）、views/plan-week.js（課表週）、
    views/home.js loadRecentSessions（近 30 天）。/api/daily-feel：views/home.js 今日體感。
    """
    s, e = ctx.span_start.isoformat(), ctx.span_end.isoformat()
    return {
        "calendar": f"/api/calendar?start={s}&end={e}",
        "feels": f"/api/daily-feel?start={s}&end={e}",
    }


def canonical_key(url: str) -> str:
    """快照 key：path + 未編碼的 k=v（保留順序、去掉 athlete_id）。頁面內的 canon() 與此一致。"""
    parts = urlsplit(url)
    query = [f"{k}={v}" for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "athlete_id"]
    return parts.path + ("?" + "&".join(query) if query else "")


# --------------------------------------------------------------------------
# 日期工具（與前端 monthGridRange／planWeekRange 同規則）
# --------------------------------------------------------------------------


def _months_back(day: datetime.date, months: int) -> datetime.date:
    y, m = divmod(day.year * 12 + (day.month - 1) - months, 12)
    m += 1
    last = (datetime.date(y + (m == 12), m % 12 + 1, 1) - datetime.timedelta(days=1)).day
    return datetime.date(y, m, min(day.day, last))


def _sunday_on_or_before(day: datetime.date) -> datetime.date:
    return day - datetime.timedelta(days=(day.weekday() + 1) % 7)


def _month_grid(day: datetime.date) -> tuple[datetime.date, datetime.date]:
    """day 所在月份的月曆格（週日起排）：第一天所在週日 ~ 最後一天所在週六。"""
    first = day.replace(day=1)
    last = _months_back(first, -1) - datetime.timedelta(days=1)
    return _sunday_on_or_before(first), _sunday_on_or_before(last) + datetime.timedelta(days=6)


def _plan_week_end(day: datetime.date) -> datetime.date:
    """day 所屬課表週（週一到週日）的週日。"""
    return day + datetime.timedelta(days=6 - day.weekday())


# --------------------------------------------------------------------------
# 資料庫（唯讀）
# --------------------------------------------------------------------------


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError(f"找不到資料庫：{db_path}")
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _copy_db(src: sqlite3.Connection, dest: Path) -> None:
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
    finally:
        out.close()


def _date_bounds(conn: sqlite3.Connection, athlete_id: int) -> tuple[str | None, str | None]:
    """最早的活動／課表日，與最晚的課表日（行事曆要涵蓋未來的課表）。"""
    act = conn.execute(
        "SELECT MIN(date(started_at)) FROM activities WHERE athlete_id = ?", (athlete_id,)
    ).fetchone()[0]
    plan_min, plan_max = conn.execute(
        "SELECT MIN(planned_date), MAX(planned_date) FROM training_plan WHERE athlete_id = ?",
        (athlete_id,),
    ).fetchone()
    earliest = min((d for d in (act, plan_min) if d), default=None)
    return earliest, plan_max


def _identity_refs(conn: sqlite3.Connection) -> list[str]:
    """資料庫記錄的外部帳號識別值（例如 Garmin userProfileId），快照裡一律不得出現。"""
    return [str(r[0]) for r in conn.execute("SELECT external_ref FROM athlete_source_identity")]


# --------------------------------------------------------------------------
# 取快照
# --------------------------------------------------------------------------


def _compact_records(detail: dict) -> dict:
    """逐秒資料改成欄式 {cols, rows}，頁面載入程式取用時還原成物件陣列。"""
    records = detail.get("records")
    points = records.get("points") if isinstance(records, dict) else None
    if isinstance(points, list) and points and isinstance(points[0], dict):
        cols = list(points[0].keys())
        records["points"] = {"cols": cols, "rows": [[p.get(c) for c in cols] for p in points]}
    return detail


def collect_snapshot(
    db_path: Path,
    detail_months: int = 0,
    athlete_id: int | None = None,
    profile_path: Path | None = None,
    today: datetime.date | None = None,
    keep_long_km: float = 0.0,
) -> tuple[dict, list[str]]:
    """回傳 (快照, 資料庫裡的帳號識別值)。來源資料庫只以唯讀模式開啟。"""
    from fastapi.testclient import TestClient

    from src.main.python.api.app import create_app
    from src.main.python.services import coach_chat, dashboard_queries

    today = today or datetime.date.today()
    profile_path = profile_path or db_path.with_name(DEFAULT_PROFILE_FILENAME)

    with tempfile.TemporaryDirectory(prefix="rc_offline_") as tmp:
        tmp_db = Path(tmp) / "snapshot.db"
        src = _open_readonly(db_path)
        try:
            _copy_db(src, tmp_db)
        finally:
            src.close()

        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        try:
            resolved = dashboard_queries.resolve_athlete_id(conn, athlete_id)
            if resolved is None:
                raise SystemExit("資料庫中沒有任何學員資料")
            earliest, plan_max = _date_bounds(conn, resolved)
            identity_refs = _identity_refs(conn)
        finally:
            conn.close()

        empty_assets = Path(tmp) / "assets"
        empty_assets.mkdir()
        app = create_app(
            str(tmp_db),
            assets_dir=empty_assets,
            chat_config=coach_chat.ChatConfig(api_key_present=False),
            profile_path=profile_path,
        )
        # TestClient 每個請求都會記一行 INFO log（新版 starlette 走 httpx2），數百場明細會洗版
        for name in ("httpx", "httpx2"):
            logging.getLogger(name).setLevel(logging.WARNING)
        cache: dict[str, dict] = {}
        with TestClient(app) as client:

            def get(url: str) -> dict:
                if url in cache:
                    return cache[url]
                full = url
                if athlete_id is not None:
                    full += ("&" if "?" in url else "?") + f"athlete_id={athlete_id}"
                res = client.get(full)
                if res.status_code != 200:
                    raise SystemExit(f"API 回應 {res.status_code}：{canonical_key(url)}")
                cache[url] = res.json()
                return cache[url]

            meta = get("/api/meta")
            ranges = tuple(r["key"] for r in meta.get("ranges") or [])

            cutoff = _months_back(today, detail_months) if detail_months > 0 else None
            span_from = cutoff or (datetime.date.fromisoformat(earliest) if earliest else today)
            span_start = _month_grid(min(span_from, today))[0]
            span_end = max(_month_grid(today)[1], _plan_week_end(today))
            if plan_max:
                plan_day = datetime.date.fromisoformat(plan_max)
                span_end = max(span_end, _month_grid(plan_day)[1], _plan_week_end(plan_day))

            ctx = SnapshotContext(today, ranges, (), span_start, span_end)
            spans = {name: get(url) for name, url in span_requests(ctx).items()}

            # 單場明細：行事曆（全部運動類型，首頁近況可點）＋場次清單（跑步）的聯集
            ids: dict[int, str] = {}
            for day in spans["calendar"].get("days") or []:
                for act in day.get("activities") or []:
                    ids[act["id"]] = day["date"]
            all_sessions = get("/api/sessions?range=all") if "all" in ranges else {"sessions": []}
            for s in all_sessions.get("sessions") or []:
                ids.setdefault(s["id"], s.get("date") or "")
            # 超過 detail_months 的舊場次，距離達 keep_long_km 以上的長跑（半馬、全馬）仍保留明細
            long_ids = {
                s["id"] for s in all_sessions.get("sessions") or []
                if keep_long_km > 0 and (s.get("distance_km") or 0) >= keep_long_km
            }
            detail_ids = tuple(
                sid for sid, day in sorted(ids.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
                if cutoff is None or sid in long_ids or (day and day >= cutoff.isoformat())
            )
            windows = [get(f"/api/wellness-trend?range={r}") for r in ranges]
            prev_ranges = tuple(p for p in (previous_range(w.get("start_date"), w["end_date"]) for w in windows) if p)
            ctx = dataclasses.replace(ctx, detail_ids=detail_ids, prev_ranges=prev_ranges)

            exact: dict[str, dict] = {}
            for url in snapshot_requests(ctx):
                data = get(url)
                if url.startswith("/api/sessions/"):
                    data = _compact_records(data)
                exact[canonical_key(url)] = data

    snapshot = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "detail_since": cutoff.isoformat() if cutoff else None,
        "detail_long_km": keep_long_km if cutoff and keep_long_km > 0 else None,
        "exact": exact,
        "calendar": spans["calendar"],
        "feels": spans["feels"].get("feels") or [],
    }
    return snapshot, identity_refs


# --------------------------------------------------------------------------
# 資安掃描
# --------------------------------------------------------------------------

# 只掃快照資料：欄位名稱類（API 回應不該帶這些欄位）
_DATA_RULES: tuple[tuple[str, re.Pattern], ...] = (
    ("Garmin 帳號識別欄位（userProfileId／displayName 等）", re.compile(
        r"userProfile(?:Id|Pk)|displayName|ownerFullName|ownerId\b|garminGuid|profileImageUrl",
        re.IGNORECASE)),
    ("憑證欄位（token／cookie／authorization 等）", re.compile(
        r'"(?:[\w-]*_token|token|tokens|oauth[\w-]*|cookies?|set-cookie|authorization|'
        r'consumer_(?:key|secret)|password|passwd|secret|api_?key|session_?id|jwt[\w-]*)"\s*:',
        re.IGNORECASE)),
    ("email／帳號欄位", re.compile(r'"(?:e-?mail(?:Address)?|user_?name|login)"\s*:', re.IGNORECASE)),
    ("疑似不透明 token 的長字串", re.compile(
        r'"(?=[A-Za-z0-9_+/=.-]*[0-9])(?=[A-Za-z0-9_+/=.-]*[A-Za-z])[A-Za-z0-9_+/=.-]{40,}"')),
)

# 快照與頁面程式碼都掃：值的樣式
_VALUE_RULES: tuple[tuple[str, re.Pattern], ...] = (
    ("email 位址", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.")),
    ("Bearer 憑證", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE)),
    ("cookie 字串", re.compile(
        r"\b(?:JWT_FGP|SESSIONID|GARMIN-SSO(?:-[A-Z]+)*|CASTGC|cf_clearance|__cf_bm|__cflb)\s*=",
        re.IGNORECASE)),
)

# 太短的識別值（例如 "1"）會在數字資料裡大量誤判，不拿來比對
_MIN_IDENTITY_LEN = 5


def security_scan(snapshot_json: str, page_text: str, identity_refs: list[str] = ()) -> None:
    """命中任何規則就拋 SecurityScanError；訊息只有規則名稱與次數。"""
    hits: list[str] = []

    def check(name: str, pattern: re.Pattern, text: str) -> None:
        count = len(pattern.findall(text))
        if count:
            hits.append(f"{name} ×{count}")

    for name, pattern in _DATA_RULES:
        check(name, pattern, snapshot_json)
    for name, pattern in _VALUE_RULES:
        check(name, pattern, snapshot_json + "\n" + page_text)
    for ref in identity_refs:
        if len(ref) >= _MIN_IDENTITY_LEN:
            check("資料庫的帳號識別值（athlete_source_identity.external_ref）",
                  re.compile(r"(?<![\w@.-])" + re.escape(ref) + r"(?![\w@.-])"), snapshot_json)
    if hits:
        raise SecurityScanError("資安掃描未通過，已中止，未寫出任何檔案。命中：" + "；".join(hits))


# --------------------------------------------------------------------------
# 前端打包
# --------------------------------------------------------------------------


def _collect_modules(path: Path, sources: dict[str, str]) -> None:
    """從進入點沿 import 收集所有模組，import 路徑改寫成 importmap 的 @rc/js/<路徑>。"""
    key = _bpc._key(path)
    if key in sources:
        return
    sources[key] = ""  # 先佔位，避免循環 import 無限遞迴
    src = _bpc._read(path)
    deps: list[Path] = []

    def rewrite(match: re.Match) -> str:
        target = _bpc._resolve(path, match.group(2))
        deps.append(target)
        return f"import {match.group(1)} from '{MODULE_PREFIX}{_bpc._key(target)}';"

    src = _bpc._IMPORT_RE.sub(rewrite, src)
    leftover = [
        line for line in re.findall(r"^\s*import\b.*$", src, re.MULTILINE)
        if f"from '{MODULE_PREFIX}" not in line
    ]
    if leftover:
        sys.exit(f"{path.name} 有不支援的 import 寫法（只支援單行的靜態 import）")
    if re.search(r"\bimport\s*\(", src):
        sys.exit(f"{path.name} 有動態 import()，離線檔不支援")
    if re.search(r"^\s*export\b[^\n;]*\bfrom\s*['\"]", src, re.MULTILINE):
        sys.exit(f"{path.name} 有 re-export（export … from），離線檔不支援")
    sources[key] = src
    for dep in deps:
        _collect_modules(dep, sources)


def _importmap(sources: dict[str, str]) -> str:
    imports = {
        MODULE_PREFIX + key: "data:text/javascript;charset=utf-8;base64,"
        + base64.b64encode(src.encode("utf-8")).decode("ascii")
        for key, src in sorted(sources.items())
    }
    return json.dumps({"imports": imports})


# 頁面內的快照載入程式：掛成 window.MOCK_API（core/api.js 與 coach.js 的既有接縫）
LOADER_JS = r"""
(function () {
  var D = null;
  var ready = window.__SNAPSHOT__
    ? Promise.resolve().then(function () { D = window.__SNAPSHOT__; })
    : (function () {
      var bin = atob(window.__SNAPSHOT_GZ__);
      var bytes = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      var stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
      return new Response(stream).json().then(function (d) { D = d; });
    })();
  // 與 build_dashboard_offline.canonical_key() 一致：path + 未編碼 k=v，去掉 athlete_id
  function canon(path) {
    var u = new URL(path, 'http://offline.invalid');
    var parts = [];
    u.searchParams.forEach(function (v, k) { if (k !== 'athlete_id') parts.push(k + '=' + v); });
    return { key: u.pathname + (parts.length ? '?' + parts.join('&') : ''), path: u.pathname, p: u.searchParams };
  }
  function offline(what) { return Promise.reject(new Error('離線快照不支援：' + what)); }
  function clone(x) { return JSON.parse(JSON.stringify(x)); }
  function routeNow(raw, body) {
    var r = canon(raw);
    if (body !== undefined) return offline('寫入資料（' + r.path + '）');
    if (Object.prototype.hasOwnProperty.call(D.exact, r.key)) {
      var out = clone(D.exact[r.key]);
      var pk = out.records && out.records.points;
      if (pk && !Array.isArray(pk)) {
        out.records.points = pk.rows.map(function (row) {
          var o = {};
          for (var i = 0; i < pk.cols.length; i++) o[pk.cols[i]] = row[i];
          return o;
        });
      }
      return Promise.resolve(out);
    }
    if (/^\/api\/sessions\/\d+$/.test(r.path)) {
      // 前端（shell/loader.js）離線時把這個訊息當說明顯示，不當讀取失敗
      return Promise.reject(new Error('離線檔未包含這場的明細'
        + (D.detail_since ? '（只保留 ' + D.detail_since + ' 之後'
          + (D.detail_long_km ? '、以及 ' + D.detail_long_km + ' km 以上長跑' : '') + '的單場明細）' : '')));
    }
    var s = r.p.get('start'), e = r.p.get('end');
    if (r.path === '/api/calendar' && s && e) {
      return Promise.resolve({
        start: s, end: e, today: D.calendar.today,
        days: clone(D.calendar.days.filter(function (d) { return d.date >= s && d.date <= e; }))
      });
    }
    if (r.path === '/api/daily-feel' && s && e) {
      return Promise.resolve({ feels: clone(D.feels.filter(function (f) { return f.date >= s && f.date <= e; })) });
    }
    return offline(r.key);
  }
  window.MOCK_API = {
    isMock: true,
    isOffline: true,
    snapshotAt: window.__SNAPSHOT_AT__,      // 頁首「資料截至」
    today: window.__SNAPSHOT_TODAY__,        // 前端 todayISO() 的「今天」＝匯出日期
    request: function (raw, body) { return ready.then(function () { return routeNow(raw, body); }); },
    chatRequest: function (path) {
      if (/\/status$/.test(canon(path).path)) {
        return Promise.resolve({ enabled: false, client_allowed: false, running: false, reason: '離線快照不提供 AI 教練' });
      }
      return offline('AI 教練');
    },
    chatStream: function () { return offline('AI 教練'); }
  };
})();
"""


def _replace_once(html: str, old: str, new: str) -> str:
    if html.count(old) != 1:
        sys.exit(f"index.html 結構改變，找不到唯一的 {old!r}，請更新 build_dashboard_offline.py")
    return html.replace(old, new)


def build_page(snapshot: dict, snapshot_at: str, use_gzip: bool = True) -> tuple[str, str, str]:
    """回傳 (完整 HTML, 快照 JSON 原文, 頁面程式碼原文)。後兩者供資安掃描。"""
    sources: dict[str, str] = {}
    _collect_modules(ENTRY, sources)
    styles = _bpc._read(STYLES)

    snapshot_json = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    if use_gzip:
        payload = base64.b64encode(gzip.compress(snapshot_json.encode("utf-8"), mtime=0)).decode("ascii")
        data_js = f"window.__SNAPSHOT_GZ__ = {json.dumps(payload)};"
    else:
        # JSON 字串裡 "\/" 合法，避免資料含 </script> 提前結束標籤
        data_js = "window.__SNAPSHOT__ = " + snapshot_json.replace("</", "<\\/") + ";"

    tail = (
        f"<script>window.__SNAPSHOT_AT__ = {json.dumps(snapshot_at)};\n"
        f"window.__SNAPSHOT_TODAY__ = {json.dumps(snapshot['today'])};\n{data_js}</script>\n"
        f"  <script>{LOADER_JS}</script>\n"
        f"  <script type=\"importmap\">{_importmap(sources)}</script>\n"
        f"  <script type=\"module\">import '{MODULE_PREFIX}{_bpc._key(ENTRY)}';</script>"
    )
    html = _bpc._read(INDEX_HTML)
    html = _replace_once(html, '<link rel="stylesheet" href="styles.css">',
                         "<style>\n" + styles.replace("</style", "<\\/style") + "\n</style>")
    html = _replace_once(html, '<script type="module" src="js/main.js"></script>', tail)

    page_text = "\n".join([_bpc._read(INDEX_HTML), styles, LOADER_JS, *sources.values()])
    return html, snapshot_json, page_text


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build(
    db_path: Path,
    out: Path,
    detail_months: int = 0,
    use_gzip: bool = True,
    athlete_id: int | None = None,
    copy_to: Path | None = None,
    profile_path: Path | None = None,
    today: datetime.date | None = None,
    also_txt: bool = False,
    keep_long_km: float = 0.0,
) -> Path:
    if copy_to is not None and not copy_to.is_dir():
        raise SystemExit(f"--copy-to 指定的資料夾不存在：{copy_to}")
    snapshot, identity_refs = collect_snapshot(db_path, detail_months, athlete_id, profile_path, today, keep_long_km)
    snapshot_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    html, snapshot_json, page_text = build_page(snapshot, snapshot_at, use_gzip)
    security_scan(snapshot_json, page_text, identity_refs)

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(html, encoding="utf-8")
    os.replace(tmp, out)
    # 同內容的 .txt：捷徑「取得檔案」拿純文字再 base64 編碼成 data URL 比較穩
    txt = out.with_suffix(".txt") if also_txt else None
    if txt is not None:
        shutil.copy2(out, txt)
    if copy_to is not None:
        shutil.copy2(out, copy_to / out.name)
        if txt is not None:
            shutil.copy2(out, copy_to / txt.name)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB, help="資料庫路徑（只讀）")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="輸出 HTML 路徑")
    parser.add_argument(
        "--copy-to", type=Path, default=os.environ.get(COPY_TO_ENV) or None,
        help=f"另外複製一份到這個資料夾（例如 iCloud Drive），預設讀環境變數 {COPY_TO_ENV}，沒設就不複製",
    )
    parser.add_argument("--dated", action="store_true", help="檔名加上匯出日期（dashboard_offline_YYYY-MM-DD.html）")
    parser.add_argument("--txt", action="store_true", help="同時輸出內容相同的 .txt（輸出處與 --copy-to 都會有）")
    parser.add_argument("--detail-months", type=int, default=0,
                        help="只保留近 N 個月的單場明細（0＝全部）")
    parser.add_argument("--keep-long-km", type=float, default=DEFAULT_KEEP_LONG_KM,
                        help="搭配 --detail-months：更早的場次中，距離達這個公里數以上的長跑仍保留明細"
                             f"（預設 {DEFAULT_KEEP_LONG_KM:g}，0＝不保留）")
    parser.add_argument("--no-gzip", action="store_true", help="快照不壓縮（除錯用）")
    parser.add_argument("--athlete-id", type=int, default=None, help="學員 ID（預設第一位）")
    parser.add_argument("--profile-path", type=Path, default=None,
                        help=f"學員檔（賽事名稱來源），預設為資料庫同資料夾的 {DEFAULT_PROFILE_FILENAME}")
    args = parser.parse_args(argv)
    if args.detail_months < 0:
        parser.error("--detail-months 不可為負數")
    if args.dated:
        args.out = args.out.with_stem(f"{args.out.stem}_{datetime.date.today().isoformat()}")
    try:
        out = build(args.db_path, args.out, args.detail_months, not args.no_gzip,
                    args.athlete_id, args.copy_to, args.profile_path, also_txt=args.txt,
                    keep_long_km=args.keep_long_km)
    except SecurityScanError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    size_kb = out.stat().st_size / 1024
    print(f"已產生 {out}（{size_kb:.0f} KB）")
    if args.copy_to is not None:
        print(f"已複製到 {args.copy_to}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
