"""fit_parser 的單元測試——降頻與心率漂移用合成資料，不含任何真實個人資料。"""

import unittest

from src.main.python.services.fit_parser import (
    DRIFT_MIN_SEC,
    DRIFT_STANDARD_SEC,
    DRIFT_WARMUP_SEC,
    assess_hr_quality,
    compute_hr_drift,
    downsample_records,
)


def _rec(elapsed: int, hr: int | None = None, pace: int | None = 360) -> dict:
    return {
        "elapsed_sec": elapsed,
        "distance_km": elapsed / 1000,
        "hr_bpm": hr,
        "pace_sec_per_km": pace,
        "cadence_spm": 85,
        "altitude_m": 10.0,
    }


class TestDownsampleRecords(unittest.TestCase):
    def test_keeps_every_nth_second(self):
        records = [_rec(i) for i in range(0, 61)]
        kept = downsample_records(records, 10)
        self.assertEqual([r["elapsed_sec"] for r in kept], [0, 10, 20, 30, 40, 50, 60])

    def test_always_keeps_last_point(self):
        """結尾不是間隔整數倍時，仍要保留最後一筆，否則曲線尾端被截斷。"""
        records = [_rec(i) for i in range(0, 45)]
        kept = downsample_records(records, 10)
        self.assertEqual(kept[-1]["elapsed_sec"], 44)

    def test_no_downsampling_when_interval_is_one(self):
        records = [_rec(i) for i in range(0, 10)]
        self.assertEqual(len(downsample_records(records, 1)), 10)

    def test_empty_input(self):
        self.assertEqual(downsample_records([], 10), [])

    def test_handles_irregular_gaps(self):
        """GPS 訊號中斷造成時間跳躍時，不應漏掉跳躍後的第一筆。"""
        records = [_rec(0), _rec(5), _rec(100), _rec(105)]
        kept = downsample_records(records, 10)
        self.assertIn(100, [r["elapsed_sec"] for r in kept])


# 心率漂移：暖身後移動 50 分鐘、每 10 秒一筆（與匯入降頻一致）。
_STEP = 10
_STEADY = 50 * 60


def _run(first_hr, second_hr, first_pace=360, second_pace=360, steady=_STEADY):
    """暖身 10 分鐘（心率 130）＋穩定段，穩定段前後半的心率與配速各為指定值。"""
    end = DRIFT_WARMUP_SEC + steady
    mid = DRIFT_WARMUP_SEC + steady // 2
    records = [_rec(t, 130) for t in range(0, DRIFT_WARMUP_SEC, _STEP)]
    for t in range(DRIFT_WARMUP_SEC, end, _STEP):
        first = t < mid
        records.append(
            _rec(t, first_hr if first else second_hr, first_pace if first else second_pace)
        )
    return records


