"""把 training_load.py／readiness.py 的純函式接到真實資料庫（Issue #21）。

training_load.py 的 compute_daily_loads()／compute_training_load_series()
與 readiness.py 的 assess_readiness() 全部刻意不碰資料庫，只吃合成的
list[dict] 輸入，方便單元測試。本模組是資料庫存取層：吃 sqlite3.Connection，
負責把 activities／daily_wellness／athlete_profile 的真實資料查出來、組成
這些純函式要的輸入格式，再串起完整流程。不 import fastapi，比照
dashboard_queries.py／training_plan_store.py 的分層方式。

（suggest_recovery_threshold(conn, athlete_id) 本身已是資料庫層，不在本模組
範圍內，直接呼叫 readiness.suggest_recovery_threshold() 即可。）

easy_pace_fast_sec_per_km 的降級行為——對應 Issue #21 Solution 段落：
compute_daily_loads() 的跑步配速估算退回路徑需要這個參數（來自 VDOT 引擎的
easy 配速區間下界），但本模組不負責呼叫 vdot_engine——呼叫端若有可用的 VDOT
結果，自行傳入；沒有時傳 None，讓 compute_daily_loads() 內建「無法估算則
排除」的既有降級邏輯自然生效，不在本模組內另外報錯或中止查詢。
"""

from __future__ import annotations

import datetime
import sqlite3
from typing import Any

from src.main.python.services.readiness import HRV_TREND_LOOKBACK_DAYS, assess_readiness
from src.main.python.services.training_load import (
    CTL_WARMUP_DAYS,
    compute_daily_loads,
    compute_training_load_series,
)

def get_activities_for_load(
    conn: sqlite3.Connection,
    athlete_id: int,
    start_date: datetime.date,
    end_date: datetime.date,
) -> list[dict[str, Any]]:
    """查出 [start_date, end_date] 區間內、與訓練負荷計算相關的活動。

    所有活動類型都撈：哪些計入負荷（如非跑步活動的時長門檻）由
    training_load.compute_activity_load() 判斷，查詢層不重複這條規則。

    回傳每筆至少含 training_load.compute_activity_load() 需要的欄位：
    "date"（datetime.date）／"activity_type"／"duration_sec"／"avg_hr_bpm"／
    "avg_pace_sec_per_km"。同一天可能有多筆（compute_daily_loads() 會加總）。
    """
    rows = conn.execute(
        """
        SELECT date(started_at) AS activity_date, activity_type, duration_sec,
               avg_hr_bpm, avg_pace_sec_per_km
        FROM activities
        WHERE athlete_id = ?
          AND date(started_at) >= ? AND date(started_at) <= ?
        ORDER BY started_at
        """,
        (athlete_id, start_date.isoformat(), end_date.isoformat()),
    ).fetchall()

    return [
        {
            "date": datetime.date.fromisoformat(row["activity_date"]),
            "activity_type": row["activity_type"],
            "duration_sec": row["duration_sec"],
            "avg_hr_bpm": row["avg_hr_bpm"],
            "avg_pace_sec_per_km": row["avg_pace_sec_per_km"],
        }
        for row in rows
    ]


def get_hr_params(
    conn: sqlite3.Connection, athlete_id: int
) -> tuple[float | None, float | None]:
    """取得該學員的 max_hr_bpm／resting_hr_bpm，供心率強度換算使用。

    回傳 (max_hr_bpm, resting_hr_bpm)。學員不存在或欄位未設定時對應位置為
    None——不報錯，讓下游純函式（compute_activity_load 等）既有的降級邏輯
    （缺心率退回配速估算，或明確排除）自然生效。
    """
    row = conn.execute(
        "SELECT max_hr_bpm, resting_hr_bpm FROM athlete_profile WHERE id = ?",
        (athlete_id,),
    ).fetchone()
    if row is None:
        return None, None
    return row["max_hr_bpm"], row["resting_hr_bpm"]


def get_dates_without_any_data(
    conn: sqlite3.Connection,
    athlete_id: int,
    start_date: datetime.date,
    end_date: datetime.date,
) -> set[datetime.date]:
    """找出 [start_date, end_date] 內、activities 與 daily_wellness 皆完全
    無任何紀錄的日期（N-13）。

    這與「查過、當天負荷為 0」不同——compute_daily_loads() 對沒有訓練的
    日子一律填 load=0.0，讀不出「今天到底有沒有同步資料」；本函式直接查
    兩張來源表本身有沒有列，供 assess_readiness() 判斷該日是否連「評估的
    前提」都不存在（例如尚未執行 Garmin 同步的最新幾天）。

    只看該 athlete 是否有任一筆活動（不限 activity_type，因為只要有活動
    紀錄就代表這天資料已同步，即使該活動類型不列入負荷計算）或任一筆
    daily_wellness 紀錄。
    """
    dates_with_data: set[datetime.date] = set()
    for row in conn.execute(
        """
        SELECT DISTINCT date(started_at) AS d FROM activities
        WHERE athlete_id = ? AND date(started_at) >= ? AND date(started_at) <= ?
        """,
        (athlete_id, start_date.isoformat(), end_date.isoformat()),
    ):
        dates_with_data.add(datetime.date.fromisoformat(row["d"]))
    for row in conn.execute(
        """
        SELECT DISTINCT date FROM daily_wellness
        WHERE athlete_id = ? AND date >= ? AND date <= ?
        """,
        (athlete_id, start_date.isoformat(), end_date.isoformat()),
    ):
        dates_with_data.add(datetime.date.fromisoformat(row["date"]))

    all_dates = {
        start_date + datetime.timedelta(days=offset)
        for offset in range((end_date - start_date).days + 1)
    }
    return all_dates - dates_with_data


