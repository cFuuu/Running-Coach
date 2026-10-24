"""`/api/garmin-cards`：Garmin 網頁式卡片牆的資料（趨勢頁「Garmin 數據」類別與首頁體能狀況共用）。

與 dashboard_queries 的事實層分開放，是刻意的邊界：這裡的每個數字都是
Garmin 的輸出（含黑盒衍生值，例如訓練準備度、身體能量、HRV 狀態），
**只供顯示**——規則引擎與 AI 教練不讀這支，也不該 import 這個模組。
唯一由本專案補上的是 VO2max 分級（查 Garmin 手冊同一份 Cooper Institute
公開表，因為 API 不提供分級）與熱血時間的週累計（Garmin 規則：高強度
分鐘數計兩倍），兩者都在回傳中標明來源。

每張卡各自取「區間內該指標最後一個有值的日子」，並附上那天的日期——
睡眠分數常隔日才算出，不能假設每張卡的最新值都落在同一天。
"""

from __future__ import annotations

import datetime
import json
import sqlite3
from typing import Any

from src.main.python.services.dashboard_queries import resolve_athlete_id, resolve_range

GARMIN_NOTE = "Garmin 數據：手錶與 Garmin Connect 的輸出，僅供顯示，不納入本專案的判斷。"

# VO2max 分級：Garmin 手冊「VO2 Max. Standard Ratings」（資料來源 The Cooper
# Institute）。每列為該年齡層 95／80／60／40 百分位的下限，低於 40 百分位即「不佳」。
# 2026-10-22 自 Garmin Forerunner 55 手冊頁核對門檻：
# https://www8.garmin.com/manuals-apac/webhelp/forerunner55/EN-SG/GUID-B0ED81E9-0F80-415F-A27D-C7BF197EE566-2379.html
# ⚠️ 中文名稱用 Garmin 台灣網站的譯名（Fu 提供截圖）：95＝優異、80＝傑出。
# 不可照英文 Superior 直譯成「傑出」——會整排錯一級（52.5 在 Garmin 網頁是「傑出」）。
VO2MAX_RATING_LABELS = ("優異", "傑出", "良好", "普通", "不佳")
VO2MAX_RATING_TABLE: dict[str, tuple[tuple[int, int, tuple[float, float, float, float]], ...]] = {
    "MALE": (
        (20, 29, (55.4, 51.1, 45.4, 41.7)),
        (30, 39, (54.0, 48.3, 44.0, 40.5)),
        (40, 49, (52.5, 46.4, 42.4, 38.5)),
        (50, 59, (48.9, 43.4, 39.2, 35.6)),
        (60, 69, (45.7, 39.5, 35.5, 32.3)),
        (70, 79, (42.1, 36.7, 32.3, 29.4)),
    ),
    "FEMALE": (
        (20, 29, (49.6, 43.9, 39.5, 36.1)),
        (30, 39, (47.4, 42.4, 37.8, 34.4)),
        (40, 49, (45.3, 39.7, 36.3, 33.0)),
        (50, 59, (41.1, 36.7, 33.0, 30.1)),
        (60, 69, (37.8, 33.0, 30.0, 27.5)),
        (70, 79, (36.7, 30.9, 28.1, 25.9)),
    ),
}
VO2MAX_RATING_SOURCE = "依 Garmin 手冊的 Cooper Institute 分級表（API 不提供分級）"

# Garmin 熱血時間規則：高強度分鐘數計兩倍，週一起算
VIGOROUS_MINUTE_WEIGHT = 2

