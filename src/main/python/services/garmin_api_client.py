"""Garmin Connect API 的薄封裝：只負責取得 raw JSON，不做正規化。

分層原則（比照 `fit_parser.py` / `fit_import_runner.py` 的既有慣例）：
本模組**只抓資料**，回傳 Garmin API 的原始結構；單位換算、欄位對應、
去重與寫入一律屬於 `garmin_sync_runner.py` 的職責。這讓本模組可以在
不碰資料庫的情況下單獨測試。

認證一律透過 `garmin_auth.load_client()`（只讀 token），本模組**不含**
任何帳密登入路徑——理由見 `garmin_auth` 模組 docstring 與 CLAUDE.md §🔐。

## 實測要點（2026-09-01 對 Fu 的真實帳號驗證）

- **單位是標準公尺／秒**，與官方匯出包的 `CENTIMETER`／`MILLISECOND`
  **不同**。例：`distance=10158.57`（公尺）對應 10.16km。故正規化層
  不可重用 `garmin_export_parser.py` 的換算係數。
- **取活動摘要必須用 `get_activities_by_date()`**，不是 `get_activity()`
  ——後者實測只回 13 欄且 `distance`／`duration`／`averageHR` 全為 `None`；
  前者回 111 欄且填滿。
- 逐項比對 DB 既有值（距離／時長／avgHR／maxHR）全數一致。
"""

from __future__ import annotations

import datetime
import time
from typing import Any

from src.main.python.services.garmin_auth import GarminAuthError, load_client

# 每次 API 呼叫之間的間隔（秒）。Garmin 對登入端點有明確 rate limit，
# 一般查詢端點雖未見上限，仍自我節制以免觸發風控——同步是背景作業，
# 慢一點沒有代價，被鎖帳號才有。
DEFAULT_REQUEST_INTERVAL_SEC = 1.0

# 單次同步允許回溯的最大天數，避免手滑打出巨量請求。
MAX_SYNC_DAYS = 365


class GarminFetchError(Exception):
    """抓取資料失敗（逾時、rate limit、伺服器錯誤等）。"""


class GarminRateLimited(GarminFetchError):
    """觸發 Garmin rate limit（429）——應停止本次同步，稍後再試。

    ⚠️ 攔到此例外時**不要**立即重試。Garmin 的限制以 IP 為單位累積，
    連續重試只會延長封鎖時間。
    """


def _classify(e: Exception) -> GarminFetchError:
    """把底層例外轉為本模組的錯誤型別。

    ⚠️ 不把原始訊息原樣帶出——它可能含 request header／token 片段
    （CLAUDE.md §🔐 第 3 條）。只保留型別名稱與 429 的判定結果。
    """
    name = type(e).__name__
    text = f"{name}: {e}"
    if "429" in text or "TooManyRequests" in name or "RateLimit" in name:
        return GarminRateLimited(
            f"觸發 Garmin rate limit（{name}）。請停止同步並等待數分鐘，"
            "勿連續重試——限制以 IP 累積，重試會延長封鎖時間。"
        )
    return GarminFetchError(f"抓取失敗（{name}）")


