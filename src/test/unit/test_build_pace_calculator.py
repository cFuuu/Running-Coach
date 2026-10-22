"""scripts/build_pace_calculator.py 的測試：離線單檔必須自給自足。"""

import importlib.util
import unittest
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "build_pace_calculator.py"
_spec = importlib.util.spec_from_file_location("build_pace_calculator", _SCRIPT)
build_pace_calculator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_pace_calculator)


class TestBuildPaceCalculator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = build_pace_calculator.build()

    def test_bundles_entry_and_dependencies(self):
        for module in ("views/tools.js", "core/pace.js", "core/dom.js", "charts.js"):
            self.assertIn(f'__m["{module}"] = ', self.html)
        self.assertIn('__m["views/tools.js"].renderToolsPage()', self.html)

    def test_is_self_contained(self):
        # 沒有 import／export 殘留、外部資源，JS 部分也沒有發請求的程式碼
        # （註解會提到 /api/，所以只檢查實際發請求的寫法）
        for needle in ("\nimport ", "\nexport ", "<link ", "src="):
            self.assertNotIn(needle, self.html)
        js = self.html[self.html.index("var __m = {};"):]
        for needle in ("fetch(", "XMLHttpRequest", "WebSocket"):
            self.assertNotIn(needle, js)

    def test_excludes_dashboard_only_modules(self):
        for module in build_pace_calculator.FORBIDDEN_MODULES:
            self.assertNotIn(f'__m["{module}"]', self.html)


if __name__ == "__main__":
    unittest.main()
