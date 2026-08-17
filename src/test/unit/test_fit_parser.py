"""fit_parser 的單元測試——降頻與心率漂移用合成資料，不含任何真實個人資料。"""

import unittest

from src.main.python.services.fit_parser import compute_hr_drift, downsample_records


def _rec(elapsed: int, hr: int | None = None) -> dict:
    return {
        "elapsed_sec": elapsed,
        "distance_km": elapsed / 1000,
        "hr_bpm": hr,
        "pace_sec_per_km": 360,
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


class TestComputeHrDrift(unittest.TestCase):
    def test_positive_drift(self):
        # 前半平均 150、後半平均 165 -> 漂移 10%
        records = [_rec(i, 150) for i in range(0, 51, 10)] + [
            _rec(i, 165) for i in range(60, 111, 10)
        ]
        result = compute_hr_drift(records)
        self.assertTrue(result["available"])
        self.assertEqual(result["first_half_avg_hr"], 150.0)
        self.assertEqual(result["second_half_avg_hr"], 165.0)
        self.assertEqual(result["drift_pct"], 10.0)

    def test_negative_drift(self):
        records = [_rec(i, 160) for i in range(0, 51, 10)] + [
            _rec(i, 152) for i in range(60, 111, 10)
        ]
        result = compute_hr_drift(records)
        self.assertLess(result["drift_pct"], 0)

    def test_unavailable_without_hr(self):
        records = [_rec(i) for i in range(0, 100, 10)]
        result = compute_hr_drift(records)
        self.assertFalse(result["available"])
        self.assertIn("reason", result)

    def test_unavailable_with_single_point(self):
        self.assertFalse(compute_hr_drift([_rec(0, 150)])["available"])

    def test_ignores_records_without_hr(self):
        """部分紀錄缺心率（感測器掉訊）時，應只用有值的點計算而非當成 0。"""
        records = [_rec(0, 150), _rec(10, None), _rec(20, 150), _rec(30, 160), _rec(40, 160)]
        result = compute_hr_drift(records)
        self.assertTrue(result["available"])
        self.assertEqual(result["first_half_avg_hr"], 150.0)


if __name__ == "__main__":
    unittest.main()
