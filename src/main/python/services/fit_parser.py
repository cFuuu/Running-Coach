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

# FIT 的經緯度單位是 semicircles（2^31 對應 180 度）。
_DEG_PER_SEMICIRCLE = 180 / 2**31
# 經緯度存到小數 5 位（約 1 m）：GPS 本身有 3–5 m 誤差，更多位數只是雜訊（N-32）。
COORD_DECIMALS = 5

# 低於此檔案大小的 FIT 幾乎都是每日監測片段而非運動活動，掃描時可先略過。
# 依據：實測 20,778 個 FIT 檔的大小中位數僅 793 bytes（多為每日監測），
# 而資料庫中最短的跑步活動為 107 秒，逐秒紀錄約 69 bytes/筆 → 推估約 7KB。
# 取 3000 bytes 作為安全下限，寧可多解析也不要漏掉短跑步。
DEFAULT_MIN_FILE_BYTES = 3000


def mps_to_pace_sec_per_km(speed_mps: float | None) -> int | None:
    """公尺/秒 → 每公里秒數。速度為 0 或 None 時回 None（靜止不該算配速）。"""
    if not speed_mps or speed_mps <= 0:
        return None
    return round(_M_PER_KM / speed_mps)


def _duration_to_pace_sec_per_km(distance_m: float | None, duration_sec: float | None) -> int | None:
    if not distance_m or distance_m <= 0 or not duration_sec or duration_sec <= 0:
        return None
    return round(duration_sec / (distance_m / _M_PER_KM))


def _semicircles_to_deg(value: int | None) -> float | None:
    if value is None:
        return None
    return round(value * _DEG_PER_SEMICIRCLE, COORD_DECIMALS)


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
                "pace_sec_per_km": mps_to_pace_sec_per_km(speed),
                "cadence_spm": r.get("cadence"),
                "altitude_m": r.get("enhanced_altitude", r.get("altitude")),
                "lat": _semicircles_to_deg(r.get("position_lat")),
                "lon": _semicircles_to_deg(r.get("position_long")),
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


# --- 心率品質（光學心率的壞資料）---
# 門檻參考 TrailRunCoach docs/research/optical-hr-quality.md 的規則，原規則
# 用 1 秒資料；本專案逐秒紀錄匯入時已降頻為每 10 秒一筆，故「3 秒內升 15」
# 改成「相鄰兩筆差 15」。全部為 🔴 推估，未經本學員資料校正。
HR_VALID_MIN_BPM = 30
HR_VALID_MAX_BPM = 225
HR_JUMP_BPM = 15
# 跳升後在此秒數內回到原水準（±HR_RETURN_TOLERANCE_BPM）算尖峰；沒回來、
# 且配速沒變（相鄰兩筆差 < HR_JUMP_STEADY_PACE_PCT）算「移動中跳一階」。
HR_SPIKE_RETURN_SEC = 30
HR_RETURN_TOLERANCE_BPM = 5
HR_JUMP_STEADY_PACE_PCT = 0.10
# 「心率 ≈ 2 × 單腳步頻」的鎖步頻偵測刻意不做：2026-10-20 在本專案 243 場
# 實測，這個簡單重疊規則會標到 62 場（多為節奏跑，真實心率本來就接近步頻），
# 誤判太多；TrailRunCoach 也因同樣理由暫緩。


def assess_hr_quality(records: list[dict]) -> dict:
    """標出可疑的心率樣本：超出合理範圍、尖峰、移動中跳一階。

    只標記、不改原始資料、不內插。回傳：
        {"suspect_elapsed_sec": set[int],  # 應排除於平均之外的樣本
         "out_of_range": int, "spikes": int, "step_jumps": int}
    「跳一階」只計數不排除——無從判斷跳之前還是之後才是真值；
    呼叫端據此降低判讀等級即可。停下再起跑的急升不算（真實心率也會急升），
    以「前後兩筆都在移動、配速變化小」排除。
    """
    suspect: set[int] = set()
    out_of_range = spikes = step_jumps = 0
    points = [r for r in records if r.get("hr_bpm")]
    for r in points:
        if not HR_VALID_MIN_BPM <= r["hr_bpm"] <= HR_VALID_MAX_BPM:
            suspect.add(r["elapsed_sec"])
            out_of_range += 1

    for i in range(1, len(points)):
        prev, cur = points[i - 1], points[i]
        if abs(cur["hr_bpm"] - prev["hr_bpm"]) < HR_JUMP_BPM:
            continue
        returned_at = next(
            (
                j
                for j in range(i + 1, len(points))
                if points[j]["elapsed_sec"] - cur["elapsed_sec"] <= HR_SPIKE_RETURN_SEC
                and abs(points[j]["hr_bpm"] - prev["hr_bpm"]) <= HR_RETURN_TOLERANCE_BPM
            ),
            None,
        )
        if returned_at is not None:
            spikes += 1
            suspect.update(p["elapsed_sec"] for p in points[i:returned_at])
        elif _steady_pace(prev, cur):
            step_jumps += 1

    return {
        "suspect_elapsed_sec": suspect,
        "out_of_range": out_of_range,
        "spikes": spikes,
        "step_jumps": step_jumps,
    }