class GarminApiClient:
    """已認證的 Garmin client，附自我節制的請求間隔。

    用法：
        client = GarminApiClient()          # 只讀 token，未認證會拋 GarminAuthRequired
        acts = client.fetch_activities(start_date, end_date)
    """

    def __init__(
        self,
        tokenstore: str | None = None,
        request_interval_sec: float = DEFAULT_REQUEST_INTERVAL_SEC,
        _client: Any = None,
    ) -> None:
        """建立 client。

        Args:
            tokenstore: token 路徑，省略時走 `garmin_auth` 的預設解析順序。
            request_interval_sec: 每次 API 呼叫之間的最小間隔。
            _client: 測試用注入點，傳入後不做真實認證。

        Raises:
            GarminAuthRequired: 尚未認證或 token 失效（由 `load_client` 拋出）。
        """
        self._client = _client if _client is not None else load_client(tokenstore)
        self._interval = request_interval_sec
        self._last_call_at: float | None = None

    def _throttle(self) -> None:
        if self._last_call_at is not None:
            elapsed = time.monotonic() - self._last_call_at
            if elapsed < self._interval:
                time.sleep(self._interval - elapsed)
        self._last_call_at = time.monotonic()

    def _call(self, method_name: str, *args: Any) -> Any:
        """呼叫底層 client 的方法，套用節流與統一的錯誤轉換。"""
        self._throttle()
        try:
            return getattr(self._client, method_name)(*args)
        except Exception as e:
            raise _classify(e) from None

    def _call_kw(self, method_name: str, **kwargs: Any) -> Any:
        """同 `_call()`，但傳 keyword 引數。

        `garminconnect` 有部分方法為 keyword-only（如 `get_lactate_threshold`），
        用位置參數會 `TypeError`；該例外經 `_classify()` 轉為 `GarminFetchError`
        後被呼叫端的容錯吞掉，欄位靜默落成 NULL 而不報錯——故需獨立入口。
        """
        self._throttle()
        try:
            return getattr(self._client, method_name)(**kwargs)
        except Exception as e:
            raise _classify(e) from None

    # --- 活動 ---

    def fetch_activities(
        self, start_date: datetime.date, end_date: datetime.date
    ) -> list[dict]:
        """取得日期區間內的活動摘要（含 `distance`／`duration`／`averageHR` 等）。

        ⚠️ 用 `get_activities_by_date()` 而非 `get_activity()`——後者實測
        回傳的摘要欄位全為 None，見模組 docstring。
        """
        if start_date > end_date:
            raise ValueError(f"start_date ({start_date}) 不可晚於 end_date ({end_date})")
        span = (end_date - start_date).days
        if span > MAX_SYNC_DAYS:
            raise ValueError(
                f"單次同步區間 {span} 天超過上限 {MAX_SYNC_DAYS} 天；"
                "歷史資料請用官方匯出包走 garmin_import_runner。"
            )
        result = self._call(
            "get_activities_by_date", start_date.isoformat(), end_date.isoformat()
        )
        return list(result or [])

    def fetch_activity_splits(self, activity_id: str | int) -> list[dict]:
        """取得單場活動的**逐圈明細**（每公里分圈的距離／時長／心率）。

        ⚠️ 分圈明細**不在**活動摘要裡：`get_activities_by_date` 只回
        `splitSummaries`——那是按 `RWD_RUN`／`RWD_WALK`／`RWD_STAND` 類型
        彙總的總和與平均（`noOfSplits` 只說該類型有幾段），**沒有逐圈序列**；
        `fastestSplit_1000` 之類也只是單一極值。摘要的 `lapCount` 告知圈數
        存在，明細需另打本端點。

        實測（2026-09-01，活動 24171213902）：摘要 `lapCount=16`，本端點回
        `{'activityId', 'lapDTOs', 'eventDTOs'}`，`lapDTOs` 為 16 圈完整明細，
        每圈含 `distance`（公尺）／`duration`（秒）／`averageHR`／`maxHR`。

        查無分圈時回傳空 list。
        """
        result = self._call("get_activity_splits", str(activity_id))
        if not result:
            return []
        return list(result.get("lapDTOs") or [])

    # --- 每日身體數據 ---
    #
    # ⚠️ Training Readiness／Body Battery／HRV status 皆為 Garmin（Firstbeat）
    # 黑盒衍生值。**照抓照存**，但取用時必須帶 `garmin_blackbox` 標記
    # （`unusable`），只可作為校準與對照，不得作為判讀依據。
    # 見 PLAN.md §1「黑盒衍生值照抓照存、只當對照組」。

    def fetch_sleep(self, cdate: datetime.date) -> dict | None:
        """取得單日睡眠資料。"""
        return self._call("get_sleep_data", cdate.isoformat())

    def fetch_hrv(self, cdate: datetime.date) -> dict | None:
        """取得單日 HRV（含 Garmin 的 status 評級，屬黑盒衍生值）。"""
        return self._call("get_hrv_data", cdate.isoformat())

    def fetch_training_readiness(self, cdate: datetime.date) -> list[dict]:
        """取得單日 Training Readiness（⚠️ Garmin 黑盒衍生值）。"""
        return list(self._call("get_training_readiness", cdate.isoformat()) or [])

    def fetch_stats(self, cdate: datetime.date) -> dict | None:
        """取得單日總覽統計（步數、靜止心率、壓力等）。"""
        return self._call("get_stats", cdate.isoformat())

    def fetch_training_status(self, cdate: datetime.date) -> dict | None:
        """取得單日訓練狀態與訓練負荷（Training Status）。

        一支端點同時帶回真 ACWR（急慢性負荷比，Acute:Chronic Workload
        Ratio）、急/慢性負荷、最佳負荷範圍、VO2Max 精確值、熱適應——
        每日僅 +1 次請求。⚠️ 回傳結構以 deviceId 字串為 key（非固定名稱），
        解析邏輯見 `garmin_sync_runner.normalize_training_status()`。
        """
        return self._call("get_training_status", cdate.isoformat())

    def fetch_race_predictions(self) -> dict | None:
        """取得賽事完賽預測（5K／10K／半馬／全馬）。

        簽章為 `(startdate, enddate, _type)` 但三者皆可省略；實測
        （2026-09-02）不帶引數即回傳當前推估值的單筆 dict，
        `fromCalendarDate`／`toCalendarDate` 為 None、`calendarDate`
        為當日——故不帶引數。
        """
        return self._call("get_race_predictions")

    def fetch_lactate_threshold(self) -> dict | None:
        """取得乳酸閾值心率與配速，以及功能性閾值功率（FTP）。

        ⚠️ 此方法為 **keyword-only**（簽章 `(*, latest=True, start_date=None,
        end_date=None, aggregation='daily')`）——傳位置參數會 `TypeError`，
        且該例外會被 `_call()` 轉成 `GarminFetchError` 後由呼叫端吞掉，
        欄位靜默落成 NULL。2026-09-02 實測確認，故明確以 `latest=True` 呼叫。

        回傳為兩個子區塊：`speed_and_heart_rate`（配速與心率）與
        `power`（FTP），非平坦結構。
        """
        return self._call_kw("get_lactate_threshold", latest=True)

    def fetch_endurance_score(self, cdate: datetime.date) -> dict | None:
        """取得耐力分數與來源貢獻拆解（P1，中長期趨勢用）。

        簽章為 `(startdate, enddate=None)`。省略 `enddate` 時回傳該日的
        單筆快照 dict（實測 2026-09-02 含 `overallScore`／`contributors`）；
        帶 `enddate` 則回傳區間彙整，結構不同——本專案只取單日快照。
        """
        return self._call("get_endurance_score", cdate.isoformat())

    def fetch_fitness_age(self, cdate: datetime.date) -> dict | None:
        """取得體能年齡與四項成分（P1）。"""
        return self._call("get_fitnessage_data", cdate.isoformat())

    def fetch_hill_score(self, cdate: datetime.date) -> dict | None:
        """取得爬坡分數（P1）。

        簽章與 `fetch_endurance_score()` 同為 `(startdate, enddate=None)`，
        省略 `enddate` 回傳單日快照。
        """
        return self._call("get_hill_score", cdate.isoformat())

    def fetch_daily_wellness_raw(self, cdate: datetime.date) -> dict:
        """彙整單日各來源的原始回傳，供正規化層使用。

        各項獨立容錯：某一項抓不到（該日無資料）不影響其他項，
        以 `None` 表示。但 rate limit 一律往外拋——那代表應停止整批同步。

        `training_status` 為 2026-09-02 新增（Task 1B-9）：每日 wellness
        端點數由 4 → 5，`garmin_sync_runner._WELLNESS_ENDPOINTS_PER_DAY`
        須同步更新，否則耗時預告會低估。
        """
        out: dict[str, Any] = {"date": cdate.isoformat()}
        for key, fn in (
            ("stats", self.fetch_stats),
            ("sleep", self.fetch_sleep),
            ("hrv", self.fetch_hrv),
            ("training_readiness", self.fetch_training_readiness),
            ("training_status", self.fetch_training_status),
        ):
            try:
                out[key] = fn(cdate)
            except GarminRateLimited:
                raise
            except GarminFetchError:
                out[key] = None
        return out

    def fetch_performance_metrics_raw(self, cdate: datetime.date) -> dict:
        """彙整賽事預測／乳酸閾值／耐力分數／體能年齡／爬坡分數的原始回傳。

        這五個端點語意上是「快照」而非逐日序列（見 `performance_metrics`
        表 schema 註解），故本方法**不**放進 `fetch_daily_wellness_raw()`
        的逐日迴圈，改由呼叫端每次同步只呼叫一次，以同步當下日期落地。

        ⚠️ `get_endurance_score`／`get_fitnessage_data`／`get_hill_score`
        的實際函式簽章（是否吃日期、單日或區間）未經真實帳號驗證
        （審計報告僅確認 `get_race_predictions`／`get_lactate_threshold`
        不吃日期參數）。簽章不符時 `_call()` 會把 `TypeError` 轉為
        `GarminFetchError`，該項獨立容錯為 `None`，不影響其他四項，
        也不會讓整批同步失敗——但仍須待本機對真實帳號驗證後才能確認
        欄位語意正確。
        """
        out: dict[str, Any] = {}
        for key, fn in (
            ("race_predictions", self.fetch_race_predictions),
            ("lactate_threshold", self.fetch_lactate_threshold),
            ("endurance_score", lambda: self.fetch_endurance_score(cdate)),
            ("fitness_age", lambda: self.fetch_fitness_age(cdate)),
            ("hill_score", lambda: self.fetch_hill_score(cdate)),
        ):
            try:
                out[key] = fn()
            except GarminRateLimited:
                raise
            except GarminFetchError:
                out[key] = None
        return out


__all__ = [
    "GarminApiClient",
    "GarminFetchError",
    "GarminRateLimited",
    "GarminAuthError",
    "MAX_SYNC_DAYS",
]