def get_wellness_for_readiness(
    conn: sqlite3.Connection,
    athlete_id: int,
    start_date: datetime.date,
    end_date: datetime.date,
) -> list[dict[str, Any]]:
    """查出 [start_date, end_date] 區間內、供 readiness.assess_readiness() 使用的每日身體數據。

    回傳每筆至少含 "date"（datetime.date）／"hrv_ms"／"hrv_weekly_avg_ms"。
    某天缺紀錄的日期不會出現在回傳清單中——assess_readiness() 對缺紀錄的日期
    本來就視為該天 HRV 維度資料缺失，呼叫端不需要為缺漏日期補上佔位列。

    ⚠️ 呼叫端若要讓 assess_readiness() 正確做 HRV 充足度檢查，必須把
    start_date 往前推 readiness.HRV_LOOKBACK_DAYS 天再傳進來，理由見
    compute_readiness_for_athlete()。本函式只忠實回傳被要求的區間，
    不自行擴張——它也被其他不需要回看的呼叫端使用。
    """
    rows = conn.execute(
        """
        SELECT date, hrv_ms, hrv_weekly_avg_ms
        FROM daily_wellness
        WHERE athlete_id = ? AND date >= ? AND date <= ?
        ORDER BY date
        """,
        (athlete_id, start_date.isoformat(), end_date.isoformat()),
    ).fetchall()

    return [
        {
            "date": datetime.date.fromisoformat(row["date"]),
            "hrv_ms": row["hrv_ms"],
            "hrv_weekly_avg_ms": row["hrv_weekly_avg_ms"],
        }
        for row in rows
    ]


def compute_training_load_for_athlete(
    conn: sqlite3.Connection,
    athlete_id: int,
    start_date: datetime.date,
    end_date: datetime.date,
    easy_pace_fast_sec_per_km: float | None = None,
) -> list[dict[str, Any]]:
    """完整流程：查活動 → 每日負荷 → ATL/CTL/TSB 時間序列。

    抽出本函式（原本是 compute_readiness_for_athlete 的前半段）供
    dashboard_queries.get_training_load()（Dashboard「訓練負荷」面板）
    與 compute_readiness_for_athlete() 共用，避免暖機期查詢邏輯出現
    第二份複本（見本模組頂端「不重複造輪子」原則）。

    easy_pace_fast_sec_per_km：見本模組頂端說明，未提供 VDOT 結果時傳 None，
    跑步活動缺心率又缺此參數的情況會被 compute_daily_loads() 明確排除，
    不影響其餘可估算的活動。

    回傳：compute_training_load_series() 的輸出，依日期升冪排序，
    涵蓋 [start_date, end_date] 整段區間（暖機期本身不出現在回傳結果中）。

    ATL/CTL 暖機期：compute_training_load_series() 把序列第一天的 ATL/CTL
    直接設為當天負荷（見該函式 docstring），若序列就從使用者要求的 start_date
    開始算，正好撞上高強度訓練日時，ATL（7 天時間常數）與 CTL（42 天時間常數）
    會以不同速度收斂，短期內把 TSB 撐出不合理的極端值——且同一天的判讀結果會
    因為呼叫端傳入的 start_date 不同而改變，不可接受。故這裡往前多抓
    CTL_WARMUP_DAYS 天的活動資料讓 ATL/CTL 先行收斂，再只取使用者要的
    [start_date, end_date] 切片回傳，暖機期本身不出現在回傳結果中。

    （2026-08-21 修正：先前這裡只抓 CTL_TIME_CONSTANT_DAYS，即 1 倍時間常數，
    對 ATL 夠用但對 CTL 自己不夠收斂，實測仍會出現「同一天因 start_date
    不同而判讀不同」的問題，改抓 training_load.CTL_WARMUP_DAYS，理由見
    該常數的定義處。）
    """
    max_hr_bpm, resting_hr_bpm = get_hr_params(conn, athlete_id)

    warmup_start = start_date - datetime.timedelta(days=CTL_WARMUP_DAYS)
    activities = get_activities_for_load(conn, athlete_id, warmup_start, end_date)
    daily_loads = compute_daily_loads(
        activities,
        warmup_start,
        end_date,
        max_hr_bpm=max_hr_bpm,
        resting_hr_bpm=resting_hr_bpm,
        easy_pace_fast_sec_per_km=easy_pace_fast_sec_per_km,
    )
    full_series = compute_training_load_series(daily_loads)
    return [day for day in full_series if day["date"] >= start_date]