_WELLNESS_COLUMNS = (
    "hrv_ms", "hrv_weekly_avg_ms", "hrv_last_night_5min_high", "hrv_status",
    "hrv_baseline_low_upper", "hrv_baseline_balanced_low", "hrv_baseline_balanced_upper",
    "training_readiness_score", "readiness_level", "readiness_feedback_short",
    "readiness_sleep_feedback", "readiness_recovery_feedback", "readiness_hrv_feedback",
    "readiness_load_feedback", "readiness_sleep_history_feedback", "readiness_stress_feedback",
    "recovery_time_hours", "acute_load",
    "sleep_score", "sleep_duration_sec", "sleep_start_local", "sleep_end_local",
    "sleep_deep_sec", "sleep_light_sec", "sleep_rem_sec", "sleep_awake_sec",
    "steps", "steps_goal",
    "body_battery_latest", "body_battery_charged", "body_battery_drained",
    "body_battery_max", "body_battery_min", "body_battery_at_wake",
    "resting_hr_bpm", "rhr_7day_avg_bpm",
    "calories_active", "calories_resting",
    "all_day_stress_avg",
    "intensity_minutes_moderate", "intensity_minutes_vigorous", "intensity_minutes_weekly_goal",
    "floors_ascended", "floors_goal",
    "spo2_pct", "spo2_lowest", "spo2_latest",
    "respiration_rate", "respiration_sleep_avg", "respiration_low", "respiration_high",
)


def vo2max_rating(value: float | None, sex: str | None, age: int | None) -> dict | None:
    """查 Cooper Institute 表得出分級；缺性別、年齡或超出表格年齡範圍時回 None。

    回傳附上該年齡層的門檻（由低到高：普通、良好、傑出、優異的下限），
    供前端畫分段量表。
    """
    if value is None or age is None or sex not in VO2MAX_RATING_TABLE:
        return None
    for lo, hi, cutoffs in VO2MAX_RATING_TABLE[sex]:
        if lo <= age <= hi:
            label = VO2MAX_RATING_LABELS[-1]
            for name, cutoff in zip(VO2MAX_RATING_LABELS, cutoffs):
                if value >= cutoff:
                    label = name
                    break
            return {"label": label, "cutoffs": list(reversed(cutoffs)), "source": VO2MAX_RATING_SOURCE}
    return None


def _window_rows(conn: sqlite3.Connection, table: str, columns: tuple[str, ...],
                 athlete_id: int, window: dict) -> list[sqlite3.Row]:
    sql = f"SELECT date, {', '.join(columns)} FROM {table} WHERE athlete_id = ? AND date <= ?"  # noqa: S608
    params: list[Any] = [athlete_id, window["end_date"]]
    if window["start_date"]:
        sql += " AND date >= ?"
        params.append(window["start_date"])
    return list(conn.execute(sql + " ORDER BY date", params))


def _latest(rows: list[sqlite3.Row], key: str) -> sqlite3.Row | None:
    """區間內 key 最後一個有值的那一列。"""
    for row in reversed(rows):
        if row[key] is not None:
            return row
    return None


def _series(rows: list[sqlite3.Row], *keys: str) -> list[dict]:
    """每日序列；所有 key 都沒值的日子不出現（前端斷線，不補 0）。"""
    return [
        {"date": r["date"], **{k: r[k] for k in keys}}
        for r in rows
        if any(r[k] is not None for k in keys)
    ]


def _intraday(conn: sqlite3.Connection, athlete_id: int, date: str | None, kind: str) -> list | None:
    if date is None:
        return None
    row = conn.execute(
        "SELECT points_json FROM wellness_intraday WHERE athlete_id = ? AND date = ? AND kind = ?",
        (athlete_id, date, kind),
    ).fetchone()
    return json.loads(row["points_json"]) if row else None


def _card(rows: list[sqlite3.Row], key: str, fields: dict[str, str]) -> dict | None:
    """以 key 決定最新日，把 fields（輸出名 → 欄位）取自那一天。"""
    row = _latest(rows, key)
    if row is None:
        return None
    return {"date": row["date"], **{name: row[col] for name, col in fields.items()}}


def _status_prefix(phrase: str | None) -> str | None:
    """PRODUCTIVE_3 → PRODUCTIVE：底線後的數字是 Garmin 回饋文案變體。"""
    if not phrase:
        return None
    head, _, tail = phrase.rpartition("_")
    return head if head and tail.isdigit() else phrase


