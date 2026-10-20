"""事實層輸出的信賴度標記（PLAN.md §7.3 定案的三欄位契約）。

本專案的規則引擎輸出給 AI 判斷層時，契約為：

    {數值, 依據來源, 信賴度/已知偏誤}

**信賴度為必填**——無法標記信賴度的事實，寧可不輸出（回傳 None），
也不給裸數字。理由見 PLAN.md §7.4：專案已實際發生過一次「被包裝成
帶依據來源的錯誤事實」（`acwr` 欄位存的是 Garmin 0–100 貢獻度分數，
卻被標成「急慢性負荷比（ACWR）」），AI 會據此產出有出處、格式完美、
內容錯誤的分析。

本模組只提供標記的產生與詞彙統一，不做任何判讀——「這個信賴度夠不夠
用來下結論」屬判斷層（AI）職責，規則層不代為決定。

信賴度等級沿用 `vdot_engine.py` 既有慣例（high/low），並補 unusable：
    - high     ：可直接採用
    - low      ：可用，但有已知偏誤，下結論前應納入考量
    - unusable ：偏誤大到不應據此判讀（呼叫端應改為不輸出該項判讀）
"""

from __future__ import annotations

from typing import Any, Literal

ConfidenceLevel = Literal["high", "low", "unusable"]

# --- 已知偏誤的集中定義（勿散落於各模組）---
# 每筆為：標記代碼 → (信賴度等級, 給 AI 看的偏誤說明)。
# 說明須寫「這個數字可能錯在哪、錯多少」，而非只寫「資料不足」——
# 前者 AI 才能判斷該不該據此下結論。

KNOWN_BIASES: dict[str, tuple[ConfidenceLevel, str]] = {
    "hrmax_watch_display": (
        "low",
        "HRmax 為手錶顯示值、非實測，可能有 ±5bpm 以上誤差；"
        "所有以 %HRR 推算的強度、負荷、等效配速都會等比偏移",
    ),
    "hrmax_age_formula": (
        "low",
        "HRmax 由年齡公式推估（220-年齡類），個體差異可達 ±10–12bpm，"
        "是本專案信賴度最低的 HRmax 來源",
    ),
    "hrv_short_baseline": (
        "low",
        "HRV 個人基準線僅約 292 天樣本（2025-10-25 起，佔 wellness 全期 17.7%），"
        "且該期間訓練量由 15 次/月降至 1–4 次/月再回升，基準線建立於訓練狀態"
        "劇烈變動中，不宜當作穩定的『你的正常值』",
    ),
    "workout_type_unknown": (
        "unusable",
        "該場活動的 workout_type 為 unknown/NULL（全體 266 場中佔 25.6%）。"
        "這不是『未知的課種』而是分類器失敗——它可能是任何課種，"
        "拿 easy 的標準評一場其實是間歇的課會得到完全錯誤的結論",
    ),
    "plan_match_inferred": (
        "low",
        "這場活動與計畫課表的對應關係是**推論的**，不是明確連結——"
        "依據只有「同一天」與「運動類型相容」。目前 training_plan 沒有任何一列"
        "填了 linked_activity_id，故所有對應皆為此種推論。若當天實際做的與課表"
        "無關（臨時改課、補跑別的內容），這個對應就是錯的，據此判斷「有沒有照表操課」"
        "會得到錯誤結論",
    ),
    "garmin_blackbox": (
        "unusable",
        "此為 Garmin（Firstbeat）黑盒演算法衍生值，公式未公開、無法標出處，"
        "僅供對照參考，不得作為判讀依據",
    ),
    "hrv_pct_form_unvalidated": (
        "low",
        "HRV 觸發用的是「當日 vs 自身 7 日均值下降 15%」這個固定百分比形式，"
        "而文獻標準做法是 LnrMSSD（取自然對數）+ 4 週基線的個人化 SWC "
        "（mean − 0.5×SD）、基線每週更新，且比較的是 7 日滾動平均對基線分布。"
        "本專案的形式與比較層級皆與文獻不符，15% 這個數字亦無文獻依據",
    ),
    "tsb_percentile_engineering": (
        "low",
        "TSB 門檻取自該學員自身歷史 TSB 分布的第 10 百分位，為工程實務決策、"
        "無文獻背書。它免疫於負荷單位的尺度問題（舊值 -30 取自 TrainingPeaks "
        "的 TSS 尺度，與本專案自建的「秒 × %HRR」相差約 30 倍），但『第 10 "
        "百分位就是異常』本身是我們選的，不是測出來的",
    ),
    "intensity_range_custom": (
        "low",
        "各課種的 %HRR 預期區間為本專案自訂，非直接引用單一文獻。"
        "Daniels 的區間定義在配速上，改用心率是為了讓受地形/天候/號誌"
        "影響的日常訓練也能判讀，但區間邊界本身未經本學員資料校正",
    ),
    "pace_fade_threshold_custom": (
        "low",
        "「前後半配速差 5% 以上算節奏不穩」為工程實務決策，無文獻依據——"
        "文獻中不存在「配速偏差多少算沒執行」的定義",
    ),
    "pace_execution_zone_estimated": (
        "low",
        "目標配速區間由 VDOT 推算而來，不是實際下達的課表配速；VDOT 本身可能"
        "取自非全力活動的心率換算值，區間即帶推估誤差。且文獻中不存在「配速"
        "偏離多少算沒執行」的定義，故本維度只回報實際配速落在區間的哪一側與"
        "偏離幅度，**不定義未達標的容差**，是否算沒執行由判斷層自行認定",
    ),
    "hr_drift_no_threshold": (
        "low",
        "心率漂移只呈現數值、不給好壞判定。drift_pct 是純前後半心率差（分母無"
        "配速，後半跑快也會算成漂移）；decoupling_pct 才是 Friel 的 Pa:HR。"
        "Friel 的「< 5%」是教練經驗法則、非同儕審查；且配速未做坡度校正、"
        "未校正溫濕度（脫水與高溫都會放大漂移）。2026-10-20 實測本專案通過"
        "拒算條件的 98 場，decoupling 中位數 3.3%、四分位 1.1–6.5%",
    ),
    "hr_drift_reference_tier": (
        "low",
        "這場的漂移只到「參考」等級：暖身後穩定段不足 40 分鐘，或心率有尖峰／"
        "突然跳一階（光學心率常見），數字可能被這些因素帶偏，不宜單獨據此判斷",
    ),
    "vdot_race_stale": (
        "low",
        "VDOT 的基準比賽距今已超過 90 天（半馬／全馬放寬至 180 天才排除），"
        "期間體能可能已有變化，數值反映的是當時而非現在的能力",
    ),
    "strength_hr_missing": (
        "low",
        "該重訓活動無心率資料，強度以固定保守值 50% HRR 估算，"
        "實際負荷可能被低估",
    ),
}

