"""workout_classifier 的單元測試——全部使用合成分圈資料，不含任何真實個人資料。"""

import unittest

from src.main.python.services.workout_classifier import (
    classify_workout,
    segment_laps,
    work_segments,
)


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


def _auto_lapped_1000m_reps(reps: int = 4, work_pace: int = 285, rest_pace: int = 540) -> list[dict]:
    """錶設 400m 自動分圈、跑完 1000m 才手動按 lap：每趟切成 400＋400＋200，
    接著約 220m 的慢跑休息（N-24 的真實情境，數值為合成）。"""
    laps = []
    for _ in range(reps):
        for km, pace in ((0.4, work_pace), (0.4, work_pace), (0.2, work_pace), (0.22, rest_pace)):
            laps.append(_lap(len(laps) + 1, pace, km=km))
    return laps


class TestIntervalSegments(unittest.TestCase):
    """N-24：分圈合併成「趟」，看快慢交替判定間歇。"""

    def test_auto_lapped_reps_merge_into_1000m_segments(self):
        works = work_segments(segment_laps(_auto_lapped_1000m_reps()))
        self.assertEqual(len(works), 4)
        for seg in works:
            self.assertAlmostEqual(seg["distance_km"], 1.0)
            self.assertAlmostEqual(seg["pace_sec_per_km"], 285)

    def test_auto_lapped_reps_are_interval_not_tempo(self):
        """★ 舊版丟掉 <0.3km 的圈後只剩平穩的 400m 圈，會誤判為 tempo。"""
        self.assertEqual(
            classify_workout(
                _auto_lapped_1000m_reps(),
                total_distance_km=4.9,
                recent_avg_distance_km=7.0,
                recent_avg_pace_sec_per_km=350,
            ),
            "interval",
        )

    def test_two_long_reps_are_interval(self):
        """4000m＋休息＋2000m 也是間歇，門檻不可設成 3 趟。"""
        laps = [_lap(i, 300, km=0.4) for i in range(1, 11)]
        laps.append(_lap(11, 500, km=0.45))
        laps += [_lap(i, 295, km=0.4) for i in range(12, 17)]
        self.assertEqual(
            classify_workout(laps, total_distance_km=6.5, recent_avg_distance_km=7.0,
                             recent_avg_pace_sec_per_km=350),
            "interval",
        )

    def test_single_stop_in_tempo_is_not_interval(self):
        """★ 慢的第一公里＋停頓一公里＋穩定 8 公里：第一段不夠快，不算一趟。
        停頓圈取 410（仍算休息段，但不觸發舊的「標準差＋全距」規則），只測新規則。"""
        laps = [_lap(1, 351), _lap(2, 410)] + [_lap(i, 302) for i in range(3, 11)]
        self.assertNotEqual(
            classify_workout(laps, total_distance_km=10.0, recent_avg_distance_km=8.0,
                             recent_avg_pace_sec_per_km=360),
            "interval",
        )

    def test_without_recent_average_segment_rule_is_skipped(self):
        """無近期平均就無從判斷「快」，不套分段規則。"""
        self.assertNotEqual(classify_workout(_auto_lapped_1000m_reps()), "interval")

    def test_long_recovery_is_not_an_interval_rest(self):
        """★ 長跑中途走路 9 分鐘：前後兩段都夠快，但那不是組間休息。"""
        laps = [_lap(i, 357) for i in range(1, 5)] + [_lap(5, 547)] + [_lap(i, 349) for i in range(6, 11)]
        self.assertNotEqual(
            classify_workout(laps, total_distance_km=10.0, recent_avg_distance_km=8.0,
                             recent_avg_pace_sec_per_km=379),
            "interval",
        )

    def test_easy_run_with_walking_km_is_not_interval(self):
        """★ 舊「標準差＋全距」規則會把夾一圈走路的輕鬆跑判成間歇；有近期平均時不再套用。"""
        laps = [_lap(i, 360) for i in range(1, 6)] + [_lap(6, 576)] + [_lap(i, 364) for i in range(7, 11)]
        self.assertEqual(
            classify_workout(laps, total_distance_km=10.0, recent_avg_distance_km=8.0,
                             recent_avg_pace_sec_per_km=377),
            "easy",
        )

    def test_button_press_lap_does_not_split_segment(self):
        """<20 秒的碎圈忽略，不會把一趟切成兩趟。"""
        laps = [_lap(1, 285, km=0.4), _lap(2, 600, km=0.02), _lap(3, 285, km=0.6),
                _lap(4, 540, km=0.22), _lap(5, 285, km=1.0)]
        works = work_segments(segment_laps(laps))
        self.assertEqual([round(s["distance_km"], 2) for s in works], [1.0, 1.0])


class TestTempoBlock(unittest.TestCase):
    """N-27：慢速暖身 → 穩定快段 → 慢速收操，整場平均看不出來的節奏跑。"""

    def _classify(self, paces):
        laps = [_lap(i, p) for i, p in enumerate(paces, start=1)]
        return classify_workout(laps, total_distance_km=float(len(paces)),
                                recent_avg_distance_km=10.0, recent_avg_pace_sec_per_km=354)

    def test_warmup_tempo_block_cooldown_is_tempo(self):
        """★ 2026-10-20 的結構：2km 暖身、4km 約 5:24、2km 收操，整場平均只比平常快 6 秒。"""
        self.assertEqual(self._classify([369, 369, 323, 322, 331, 325, 380, 366]), "tempo")

    def test_without_cooldown_is_not_inferred(self):
        """只有前慢後快（或前快後慢）可能是起跑過快的輕鬆跑，寧可不判。"""
        self.assertEqual(self._classify([369, 369, 323, 322, 331, 325]), "easy")
        self.assertEqual(self._classify([323, 322, 331, 325, 380, 366]), "easy")

    def test_block_shorter_than_15_minutes_is_not_tempo(self):
        self.assertEqual(self._classify([369, 369, 323, 322, 380, 366, 370, 368]), "easy")

    def test_unsteady_block_is_not_tempo(self):
        """快段內配速落差超過 30 秒，不是穩定配速。"""
        self.assertEqual(self._classify([369, 369, 300, 330, 331, 333, 380, 366]), "easy")

    def test_all_easy_run_is_still_easy(self):
        self.assertEqual(self._classify([360] * 8), "easy")


if __name__ == "__main__":
    unittest.main()