class TestComputeHrDrift(unittest.TestCase):
    def test_drift_and_decoupling_at_steady_pace(self):
        # 配速不變、心率 150→160：心率漂移 6.7%，效率掉 1 − 150/160 = 6.25%
        result = compute_hr_drift(_run(150, 160))
        self.assertTrue(result["available"])
        self.assertEqual(result["first_half_avg_hr"], 150.0)
        self.assertEqual(result["second_half_avg_hr"], 160.0)
        self.assertEqual(result["drift_pct"], 6.7)
        self.assertAlmostEqual(result["decoupling_pct"], 6.25, delta=0.06)
        self.assertEqual(result["tier"], "standard")

    def test_warmup_is_excluded(self):
        """暖身期的低心率不得拉低前半平均。"""
        result = compute_hr_drift(_run(150, 150))
        self.assertEqual(result["first_half_avg_hr"], 150.0)
        self.assertEqual(result["drift_pct"], 0.0)

    def test_faster_second_half_is_not_decoupling(self):
        """後半自己跑快約 5%、心率等比上升：純心率差記成漂移，Pa:HR 不會。
        （後半整段都快，最後 10% 相對前段只快約 3%，不觸發結尾加速拒算）"""
        result = compute_hr_drift(_run(150, 157, first_pace=330, second_pace=315))
        self.assertGreater(result["drift_pct"], 0)
        self.assertAlmostEqual(result["decoupling_pct"], 0.0, delta=0.5)

    def test_short_steady_part_is_reference_tier(self):
        result = compute_hr_drift(_run(150, 155, steady=DRIFT_MIN_SEC + 300))
        self.assertTrue(result["available"])
        self.assertEqual(result["tier"], "reference")
        self.assertTrue(result["tier_reasons"])

    def test_too_short_is_not_assessed(self):
        result = compute_hr_drift(_run(150, 155, steady=DRIFT_MIN_SEC - 300))
        self.assertFalse(result["available"])
        self.assertIn("下限", result["reason"])

    def test_standard_boundary_is_inclusive(self):
        result = compute_hr_drift(_run(150, 155, steady=DRIFT_STANDARD_SEC))
        self.assertEqual(result["tier"], "standard")

    def test_interval_workout_is_not_assessed(self):
        result = compute_hr_drift(_run(150, 165), workout_type="interval")
        self.assertFalse(result["available"])

    def test_hilly_run_is_not_assessed(self):
        result = compute_hr_drift(_run(150, 165), elevation_gain_m=250, distance_km=10)
        self.assertFalse(result["available"])
        self.assertIn("爬升", result["reason"])

    def test_many_stops_is_not_assessed(self):
        records = _run(150, 165)
        for r in records[100:130]:  # 暖身後 5 分鐘停等（約 10%）
            r["pace_sec_per_km"] = None
        result = compute_hr_drift(records)
        self.assertFalse(result["available"])
        self.assertIn("停頓", result["reason"])

    def test_fast_finish_is_not_assessed(self):
        records = _run(150, 150)
        for r in records[-30:]:  # 最後 5 分鐘從 6:00 加速到 5:00
            r["pace_sec_per_km"] = 300
        result = compute_hr_drift(records)
        self.assertFalse(result["available"])
        self.assertIn("加速", result["reason"])

    def test_unavailable_without_hr(self):
        records = [_rec(t) for t in range(0, DRIFT_WARMUP_SEC + _STEADY, _STEP)]
        result = compute_hr_drift(records)
        self.assertFalse(result["available"])
        self.assertIn("reason", result)

    def test_unavailable_with_single_point(self):
        self.assertFalse(compute_hr_drift([_rec(0, 150)])["available"])

    def test_spike_is_excluded_from_average(self):
        records = _run(150, 150)
        records[100]["hr_bpm"] = 200  # 單筆尖峰，下一筆就回來
        result = compute_hr_drift(records)
        self.assertEqual(result["first_half_avg_hr"], 150.0)
        self.assertEqual(result["hr_quality"]["spikes"], 1)

    def test_step_jump_downgrades_to_reference(self):
        records = _run(150, 150)
        for r in records[200:]:  # 配速不變，心率突然跳一階且沒回來
            r["hr_bpm"] = 170
        result = compute_hr_drift(records)
        self.assertEqual(result["hr_quality"]["step_jumps"], 1)
        self.assertEqual(result["tier"], "reference")


class TestAssessHrQuality(unittest.TestCase):
    def test_out_of_range_is_suspect(self):
        records = [_rec(0, 150), _rec(10, 240), _rec(20, 150)]
        quality = assess_hr_quality(records)
        self.assertIn(10, quality["suspect_elapsed_sec"])
        self.assertEqual(quality["out_of_range"], 1)

    def test_gradual_climb_is_not_flagged(self):
        """真實爬坡：心率 3 分鐘內穩定升 30 bpm，不是尖峰也不是跳一階。"""
        records = [_rec(t, 140 + t // 10 * 2) for t in range(0, 180, 10)]
        quality = assess_hr_quality(records)
        self.assertEqual(quality["spikes"] + quality["step_jumps"], 0)
        self.assertFalse(quality["suspect_elapsed_sec"])

    def test_jump_after_stop_is_not_step(self):
        """停下再起跑的急升可能是真的，不算跳一階。"""
        records = [_rec(0, 120, pace=None), _rec(10, 140, pace=360), _rec(20, 142)]
        self.assertEqual(assess_hr_quality(records)["step_jumps"], 0)


if __name__ == "__main__":
    unittest.main()
