"""session_quality 的單元測試——全部使用合成資料，不含真實個人資料。"""

import unittest

from src.main.python.services.session_quality import (
    MIN_DURATION_SEC_FOR_PACING,
    assess_intensity_fit,
    assess_pace_consistency,
    assess_session,
)

MAX_HR = 200.0
RHR = 50.0  # HRR 儲備 150 → 心率 x 的 %HRR = (x-50)/150


def _act(workout_type="easy", avg_hr=None):
    return {"workout_type": workout_type, "avg_hr_bpm": avg_hr}


def _records(first_pace, second_pace, duration=MIN_DURATION_SEC_FOR_PACING + 600):
    mid = duration // 2
    return [
        {"elapsed_sec": t, "pace_sec_per_km": first_pace if t <= mid else second_pace}
        for t in range(0, duration + 1, 10)
    ]


class IntensityFitTest(unittest.TestCase):
    def test_easy_run_within_range(self):
        got = assess_intensity_fit(_act("easy", 140.0), MAX_HR, RHR)  # 60% HRR
        self.assertEqual(got["verdict"], "within")

    def test_easy_run_too_hard_is_flagged_above(self):
        got = assess_intensity_fit(_act("easy", 180.0), MAX_HR, RHR)  # 86.7%
        self.assertEqual(got["verdict"], "above")

    def test_interval_too_slow_is_flagged_below(self):
        got = assess_intensity_fit(_act("interval", 140.0), MAX_HR, RHR)  # 60%
        self.assertEqual(got["verdict"], "below")

    def test_unknown_workout_type_is_not_assessed(self):
        """課種未知時不評——unknown 是分類器失敗，可能是任何課種。"""
        for wt in ("unknown", None, ""):
            with self.subTest(workout_type=wt):
                self.assertIsNone(assess_intensity_fit(_act(wt, 140.0), MAX_HR, RHR))

    def test_missing_hr_returns_none_rather_than_guessing(self):
        self.assertIsNone(assess_intensity_fit(_act("easy", None), MAX_HR, RHR))
        self.assertIsNone(assess_intensity_fit(_act("easy", 140.0), None, RHR))
        self.assertIsNone(assess_intensity_fit(_act("easy", 140.0), MAX_HR, None))

    def test_carries_both_hrmax_and_custom_range_biases(self):
        got = assess_intensity_fit(_act("easy", 140.0), MAX_HR, RHR, "watch_display")
        codes = {b["code"] for b in got["confidence"]["biases"]}
        self.assertIn("hrmax_watch_display", codes)
        self.assertIn("intensity_range_custom", codes)

    def test_measured_hrmax_still_flags_custom_range(self):
        """就算 HRmax 實測，區間本身仍是自訂的。"""
        got = assess_intensity_fit(_act("easy", 140.0), MAX_HR, RHR, "measured")
        self.assertEqual(got["confidence"]["level"], "low")


class PaceConsistencyTest(unittest.TestCase):
    def test_even_pacing(self):
        got = assess_pace_consistency(_records(360, 365))
        self.assertEqual(got["verdict"], "even")

    def test_fade_detected(self):
        got = assess_pace_consistency(_records(360, 400))  # 慢 11%
        self.assertEqual(got["verdict"], "faded")
        self.assertGreater(got["fade_pct"], 0)

    def test_negative_split_detected(self):
        got = assess_pace_consistency(_records(400, 360))
        self.assertEqual(got["verdict"], "negative_split")
        self.assertLess(got["fade_pct"], 0)

    def test_short_activity_not_assessed(self):
        """短於下限的活動尚未進入穩態，比較前後半沒有意義。"""
        self.assertIsNone(assess_pace_consistency(_records(360, 400, duration=600)))

    def test_insufficient_records_returns_none(self):
        self.assertIsNone(assess_pace_consistency([]))
        self.assertIsNone(assess_pace_consistency([{"elapsed_sec": 0, "pace_sec_per_km": 360}]))


class AssessSessionTest(unittest.TestCase):
    def _full(self):
        return assess_session(
            _act("easy", 140.0),
            _records(360, 365),
            {"available": True, "drift_pct": 4.0, "first_half_avg_hr": 140.0, "second_half_avg_hr": 145.6},
            MAX_HR,
            RHR,
            "watch_display",
        )

    def test_all_three_dimensions_present(self):
        got = self._full()
        names = {d["dimension"] for d in got["dimensions"]}
        self.assertEqual(names, {"intensity_fit", "pace_consistency", "hr_drift"})
        self.assertEqual(got["not_assessed"], [])

    def test_every_dimension_carries_confidence(self):
        for dim in self._full()["dimensions"]:
            with self.subTest(dimension=dim["dimension"]):
                self.assertIn("confidence", dim)
                self.assertIn(dim["confidence"]["level"], ("high", "low"))

    def test_no_overall_score_is_emitted(self):
        """刻意不輸出總分——合成單一分數等於把判斷偷渡進事實層。"""
        got = self._full()
        self.assertEqual(set(got), {"dimensions", "not_assessed"})
        for key in ("score", "overall", "grade", "rating"):
            self.assertNotIn(key, got)

    def test_hr_drift_has_no_verdict(self):
        """本專案算的是純心率差而非 Pa:HR，沒有可用的異常閾值。"""
        drift = next(d for d in self._full()["dimensions"] if d["dimension"] == "hr_drift")
        self.assertNotIn("verdict", drift)

    def test_unknown_workout_type_reports_reason_not_silence(self):
        got = assess_session(_act("unknown", 140.0), [], None, MAX_HR, RHR)
        blocked = {d["dimension"] for d in got["not_assessed"]}
        self.assertIn("intensity_fit", blocked)
        reason = next(d for d in got["not_assessed"] if d["dimension"] == "intensity_fit")
        self.assertTrue(reason["reason"].strip())

    def test_missing_everything_still_returns_structure(self):
        got = assess_session(_act(None), [], None, None, None)
        self.assertEqual(got["dimensions"], [])
        self.assertEqual(len(got["not_assessed"]), 3)


if __name__ == "__main__":
    unittest.main()
