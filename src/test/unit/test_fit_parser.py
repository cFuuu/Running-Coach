"""fit_parser 的單元測試——降頻與心率漂移用合成資料，不含任何真實個人資料。"""

import unittest

from src.main.python.services.fit_parser import (
    MIN_DRIFT_DURATION_SEC,
    compute_hr_drift,
    downsample_records,
)


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


# 心率漂移的測試一律用長於 MIN_DRIFT_DURATION_SEC 的樣本——短於下限的活動
# 現在會被前置條件擋下（見 compute_hr_drift 的說明），時長本身另有測試涵蓋。
_LONG = MIN_DRIFT_DURATION_SEC + 600  # 55 分鐘
_HALF = _LONG // 2


def _long_run(first_hr: int, second_hr: int) -> list[dict]:
    """建立一場長度足夠、前後半心率分別為指定值的活動。"""
    return [_rec(i, first_hr) for i in range(0, _HALF + 1, 60)] + [
        _rec(i, second_hr) for i in range(_HALF + 60, _LONG + 1, 60)
    ]


class TestComputeHrDrift(unittest.TestCase):
    def test_positive_drift(self):
        # 前半平均 150、後半平均 165 -> 漂移 10%
        result = compute_hr_drift(_long_run(150, 165))
        self.assertTrue(result["available"])
        self.assertEqual(result["first_half_avg_hr"], 150.0)
        self.assertEqual(result["second_half_avg_hr"], 165.0)
        self.assertEqual(result["drift_pct"], 10.0)

    def test_negative_drift(self):
        result = compute_hr_drift(_long_run(160, 152))
        self.assertLess(result["drift_pct"], 0)

    def test_unavailable_without_hr(self):
        records = [_rec(i) for i in range(0, _LONG, 60)]
        result = compute_hr_drift(records)
        self.assertFalse(result["available"])
        self.assertIn("reason", result)

    def test_unavailable_with_single_point(self):
        self.assertFalse(compute_hr_drift([_rec(0, 150)])["available"])

    def test_short_activity_is_not_assessed(self):
        """短於下限時不輸出漂移值——指標假設前後半外部負荷相同且已進入穩態，
        短時間活動不滿足此前提（PLAN.md N-3 第一批定案）。
        """
        short = [_rec(i, 150) for i in range(0, 600, 60)]  # 10 分鐘
        result = compute_hr_drift(short)
        self.assertFalse(result["available"])
        self.assertIn("下限", result["reason"])

    def test_duration_boundary_is_inclusive(self):
        """恰好達到下限的活動應可計算，不被誤擋。"""
        records = [_rec(i, 150) for i in range(0, MIN_DRIFT_DURATION_SEC + 1, 60)]
        self.assertTrue(compute_hr_drift(records)["available"])

    def test_ignores_records_without_hr(self):
        """部分紀錄缺心率（感測器掉訊）時，應只用有值的點計算而非當成 0。"""
        records = _long_run(150, 160)
        records.insert(1, _rec(30, None))
        result = compute_hr_drift(records)
        self.assertTrue(result["available"])
        self.assertEqual(result["first_half_avg_hr"], 150.0)


if __name__ == "__main__":
    unittest.main()