# HRmax 來源代碼（athlete_profile.max_hr_source）→ 偏誤標記。
# `measured` 為實測，無已知偏誤，故不在此表中。
_HRMAX_SOURCE_BIASES: dict[str, str] = {
    "watch_display": "hrmax_watch_display",
    "observed_estimate": "hrmax_watch_display",
    "age_formula": "hrmax_age_formula",
}


def describe_bias(code: str) -> dict[str, str]:
    """把偏誤代碼展開成 {code, level, detail}。未知代碼視為 unusable——
    寧可保守擋下，也不讓沒定義過的東西以 high 信賴度流到判斷層。
    """
    level, detail = KNOWN_BIASES.get(
        code, ("unusable", f"未定義的偏誤標記 `{code}`，無法評估其可信度")
    )
    return {"code": code, "level": level, "detail": detail}


def combine_level(codes: list[str]) -> ConfidenceLevel:
    """多個偏誤並存時取最保守者：任一 unusable 即 unusable，任一 low 即 low。"""
    levels = {describe_bias(c)["level"] for c in codes}
    if "unusable" in levels:
        return "unusable"
    if "low" in levels:
        return "low"
    return "high"


def fact(
    value: Any,
    *,
    source: str,
    bias_codes: list[str] | None = None,
) -> dict[str, Any] | None:
    """把一個數值包成事實層的三欄位輸出。

    參數：
        value       ：數值本身
        source      ：依據來源（計算方式／資料出處／文獻），**必填**
        bias_codes  ：已知偏誤代碼清單（KNOWN_BIASES 的 key）

    回傳 {value, source, confidence: {level, biases}}；
    **信賴度為 unusable 時回傳 None**——依 §7.3 硬規則，寧可不輸出，
    也不給一個判斷層無從評估可信度的裸數字。呼叫端須處理 None
    （表示「這項不評」），而非填 0 或預設值掩蓋。
    """
    codes = bias_codes or []
    level = combine_level(codes)
    if level == "unusable":
        return None
    return {
        "value": value,
        "source": source,
        "confidence": {
            "level": level,
            "biases": [describe_bias(c) for c in codes],
        },
    }


def hrmax_bias_codes(max_hr_source: str | None) -> list[str]:
    """由 athlete_profile.max_hr_source 推出對應的偏誤代碼清單。

    `measured`（實測）回傳空清單；None 或未知來源一律視為未實測，
    保守地標為手錶顯示值等級的偏誤。
    """
    if max_hr_source == "measured":
        return []
    if max_hr_source is None:
        return ["hrmax_watch_display"]
    return [_HRMAX_SOURCE_BIASES.get(max_hr_source, "hrmax_watch_display")]


def workout_type_bias_codes(workout_type: str | None) -> list[str]:
    """workout_type 為 unknown/NULL 時回傳 unusable 級偏誤——
    呼叫端據此略過所有需要「知道這是什麼課」才能評的維度。
    """
    if workout_type in (None, "", "unknown"):
        return ["workout_type_unknown"]
    return []