def _training_status_card(rows: list[sqlite3.Row]) -> dict | None:
    latest = _latest(rows, "training_status_phrase")
    if latest is None:
        return None
    return {
        "date": latest["date"],
        "status": _status_prefix(latest["training_status_phrase"]),
        # 用 Garmin 自己給的起始日，不由逐日狀態回推（兩者規則不同，見 schema.sql）
        "since": latest["status_since_date"],
        "series": [
            {"date": r["date"], "status": _status_prefix(r["training_status_phrase"])}
            for r in rows if r["training_status_phrase"]
        ],
    }


def _intensity_minutes_card(conn: sqlite3.Connection, athlete_id: int, rows: list[sqlite3.Row]) -> dict | None:
    latest = _latest(rows, "intensity_minutes_moderate")
    if latest is None:
        return None
    day = datetime.date.fromisoformat(latest["date"])
    monday = day - datetime.timedelta(days=day.weekday())
    week = list(conn.execute(
        "SELECT date, intensity_minutes_moderate AS moderate, intensity_minutes_vigorous AS vigorous "
        "FROM daily_wellness WHERE athlete_id = ? AND date >= ? AND date <= ? ORDER BY date",
        (athlete_id, monday.isoformat(), latest["date"]),
    ))
    total = sum((r["moderate"] or 0) + VIGOROUS_MINUTE_WEIGHT * (r["vigorous"] or 0) for r in week)
    return {
        "date": latest["date"],
        "week_start": monday.isoformat(),
        "week_total": total,
        "weekly_goal": latest["intensity_minutes_weekly_goal"],
        "rule": "週一起算，高強度分鐘數計兩倍（Garmin 規則）",
        "daily": [{"date": r["date"], "moderate": r["moderate"], "vigorous": r["vigorous"]} for r in week],
    }


# 健康狀態卡的指標順序，對照 Garmin 網頁：心率、HRV、呼吸、脈搏血氧（皮膚溫度有值才接在後面）
HEALTH_STATUS_ORDER = ("HR", "HRV", "RESPIRATION", "SPO2", "SKIN_TEMP_C")


def _health_status_card(conn: sqlite3.Connection, athlete_id: int, window: dict) -> dict | None:
    """區間內最後一天的健康狀態。範圍與判定都是 Garmin 給的，這裡只排序與計數。"""
    params: list[Any] = [athlete_id, window["end_date"]]
    sql = "SELECT MAX(date) AS d FROM health_status WHERE athlete_id = ? AND date <= ?"
    if window["start_date"]:
        sql += " AND date >= ?"
        params.append(window["start_date"])
    latest = conn.execute(sql, params).fetchone()["d"]
    if latest is None:
        return None
    rows = {
        r["metric"]: r for r in conn.execute(
            "SELECT metric, value, baseline_lower, baseline_upper, status, percentage "
            "FROM health_status WHERE athlete_id = ? AND date = ?",
            (athlete_id, latest),
        )
    }
    metrics = [
        {"metric": m, "value": rows[m]["value"], "lower": rows[m]["baseline_lower"],
         "upper": rows[m]["baseline_upper"], "status": rows[m]["status"],
         "percentage": rows[m]["percentage"]}
        for m in HEALTH_STATUS_ORDER if m in rows
    ]
    return {
        "date": latest,
        # 與 Garmin 的 outliersCount 同義：狀態不是 IN_RANGE 的指標數
        "outliers": sum(1 for m in metrics if m["status"] != "IN_RANGE"),
        "metrics": metrics,
    }