def compute_readiness_for_athlete(
    conn: sqlite3.Connection,
    athlete_id: int,
    start_date: datetime.date,
    end_date: datetime.date,
    easy_pace_fast_sec_per_km: float | None = None,
) -> list[dict[str, Any]]:
    """完整流程：查活動 → 每日負荷 → ATL/CTL/TSB → 查 wellness → readiness 判讀。

    easy_pace_fast_sec_per_km：見本模組頂端說明，未提供 VDOT 結果時傳 None，
    跑步活動缺心率又缺此參數的情況會被 compute_daily_loads() 明確排除，
    不影響其餘可估算的活動。

    個人化恢復閾值（athlete_profile.high_risk_consecutive_training_days）
    在此一併查出並傳入 assess_readiness()，未設定（NULL）時該函式自行退回
    預設值，本函式不重複那段降級邏輯。

    回傳：readiness.assess_readiness() 的輸出，依日期升冪排序，涵蓋
    [start_date, end_date] 整段區間。

    N-13：[start_date, end_date] 內若某天 activities／daily_wellness 皆無
    任何紀錄（尚未同步或無資料），該天的 readiness 為 None 而非 "normal"，
    見 get_dates_without_any_data() 與 assess_readiness() 的說明。

    ⚠️ 本函式需要完整序列（含暖機期）建立連續訓練天數查詢表，故不能直接
    呼叫 compute_training_load_for_athlete()（它只回傳切片後的區間）——
    兩者各自獨立重算一次查詢與遞推，成本是兩張千筆小表的 JOIN，可忽略。
    """
    max_hr_bpm, resting_hr_bpm = get_hr_params(conn, athlete_id)

    warmup_start = start_date - datetime.timedelta(days=CTL_WARMUP_DAYS)
    activities = get_activities_for_load(conn, athlete_id, warmup_start, end_date)
    daily_loads = compute_daily_loads(
        activities,
        warmup_start,
        end_date,
        max_hr_bpm=max_hr_bpm,
        resting_hr_bpm=resting_hr_bpm,
        easy_pace_fast_sec_per_km=easy_pace_fast_sec_per_km,
    )
    training_load_series = compute_training_load_series(daily_loads)

    # HRV 的充足度檢查會往前回看 HRV_LOOKBACK_DAYS 天數算「有幾個有效測量點」，
    # 而 assess_readiness() 只看得到這裡傳進去的列。若只查 [start_date, end_date]，
    # 區間最前面幾天的回看視窗會落在資料之外，即使 daily_wellness 天天有值也會
    # 被誤判為「測量點不足、不評」——單日查詢（教練最常問的「今天能不能練」）
    # 必然中招。與上方 activities 的 warmup 是同一個形狀的問題，故同樣往前多抓。
    # HRV 趨勢（SWC）要回看 7 日滾動＋28 天基線，比充足度檢查更長，取兩者較長者。
    wellness_start = start_date - datetime.timedelta(days=HRV_TREND_LOOKBACK_DAYS)
    daily_wellness = get_wellness_for_readiness(conn, athlete_id, wellness_start, end_date)

    # N-13：只在呼叫端要求輸出的區間內查「完全無資料的日期」，暖機期
    # （warmup_start~start_date 之間）不需要這個檢查——那段本來就只用於
    # ATL/CTL 收斂與連續訓練天數查詢表，不會被輸出。
    dates_without_any_data = get_dates_without_any_data(conn, athlete_id, start_date, end_date)

    threshold_row = conn.execute(
        "SELECT high_risk_consecutive_training_days FROM athlete_profile WHERE id = ?",
        (athlete_id,),
    ).fetchone()
    high_risk_consecutive_training_days = (
        threshold_row["high_risk_consecutive_training_days"] if threshold_row else None
    )

    # HRmax 的來源標記一併傳下去，讓 TSB 維度的觸發帶上信賴度
    # （負荷以 %HRR 推算，HRmax 非實測則整條 ATL/CTL/TSB 同步偏移）。
    # 不改 get_hr_params() 的回傳形狀——它有多處呼叫端，另查一次成本極低。
    source_row = conn.execute(
        "SELECT max_hr_source FROM athlete_profile WHERE id = ?", (athlete_id,)
    ).fetchone()
    max_hr_source = source_row["max_hr_source"] if source_row else None

    return assess_readiness(
        training_load_series,
        daily_wellness,
        high_risk_consecutive_training_days=high_risk_consecutive_training_days,
        output_from_date=start_date,
        max_hr_source=max_hr_source,
        dates_without_any_data=dates_without_any_data,
    )