def _steady_pace(prev: dict, cur: dict) -> bool:
    p0, p1 = prev.get("pace_sec_per_km"), cur.get("pace_sec_per_km")
    if not p0 or not p1:
        return False
    return abs(p1 - p0) / p0 < HR_JUMP_STEADY_PACE_PCT


# --- 心率漂移 / Pa:HR decoupling ---
# 暖身：固定切掉前 10 分鐘，暖身期的心率爬升不該算進前半段。
# 🔴 推估（TrailRunCoach workout_review.WARMUP_S 同值）。
DRIFT_WARMUP_SEC = 10 * 60
# 暖身後的移動時間：≥ 40 分鐘為「標準」（Uphill Athlete 的 AeT 漂移測試最短
# 40 分鐘）、30–40 分鐘只當「參考」（🔴 推估）、< 30 分鐘不算。
DRIFT_STANDARD_SEC = 40 * 60
DRIFT_MIN_SEC = 30 * 60
# 慢於此配速視為停下／走路，不算移動時間。🔴 推估。
DRIFT_STOPPED_PACE_SEC_PER_KM = 1200
# 以下拒算條件皆為 🔴 推估（取自 TrailRunCoach 的 drift_of），2026-10-20
# 在本專案 142 場夠長的跑步實測：停頓 >5% 擋 21 場、結尾加速 >5% 擋 23 場、
# 每公里爬升 ≥20m 擋 7 場，合計通過 98 場。
DRIFT_MAX_STOP_FRACTION = 0.05
DRIFT_MAX_GAIN_M_PER_KM = 20
DRIFT_FAST_FINISH_TAIL = 0.10
DRIFT_MAX_FAST_FINISH = 0.05
# 視窗內可疑心率樣本超過此比例、或有任何一次移動中跳一階 → 降為參考。🔴 推估。
DRIFT_MAX_SUSPECT_HR_FRACTION = 0.03
# 前後半外部負荷不同，算出來沒有意義的課種。
_DRIFT_EXCLUDED_WORKOUT_TYPES = frozenset({"interval"})


def _unavailable(reason: str) -> dict:
    return {"available": False, "reason": reason}


def _speed_m_per_min(pace_sec_per_km: float) -> float:
    return _M_PER_KM * 60 / pace_sec_per_km


