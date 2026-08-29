"""data_confidence 的單元測試——全部使用合成資料，不含真實個人資料。

重點在驗證 PLAN.md §7.3 的硬規則：信賴度必填、unusable 一律不輸出裸數字。
"""

import unittest

from src.main.python.services import data_confidence as dc


class DescribeBiasTest(unittest.TestCase):
    def test_known_code_returns_level_and_detail(self):
        got = dc.describe_bias("hrmax_watch_display")
        self.assertEqual(got["code"], "hrmax_watch_display")
        self.assertEqual(got["level"], "low")
        self.assertIn("HRmax", got["detail"])

    def test_unknown_code_is_treated_as_unusable(self):
        """未定義的代碼必須保守擋下，不可讓它以 high 信賴度流到判斷層。"""
        got = dc.describe_bias("something_never_defined")
        self.assertEqual(got["level"], "unusable")

    def test_every_known_bias_has_a_valid_level_and_nonempty_detail(self):
        for code, (level, detail) in dc.KNOWN_BIASES.items():
            with self.subTest(code=code):
                self.assertIn(level, ("high", "low", "unusable"))
                self.assertTrue(detail.strip(), "偏誤說明不可為空——AI 需要知道錯在哪")


class CombineLevelTest(unittest.TestCase):
    def test_no_bias_is_high(self):
        self.assertEqual(dc.combine_level([]), "high")

    def test_single_low(self):
        self.assertEqual(dc.combine_level(["hrmax_watch_display"]), "low")

    def test_unusable_dominates_low(self):
        """多個偏誤並存時取最保守者。"""
        self.assertEqual(
            dc.combine_level(["hrmax_watch_display", "workout_type_unknown"]),
            "unusable",
        )


class FactTest(unittest.TestCase):
    def test_clean_fact_is_high_confidence(self):
        got = dc.fact(42.0, source="測試來源")
        self.assertEqual(got["value"], 42.0)
        self.assertEqual(got["source"], "測試來源")
        self.assertEqual(got["confidence"]["level"], "high")
        self.assertEqual(got["confidence"]["biases"], [])

    def test_low_confidence_fact_carries_bias_detail(self):
        got = dc.fact(198, source="watch", bias_codes=["hrmax_watch_display"])
        self.assertEqual(got["confidence"]["level"], "low")
        self.assertEqual(len(got["confidence"]["biases"]), 1)

    def test_unusable_returns_none_rather_than_bare_number(self):
        """§7.3 硬規則：無法標記信賴度就不輸出，寧可 None 也不給裸數字。"""
        self.assertIsNone(
            dc.fact(1.23, source="x", bias_codes=["workout_type_unknown"])
        )

    def test_every_fact_has_all_three_contract_fields(self):
        got = dc.fact(1, source="s")
        self.assertEqual(set(got), {"value", "source", "confidence"})


class HrmaxBiasCodesTest(unittest.TestCase):
    def test_measured_has_no_bias(self):
        self.assertEqual(dc.hrmax_bias_codes("measured"), [])

    def test_watch_display_is_flagged(self):
        self.assertEqual(dc.hrmax_bias_codes("watch_display"), ["hrmax_watch_display"])

    def test_age_formula_is_flagged_separately(self):
        self.assertEqual(dc.hrmax_bias_codes("age_formula"), ["hrmax_age_formula"])

    def test_none_and_unknown_sources_default_to_flagged(self):
        """缺來源不等於實測——保守視為未實測。"""
        self.assertEqual(dc.hrmax_bias_codes(None), ["hrmax_watch_display"])
        self.assertEqual(dc.hrmax_bias_codes("何方神聖"), ["hrmax_watch_display"])


class WorkoutTypeBiasCodesTest(unittest.TestCase):
    def test_labeled_workout_has_no_bias(self):
        self.assertEqual(dc.workout_type_bias_codes("interval"), [])

    def test_unknown_null_and_empty_are_all_blocked(self):
        for value in ("unknown", None, ""):
            with self.subTest(value=value):
                self.assertEqual(
                    dc.workout_type_bias_codes(value), ["workout_type_unknown"]
                )

    def test_blocked_workout_type_is_unusable_level(self):
        codes = dc.workout_type_bias_codes("unknown")
        self.assertEqual(dc.combine_level(codes), "unusable")


if __name__ == "__main__":
    unittest.main()
