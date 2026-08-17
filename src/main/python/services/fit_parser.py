"""解析 Garmin FIT 檔，取出每公里分圈與逐秒紀錄。

本模組刻意不碰資料庫：每個函式吃檔案位元組或已載入的資料，回傳純 dict/list，
方便單元測試。資料庫寫入在 fit_import_runner.py。
（與 garmin_export_parser.py / garmin_import_runner.py 的分層方式一致。）

為什麼需要 FIT——2026-08-17 實測結論：
Garmin 官方匯出 JSON 裡的 `splits` 是「手動按錶」的不規則分段（實測某場 10km 只有
2~5 段、長度 3.42/2.97/3.76km，還夾雜 0km 空圈），266 場跑步中僅 94 場有多於 1 段。
FIT 檔裡的 `lap` 訊息才是整齊的每公里分圈（實測某場 21km：22 圈、每圈精準 1000.0m，
且含每圈平均心率），另有逐秒 `record` 可算配速曲線與心率漂移。
"""

from __future__ import annotations

import io
from typing import Any, BinaryIO

import fitparse

# FIT 標準單位：距離公尺、速度公尺/秒、時間秒。與 Garmin 匯出 JSON 的
# 公分/毫秒完全不同（那邊的換算見 garmin_export_parser.py）。
_M_PER_KM = 1000

# 低於此檔案大小的 FIT 幾乎都是每日監測片段而非運動活動，掃描時可先略過。
# 依據：實測 20,778 個 FIT 檔的大小中位數僅 793 bytes（多為每日監測），
# 而資料庫中最短的跑步活動為 107 秒，逐秒紀錄約 69 bytes/筆 → 推估約 7KB。
# 取 3000 bytes 作為安全下限，寧可多解析也不要漏掉短跑步。
DEFAULT_MIN_FILE_BYTES = 3000


def _mps_to_pace_sec_per_km(speed_mps: float | None) -> int | None:
    """公尺/秒 → 每公里秒數。速度為 0 或 None 時回 None（靜止不該算配速）。"""
    if not speed_mps or speed_mps <= 0:
        return None
    return round(_M_PER_KM / speed_mps)


def _duration_to_pace_sec_per_km(distance_m: float | None, duration_sec: float | None) -> int | None:
    if not distance_m or distance_m <= 0 or not duration_sec or duration_sec <= 0:
        return None
    return round(duration_sec / (distance_m / _M_PER_KM))


def _msg_to_dict(message: Any) -> dict:
    return {field.name: field.value for field in message}


def parse_fit(source: bytes | BinaryIO) -> dict | None:
    """解析單一 FIT 檔，回傳 {"session": ..., "laps": [...], "records": [...]}。

    無 session 訊息時回傳 None——實測確實有這種檔案（多為裝置設定或監測片段），
    呼叫端應跳過而非中斷整批匯入。
    """
    stream = io.BytesIO(source) if isinstance(source, bytes) else source
    fit = fitparse.FitFile(stream)

    session_msgs = list(fit.get_messages("session"))
    if not session_msgs:
        return None

    s = _msg_to_dict(session_msgs[0])
    session = {
        "start_time_utc": s.get("start_time"),
        "total_distance_m": s.get("total_distance"),
        "total_elapsed_sec": s.get("total_elapsed_time"),
        "sport": s.get("sport"),
        "sub_sport": s.get("sub_sport"),
    }

    laps = []
    for idx, msg in enumerate(fit.get_messages("lap"), start=1):
        lap = _msg_to_dict(msg)
        distance_m = lap.get("total_distance")
        duration_sec = lap.get("total_elapsed_time")
        laps.append(
            {
                "lap_index": idx,
                "distance_km": round(distance_m / _M_PER_KM, 4) if distance_m else None,
                "duration_sec": duration_sec,
                "pace_sec_per_km": _duration_to_pace_sec_per_km(distance_m, duration_sec),
                "avg_hr_bpm": lap.get("avg_heart_rate"),
                "max_hr_bpm": lap.get("max_heart_rate"),
            }
        )

    records = []
    start_ts = None
    for msg in fit.get_messages("record"):
        r = _msg_to_dict(msg)
        ts = r.get("timestamp")
        if ts is None:
            continue
        if start_ts is None:
            start_ts = ts
        distance_m = r.get("distance")
        # enhanced_speed 精度較高，舊裝置可能只有 speed。
        speed = r.get("enhanced_speed", r.get("speed"))
        records.append(
            {
                "elapsed_sec": int((ts - start_ts).total_seconds()),
                "distance_km": round(distance_m / _M_PER_KM, 4) if distance_m is not None else None,
                "hr_bpm": r.get("heart_rate"),
                "pace_sec_per_km": _mps_to_pace_sec_per_km(speed),
                "cadence_spm": r.get("cadence"),
                "altitude_m": r.get("enhanced_altitude", r.get("altitude")),
            }
        )

    return {"session": session, "laps": laps, "records": records}


def downsample_records(records: list[dict], every_sec: int) -> list[dict]:
    """把逐秒紀錄降頻成每 every_sec 秒一筆。

    只保留每個時間區間的第一筆，不做平均——畫配速/心率曲線與算心率漂移都不需要
    真正的每秒精度，而完整保存 266 場 × 約 9000 筆會產生數百萬列卻無實益。
    第一筆與最後一筆一定保留，避免曲線頭尾被截掉。
    """
    if every_sec <= 1 or not records:
        return records

    kept: list[dict] = []
    next_threshold = 0
    for rec in records:
        if rec["elapsed_sec"] >= next_threshold:
            kept.append(rec)
            next_threshold = rec["elapsed_sec"] + every_sec

    last = records[-1]
    if kept and kept[-1]["elapsed_sec"] != last["elapsed_sec"]:
        kept.append(last)
    return kept


def compute_hr_drift(records: list[dict]) -> dict:
    """以逐秒心率計算前後半段的心率漂移。

    定義：依 elapsed_sec 中點切成前後兩半，各取平均心率，
    drift_pct = (後半 - 前半) / 前半 * 100。
    只回傳數值不做解讀——解讀留給之後的 AI coach 層。
    """
    hr_points = [r for r in records if r.get("hr_bpm")]
    if len(hr_points) < 2:
        return {"available": False, "reason": "此活動無足夠心率資料可計算漂移"}

    mid = (hr_points[0]["elapsed_sec"] + hr_points[-1]["elapsed_sec"]) / 2
    first = [r["hr_bpm"] for r in hr_points if r["elapsed_sec"] <= mid]
    second = [r["hr_bpm"] for r in hr_points if r["elapsed_sec"] > mid]
    if not first or not second:
        return {"available": False, "reason": "此活動無足夠心率資料可計算漂移"}

    first_avg = sum(first) / len(first)
    second_avg = sum(second) / len(second)
    return {
        "available": True,
        "first_half_avg_hr": round(first_avg, 1),
        "second_half_avg_hr": round(second_avg, 1),
        "drift_pct": round((second_avg - first_avg) / first_avg * 100, 1),
    }