def compute_hr_drift(
    records: list[dict],
    *,
    workout_type: str | None = None,
    elevation_gain_m: float | None = None,
    distance_km: float | None = None,
) -> dict:
    """暖身後的穩定段，比較前後半的心率與有氧效率（Pa:HR decoupling）。

    定義（暖身後、只取移動中的樣本，依移動時間切成前後兩半）：
        drift_pct      = (後半平均心率 − 前半) / 前半 × 100      純心率差
        EF             = 平均速度（公尺/分）÷ 平均心率
        decoupling_pct = (前半 EF − 後半 EF) / 前半 EF × 100     Pa:HR
    decoupling 為正代表後半段同樣的心跳換到的速度變少。後半自己跑快造成的
    心率上升，drift_pct 會記成漂移，decoupling 不會——這是兩者的根本差別。

    拒算（前後半外部負荷不同、數字沒有意義）：間歇課、停頓 > 5%、
    每公里爬升 ≥ 20m（逐筆資料沒有坡度校正配速）、最後 10% 比前段快 > 5%。
    分級：暖身後移動 ≥ 40 分鐘為 standard，30–40 分鐘或心率品質可疑為 reference。

    只回傳數值不做解讀。Friel 的「Pa:HR < 5%」是教練經驗法則、非同儕審查，
    本函式不帶任何門檻，見 docs/research/physiological-thresholds.md 主題一。
    """
    if workout_type in _DRIFT_EXCLUDED_WORKOUT_TYPES:
        return _unavailable("間歇課前後半的外部負荷不同，漂移沒有意義")
    if elevation_gain_m and distance_km and distance_km > 0:
        gain_per_km = elevation_gain_m / distance_km
        if gain_per_km >= DRIFT_MAX_GAIN_M_PER_KM:
            return _unavailable(
                f"每公里爬升 {gain_per_km:.0f}m，超過 {DRIFT_MAX_GAIN_M_PER_KM}m——"
                "逐筆資料沒有坡度校正配速，坡道會讓前後半不可比"
            )

    if not any(r.get("hr_bpm") for r in records):
        return _unavailable("此活動無心率資料可計算漂移")
    window = [r for r in records if r["elapsed_sec"] >= DRIFT_WARMUP_SEC]
    sample_sec = _median_interval(records)
    quality = assess_hr_quality(window)
    moving = [
        r
        for r in window
        if r.get("pace_sec_per_km")
        and r["pace_sec_per_km"] < DRIFT_STOPPED_PACE_SEC_PER_KM
        and r.get("hr_bpm")
        and r["elapsed_sec"] not in quality["suspect_elapsed_sec"]
    ]
    moving_sec = len(moving) * sample_sec
    if moving_sec < DRIFT_MIN_SEC:
        return _unavailable(
            f"暖身（前 {DRIFT_WARMUP_SEC // 60} 分鐘）後的有效移動時間 "
            f"{moving_sec / 60:.0f} 分鐘，短於 {DRIFT_MIN_SEC // 60} 分鐘下限——"
            "漂移假設身體已進入穩態，時間太短不滿足此前提"
        )

    span_sec = window[-1]["elapsed_sec"] - window[0]["elapsed_sec"] + sample_sec
    stop_fraction = max(0.0, 1 - moving_sec / span_sec)
    if stop_fraction > DRIFT_MAX_STOP_FRACTION:
        return _unavailable(
            f"暖身後停頓（含心率可疑、走路）占 {stop_fraction:.0%}，超過 "
            f"{DRIFT_MAX_STOP_FRACTION:.0%}——停下時心率回落，前後半不可比"
        )

    tail = max(1, round(len(moving) * DRIFT_FAST_FINISH_TAIL))
    head_speed = _mean_speed(moving[:-tail])
    fast_finish = _mean_speed(moving[-tail:]) / head_speed - 1
    if fast_finish > DRIFT_MAX_FAST_FINISH:
        return _unavailable(
            f"最後 {DRIFT_FAST_FINISH_TAIL:.0%} 比前段快 {fast_finish:.0%}——"
            "結尾加速會把後半心率拉高，漂移會被高估"
        )

    half = len(moving) // 2
    first, second = moving[:half], moving[half:]
    first_hr = sum(r["hr_bpm"] for r in first) / len(first)
    second_hr = sum(r["hr_bpm"] for r in second) / len(second)
    first_ef = _mean_speed(first) / first_hr
    second_ef = _mean_speed(second) / second_hr

    tier_reasons: list[str] = []
    if moving_sec < DRIFT_STANDARD_SEC:
        tier_reasons.append(
            f"暖身後移動 {moving_sec / 60:.0f} 分鐘，未達 {DRIFT_STANDARD_SEC // 60} 分鐘"
        )
    suspect_fraction = len(quality["suspect_elapsed_sec"]) / len(window)
    if suspect_fraction > DRIFT_MAX_SUSPECT_HR_FRACTION:
        tier_reasons.append(f"可疑心率樣本占 {suspect_fraction:.0%}（尖峰或超出範圍）")
    if quality["step_jumps"]:
        tier_reasons.append(f"心率在配速穩定時突然跳一階 {quality['step_jumps']} 次")

    return {
        "available": True,
        "tier": "reference" if tier_reasons else "standard",
        "tier_reasons": tier_reasons,
        "steady_moving_min": round(moving_sec / 60, 1),
        "first_half_avg_hr": round(first_hr, 1),
        "second_half_avg_hr": round(second_hr, 1),
        "drift_pct": round((second_hr - first_hr) / first_hr * 100, 1),
        "first_half_ef": round(first_ef, 3),
        "second_half_ef": round(second_ef, 3),
        "decoupling_pct": round((first_ef - second_ef) / first_ef * 100, 1),
        "hr_quality": {
            "spikes": quality["spikes"],
            "step_jumps": quality["step_jumps"],
            "out_of_range": quality["out_of_range"],
        },
    }


def _mean_speed(records: list[dict]) -> float:
    """平均速度（公尺/分）。以速度平均而非配速平均——配速是速度的倒數，
    直接平均配速會偏向慢的樣本。"""
    return sum(_speed_m_per_min(r["pace_sec_per_km"]) for r in records) / len(records)


def _median_interval(records: list[dict]) -> int:
    """紀錄的取樣間隔（秒）。匯入時已降頻，從資料本身推導，不寫死。"""
    gaps = sorted(
        b["elapsed_sec"] - a["elapsed_sec"] for a, b in zip(records, records[1:])
    )
    return max(1, gaps[len(gaps) // 2]) if gaps else 1
