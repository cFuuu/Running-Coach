"""garmin_api_client 的單元測試——用 `_client` 注入點餵假回傳，不發網路請求、
不含真實憑證。

重點（Task 1B-9，2026-09-02 新增）：
1. 新的六個 fetch_* 方法確實呼叫底層 client 對應方法、參數正確
   （吃日期的傳 ISO 字串、審計報告確認不吃日期的不傳參數）。
2. `fetch_daily_wellness_raw()` 納入 training_status 後，單項失敗仍獨立
   容錯，不影響其他四項；rate limit 一律往外拋。
3. `fetch_performance_metrics_raw()` 五項獨立容錯，同樣只有 rate limit 往外拋。
"""

from __future__ import annotations

import datetime
import unittest

from src.main.python.services import garmin_api_client as client_module
from src.main.python.services.garmin_api_client import GarminApiClient, GarminFetchError


class StubClient:
    """假的底層 garminconnect client——記錄呼叫、依設定回傳值或拋例外。

    ⚠️ `calls` 同時記錄位置參數與 keyword 參數。初版只記 `*args`，於是
    「該用 keyword 卻傳位置參數」這類錯誤在測試裡看不出來——`get_lactate_
    threshold` 為 keyword-only，真實呼叫會 `TypeError`，假 client 卻照收，
    測試全過而正式環境整欄靜默落 NULL（2026-09-02 實測發現）。
    """

    #: 底層函式庫中為 keyword-only 的方法——傳位置參數會 TypeError。
    #: 假 client 必須複製這個限制，否則測不出呼叫方式的錯誤。
    KEYWORD_ONLY = {"get_lactate_threshold"}

    def __init__(self, **methods):
        self._methods = methods
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        def _invoke(*args, **kwargs):
            if name in self.KEYWORD_ONLY and args:
                raise TypeError(
                    f"{name}() takes 1 positional argument but {len(args) + 1} were given"
                )
            self.calls.append((name, args, kwargs))
            value = self._methods.get(name)
            if isinstance(value, BaseException):
                raise value
            return value

        return _invoke


def _make_client(**methods) -> GarminApiClient:
    """建立不節流（間隔 0 秒）的 client，避免測試因 `_throttle()` 變慢。"""
    return GarminApiClient(request_interval_sec=0, _client=StubClient(**methods))


class TestNewFetchMethods(unittest.TestCase):
    """六個新 fetch 方法：正確呼叫底層方法名與參數。"""

    def setUp(self):
        self.cdate = datetime.date(2026, 8, 31)

    def test_fetch_training_status_passes_iso_date(self):
        stub = StubClient(get_training_status={"trainingStatus": 4})
        client = GarminApiClient(request_interval_sec=0, _client=stub)
        result = client.fetch_training_status(self.cdate)
        self.assertEqual(result, {"trainingStatus": 4})
        self.assertEqual(stub.calls, [("get_training_status", ("2026-08-31",), {})])

    def test_fetch_race_predictions_takes_no_date_argument(self):
        """★ 審計報告 §2.4 實測：此端點不吃日期參數，回傳當前值。"""
        stub = StubClient(get_race_predictions={"time5K": 1403})
        client = GarminApiClient(request_interval_sec=0, _client=stub)
        result = client.fetch_race_predictions()
        self.assertEqual(result, {"time5K": 1403})
        self.assertEqual(stub.calls, [("get_race_predictions", (), {})])

    def test_fetch_lactate_threshold_uses_keyword_only_call(self):
        """★ 此端點為 keyword-only——傳位置參數會 TypeError 而靜默落 NULL。

        `StubClient.KEYWORD_ONLY` 複製了真實函式庫的限制，故若實作改回
        位置參數，這個測試會失敗而非誤過（2026-09-02 實測發現的缺陷）。
        """
        stub = StubClient(get_lactate_threshold={"speed_and_heart_rate": {}})
        client = GarminApiClient(request_interval_sec=0, _client=stub)
        client.fetch_lactate_threshold()
        self.assertEqual(stub.calls, [("get_lactate_threshold", (), {"latest": True})])

    def test_fetch_endurance_score_passes_iso_date(self):
        stub = StubClient(get_endurance_score={"overallScore": 5905})
        client = GarminApiClient(request_interval_sec=0, _client=stub)
        client.fetch_endurance_score(self.cdate)
        self.assertEqual(stub.calls, [("get_endurance_score", ("2026-08-31",), {})])

    def test_fetch_fitness_age_passes_iso_date(self):
        stub = StubClient(get_fitnessage_data={"fitnessAge": 19.8})
        client = GarminApiClient(request_interval_sec=0, _client=stub)
        client.fetch_fitness_age(self.cdate)
        self.assertEqual(stub.calls, [("get_fitnessage_data", ("2026-08-31",), {})])

    def test_fetch_hill_score_passes_iso_date(self):
        stub = StubClient(get_hill_score={"overallScore": 27})
        client = GarminApiClient(request_interval_sec=0, _client=stub)
        client.fetch_hill_score(self.cdate)
        self.assertEqual(stub.calls, [("get_hill_score", ("2026-08-31",), {})])


