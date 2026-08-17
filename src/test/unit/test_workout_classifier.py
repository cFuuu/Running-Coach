"""workout_classifier 的單元測試——全部使用合成分圈資料，不含任何真實個人資料。"""

import unittest

from src.main.python.services.workout_classifier import classify_workout


def _lap(index: int, pace: int, km: float = 1.0) -> dict:
    return {
        "lap_index": index,
        "distance_km": km,
        "duration_sec": pace * km,
        "pace_sec_per_km": pace,
        "avg_hr_bpm": None,
        "max_hr_bpm": None,
    }


class TestClassifyWorkout(unittest.TestCase):
    def test_alternating_fast_slow_is_interval(self):
        # 快慢交替：300/450 秒交替，標準差與落差都大
        laps = [_lap(i, 300 if i % 2 else 450) for i in range(1, 9)]
        self.assertEqual(classify_workout(laps), "interval")

    def test_long_distance_relative_to_recent_average_is_lsd(self):
        laps = [_lap(i, 400) for i in range(1, 16)]
        self.assertEqual(
            classify_workout(
                laps, total_distance_km=15.0, recent_avg_distance_km=8.0
            ),
            "lsd",
        )

    def test_steady_fast_pace_is_tempo(self):
        laps = [_lap(i, 330) for i in range(1, 7)]
        self.assertEqual(
            classify_workout(
                laps,
                total_distance_km=6.0,
                recent_avg_distance_km=7.0,
                recent_avg_pace_sec_per_km=380,
            ),
            "tempo",
        )

    def test_steady_normal_pace_is_easy(self):
        laps = [_lap(i, 380) for i in range(1, 7)]
        self.assertEqual(
            classify_workout(
                laps,
                total_distance_km=6.0,
                recent_avg_distance_km=7.0,
                recent_avg_pace_sec_per_km=380,
            ),
            "easy",
        )

    def test_too_few_laps_is_unknown(self):
        self.assertEqual(classify_workout([_lap(1, 360), _lap(2, 365)]), "unknown")

    def test_empty_laps_is_unknown(self):
        self.assertEqual(classify_workout([]), "unknown")

    def test_short_warmup_laps_are_excluded_from_structure(self):
        """0.1km 的碎圈不應被當成配速結構的一部分而誤判為間歇。"""
        laps = [_lap(1, 900, km=0.05), _lap(2, 380), _lap(3, 378), _lap(4, 382)]
        self.assertEqual(
            classify_workout(
                laps,
                total_distance_km=3.05,
                recent_avg_distance_km=7.0,
                recent_avg_pace_sec_per_km=380,
            ),
            "easy",
        )

    def test_lsd_takes_priority_even_with_few_laps(self):
        """長跑即使只有一兩圈（沒按錶），仍應靠距離判定為 LSD 而非 unknown。"""
        self.assertEqual(
            classify_workout(
                [_lap(1, 400, km=20.0)],
                total_distance_km=20.0,
                recent_avg_distance_km=8.0,
            ),
            "lsd",
        )


if __name__ == "__main__":
    unittest.main()