def get_garmin_cards(
    conn: sqlite3.Connection,
    athlete_id: int | None = None,
    range_key: str = "30d",
) -> dict:
    """Garmin 卡片牆：每張卡的最新值（附日期）＋區間內每日序列＋最新日的日內序列。"""
    resolved = resolve_athlete_id(conn, athlete_id)
    window = resolve_range(conn, resolved, range_key)
    if resolved is None:
        return {**window, "note": GARMIN_NOTE, "cards": {}}

    w = _window_rows(conn, "daily_wellness", _WELLNESS_COLUMNS, resolved, window)
    ts = _window_rows(
        conn, "training_status",
        ("training_status_phrase", "status_since_date", "acute_load", "chronic_load", "acwr_ratio", "acwr_status",
         "chronic_load_optimal_min", "chronic_load_optimal_max", "vo2max_precise", "vo2max_date",
         "monthly_load_aerobic_low", "monthly_load_aerobic_high", "monthly_load_anaerobic",
         "monthly_load_aerobic_low_target_min", "monthly_load_aerobic_low_target_max",
         "monthly_load_aerobic_high_target_min", "monthly_load_aerobic_high_target_max",
         "monthly_load_anaerobic_target_min", "monthly_load_anaerobic_target_max",
         "training_balance_feedback"),
        resolved, window,
    )
    pm = _window_rows(
        conn, "performance_metrics",
        ("lactate_threshold_hr_bpm", "lactate_threshold_pace_sec_per_km",
         "functional_threshold_power", "endurance_score", "endurance_classification",
         "chronological_age"),
        resolved, window,
    )
    profile = conn.execute("SELECT sex FROM athlete_profile WHERE id = ?", (resolved,)).fetchone()

    cards: dict[str, Any] = {}

    hrv = _card(w, "hrv_weekly_avg_ms", {
        "status": "hrv_status", "weekly_avg_ms": "hrv_weekly_avg_ms", "last_night_ms": "hrv_ms",
        "last_night_5min_high_ms": "hrv_last_night_5min_high",
        "baseline_low_upper": "hrv_baseline_low_upper",
        "baseline_balanced_low": "hrv_baseline_balanced_low",
        "baseline_balanced_upper": "hrv_baseline_balanced_upper",
    })
    if hrv:
        # 每日平衡區間一併送出，前端用來畫 Garmin 風格的「區間帶＋7 天平均點」趨勢圖
        hrv["series"] = _series(w, "hrv_weekly_avg_ms", "hrv_ms",
                                "hrv_baseline_balanced_low", "hrv_baseline_balanced_upper")
        cards["hrv"] = hrv

    readiness = _card(w, "training_readiness_score", {
        "score": "training_readiness_score", "level": "readiness_level",
        "feedback_short": "readiness_feedback_short",
        # 因素清單（Garmin 網頁的「因素」）：評級代碼＋對應的數值
        "sleep_feedback": "readiness_sleep_feedback", "sleep_score": "sleep_score",
        "recovery_feedback": "readiness_recovery_feedback", "recovery_time_hours": "recovery_time_hours",
        "hrv_feedback": "readiness_hrv_feedback", "hrv_weekly_avg_ms": "hrv_weekly_avg_ms",
        "hrv_status": "hrv_status",
        "load_feedback": "readiness_load_feedback", "acute_load": "acute_load",
        "sleep_history_feedback": "readiness_sleep_history_feedback",
        "stress_feedback": "readiness_stress_feedback",
    })
    if readiness:
        readiness["series"] = _series(w, "training_readiness_score")
        cards["training_readiness"] = readiness

    sleep = _card(w, "sleep_score", {
        "score": "sleep_score", "duration_sec": "sleep_duration_sec",
        "start_local": "sleep_start_local", "end_local": "sleep_end_local",
        "deep_sec": "sleep_deep_sec", "light_sec": "sleep_light_sec",
        "rem_sec": "sleep_rem_sec", "awake_sec": "sleep_awake_sec",
    })
    if sleep:
        sleep["levels"] = _intraday(conn, resolved, sleep["date"], "sleep_levels")
        sleep["series"] = _series(w, "sleep_score")
        cards["sleep"] = sleep

    steps = _card(w, "steps", {"value": "steps", "goal": "steps_goal"})
    if steps:
        steps["series"] = _series(w, "steps", "steps_goal")
        cards["steps"] = steps

    status = _training_status_card(ts)
    if status:
        cards["training_status"] = status

    battery = _card(w, "body_battery_max", {
        "latest": "body_battery_latest", "charged": "body_battery_charged",
        "drained": "body_battery_drained", "high": "body_battery_max",
        "low": "body_battery_min", "at_wake": "body_battery_at_wake",
    })
    if battery:
        battery["intraday"] = _intraday(conn, resolved, battery["date"], "body_battery")
        battery["series"] = _series(w, "body_battery_max", "body_battery_min")
        cards["body_battery"] = battery

    heart = _card(w, "resting_hr_bpm", {"resting": "resting_hr_bpm", "resting_7day_avg": "rhr_7day_avg_bpm"})
    if heart:
        heart["intraday"] = _intraday(conn, resolved, heart["date"], "heart_rate")
        heart["series"] = _series(w, "resting_hr_bpm")
        cards["heart_rate"] = heart

    calories = _card(w, "calories_resting", {"active": "calories_active", "resting": "calories_resting"})
    if calories:
        calories["series"] = _series(w, "calories_active", "calories_resting")
        cards["calories"] = calories

    stress = _card(w, "all_day_stress_avg", {"avg": "all_day_stress_avg"})
    if stress:
        stress["intraday"] = _intraday(conn, resolved, stress["date"], "stress")
        stress["series"] = _series(w, "all_day_stress_avg")
        cards["stress"] = stress

    intensity = _intensity_minutes_card(conn, resolved, w)
    if intensity:
        cards["intensity_minutes"] = intensity

    floors = _card(w, "floors_ascended", {"value": "floors_ascended", "goal": "floors_goal"})
    if floors:
        floors["series"] = _series(w, "floors_ascended")
        cards["floors"] = floors

    spo2 = _card(w, "spo2_pct", {"avg": "spo2_pct", "lowest": "spo2_lowest", "latest": "spo2_latest"})
    if spo2:
        spo2["series"] = _series(w, "spo2_pct")
        cards["spo2"] = spo2

    respiration = _card(w, "respiration_rate", {
        "waking_avg": "respiration_rate", "sleep_avg": "respiration_sleep_avg",
        "low": "respiration_low", "high": "respiration_high",
    })
    if respiration:
        respiration["series"] = _series(w, "respiration_rate", "respiration_sleep_avg")
        cards["respiration"] = respiration

    vo2 = _latest(ts, "vo2max_precise")
    if vo2:
        age_row = _latest(pm, "chronological_age")
        cards["vo2max"] = {
            "date": vo2["date"],
            "value": vo2["vo2max_precise"],
            "measured_on": vo2["vo2max_date"],
            "rating": vo2max_rating(
                vo2["vo2max_precise"],
                profile["sex"] if profile else None,
                age_row["chronological_age"] if age_row else None,
            ),
            "series": _series(ts, "vo2max_precise"),
        }

    lt = _card(pm, "lactate_threshold_hr_bpm", {
        "hr_bpm": "lactate_threshold_hr_bpm",
        "pace_sec_per_km": "lactate_threshold_pace_sec_per_km",
        "power_watts": "functional_threshold_power",
    })
    if lt:
        cards["lactate_threshold"] = lt

    endurance = _card(pm, "endurance_score", {
        "score": "endurance_score", "classification": "endurance_classification",
    })
    if endurance:
        endurance["series"] = _series(pm, "endurance_score")
        cards["endurance_score"] = endurance

    load = _card(ts, "acute_load", {
        "acute": "acute_load", "chronic": "chronic_load", "ratio": "acwr_ratio",
        "ratio_status": "acwr_status",
        "chronic_optimal_min": "chronic_load_optimal_min",
        "chronic_optimal_max": "chronic_load_optimal_max",
    })
    if load:
        load["series"] = _series(ts, "acute_load", "chronic_load")
        cards["training_load"] = load

    health = _health_status_card(conn, resolved, window)
    if health:
        cards["health_status"] = health

    focus = _latest(ts, "monthly_load_aerobic_low")
    if focus:
        cards["load_focus"] = {
            "date": focus["date"],
            "feedback": focus["training_balance_feedback"],
            # Garmin 負荷焦點：近 4 週三類負荷與各自目標範圍
            "categories": [
                {"key": key, "value": focus[f"monthly_load_{key}"],
                 "target_min": focus[f"monthly_load_{key}_target_min"],
                 "target_max": focus[f"monthly_load_{key}_target_max"]}
                for key in ("anaerobic", "aerobic_high", "aerobic_low")
            ],
        }

    return {**window, "note": GARMIN_NOTE, "cards": cards}