class TestFetchDailyWellnessRawIncludesTrainingStatus(unittest.TestCase):
    """★ Task 1B-9：training_status 併入每日彙整，端點數由 4 → 5。"""

    def setUp(self):
        self.cdate = datetime.date(2026, 8, 31)

    def test_all_five_sources_present(self):
        client = _make_client(
            get_stats={"totalSteps": 100},
            get_sleep_data={"dailySleepDTO": {}},
            get_hrv_data={"hrvSummary": {}},
            get_training_readiness=[{"score": 60}],
            get_training_status={"trainingStatus": 4},
        )
        out = client.fetch_daily_wellness_raw(self.cdate)
        self.assertEqual(out["date"], "2026-08-31")
        self.assertEqual(out["stats"], {"totalSteps": 100})
        self.assertEqual(out["training_status"], {"trainingStatus": 4})

    def test_training_status_failure_isolated_from_other_fields(self):
        """單項抓不到不影響其他項，該項回 None。"""
        client = _make_client(
            get_stats={"totalSteps": 100},
            get_sleep_data=None,
            get_hrv_data=None,
            get_training_readiness=[],
            get_training_status=GarminFetchError("Timeout"),
        )
        out = client.fetch_daily_wellness_raw(self.cdate)
        self.assertIsNone(out["training_status"])
        self.assertEqual(out["stats"], {"totalSteps": 100})

    def test_rate_limit_on_training_status_propagates(self):
        """★ 429 一律往外拋，不可被當成單項失敗吞掉。"""
        client = _make_client(
            get_stats={"totalSteps": 100},
            get_sleep_data=None,
            get_hrv_data=None,
            get_training_readiness=[],
            get_training_status=Exception("429 Too Many Requests"),
        )
        with self.assertRaises(client_module.GarminRateLimited):
            client.fetch_daily_wellness_raw(self.cdate)


class TestFetchPerformanceMetricsRaw(unittest.TestCase):
    """★ 新增：五個效能指標端點彙整成一個 dict，供 normalize_performance_metrics 使用。"""

    def setUp(self):
        self.cdate = datetime.date(2026, 9, 1)

    def test_aggregates_all_five_sources(self):
        client = _make_client(
            get_race_predictions={"time5K": 1403},
            get_lactate_threshold={"heartRate": 179.0},
            get_endurance_score={"overallScore": 5905},
            get_fitnessage_data={"fitnessAge": 19.8},
            get_hill_score={"overallScore": 27},
        )
        out = client.fetch_performance_metrics_raw(self.cdate)
        self.assertEqual(out["race_predictions"], {"time5K": 1403})
        self.assertEqual(out["lactate_threshold"], {"heartRate": 179.0})
        self.assertEqual(out["endurance_score"], {"overallScore": 5905})
        self.assertEqual(out["fitness_age"], {"fitnessAge": 19.8})
        self.assertEqual(out["hill_score"], {"overallScore": 27})

    def test_single_field_failure_isolated(self):
        """★ 簽章不符（如 hill_score 實際吃區間而非單日）僅讓該項回 None，
        不影響其他四項——見 fetch_performance_metrics_raw docstring。"""
        client = _make_client(
            get_race_predictions={"time5K": 1403},
            get_lactate_threshold=GarminFetchError("抓取失敗"),
            get_endurance_score={"overallScore": 5905},
            get_fitnessage_data=None,
            get_hill_score={"overallScore": 27},
        )
        out = client.fetch_performance_metrics_raw(self.cdate)
        self.assertIsNone(out["lactate_threshold"])
        self.assertIsNone(out["fitness_age"])
        self.assertEqual(out["race_predictions"], {"time5K": 1403})
        self.assertEqual(out["hill_score"], {"overallScore": 27})

    def test_rate_limit_propagates_and_aborts(self):
        client = _make_client(
            get_race_predictions={"time5K": 1403},
            get_lactate_threshold=Exception("429"),
            get_endurance_score={"overallScore": 5905},
            get_fitnessage_data=None,
            get_hill_score=None,
        )
        with self.assertRaises(client_module.GarminRateLimited):
            client.fetch_performance_metrics_raw(self.cdate)


if __name__ == "__main__":
    unittest.main()
