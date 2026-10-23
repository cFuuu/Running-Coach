"""Dashboard 主題色的 WCAG 對比度測試（N-19 §1）。

【為什麼要有這支測試】
    配色的可讀性用眼睛判斷會出錯。本專案就發生過：憑感覺挑的深色邊框
    `--accent-border: #2e4a63` 對卡片底實測只有 1.76:1，遠低於 WCAG
    非文字元件的 3:1 門檻，深色模式下邊框幾乎看不見，但沒有人「看出來」。

    因此這支測試**直接解析 styles.css 算對比度**，而不是比對一份硬編碼的
    色號清單——後者只會在有人改色時跟著改，永遠會過，等於沒測。
    日後任何人調整 accent 色系而破壞可讀性，這裡會直接失敗。

【WCAG 2.1 門檻】
    1.4.3 文字（一般字級）        4.5:1
    1.4.11 非文字元件（邊框等）   3.0:1

【淺色模式的門檻為何與深色不同】
    深色的門檻取自 N19 spec §1.5（地端實測值）。淺色模式有兩條無法直接套用，
    原因是淺色主題的「淡底＋淡框」本來就是低對比的設計語言，不是這次改藍造成的：

      --accent-border (#bcd4ea) vs --surface (#ffffff) = 1.53
          改藍前的綠色版 #bcd9c8 是 1.51，通用的 --border (#dfe3e8) 更只有 1.29。
          淺色模式的「可辨識」是靠 --accent 本身（5.66:1）承載，不是靠邊框；
          要讓淺框達 3:1 必須把它壓深到接近 --accent，整個淺色卡片系統都得重畫。
          故此處改為相對斷言：accent 邊框至少要和普通邊框一樣看得見。

      --accent-soft (#e4eef8) vs --bg (#f5f6f8) = 1.09
          改藍前的綠色版 #e3f0e8 同樣是 1.09。淺色的 soft 底是「幾乎白」的淡藍，
          純對比度數字分辨不出「淡藍」與「淡灰」，故改用色相斷言（藍通道明顯
          高於紅通道）來確保它真的有上色，這比放寬一個數字門檻更抓得到問題。

    三條真正承載文字的門檻（accent / accent-text / on-accent）淺深兩色一律 4.5:1，不放寬。
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
STYLES_CSS = REPO_ROOT / "src" / "main" / "resources" / "assets" / "dashboard" / "styles.css"

# WCAG 2.1 門檻
TEXT_CONTRAST = 4.5  # 1.4.3 一般字級文字
NON_TEXT_CONTRAST = 3.0  # 1.4.11 非文字元件（邊框、圖示）
TINTED_SURFACE_CONTRAST = 1.2  # 「看得出有上色」（非 WCAG，見 spec §1.5）

# 中性色的通道最大落差容忍值：超過就代表被染上色相。
# 現況最大者為淺色 --text-muted #66727f（落差 25）與深色 --surface-sunken #20262c（12）。
NEUTRAL_TEXT_SPREAD = 32
NEUTRAL_SURFACE_SPREAD = 16

# --accent-soft 這類「淡底」要有的最低飽和度，用來區分「真的有上色」與「中性灰」。
# 現況淺色 #e4eef8 為 0.59、深色 #233648 為 0.35。
MIN_TINT_SATURATION = 0.25


# ------------------------------------------------------------ CSS 解析

def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _parse_block(css: str, selector: str) -> dict[str, str]:
    """從 CSS 取出某個選擇器區塊裡的所有自訂屬性。

    只處理本檔實際的形狀（:root 區塊無巢狀），因此用「選擇器 → 第一個 }」
    的最短匹配即可，不需要寫一個完整的 CSS parser。
    """
    body = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", css, flags=re.DOTALL)
    if body is None:
        raise AssertionError(f"styles.css 找不到 {selector} 區塊")
    return {
        name: value.strip()
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body.group(1))
    }


def _rgb(value: str) -> tuple[int, int, int]:
    """把 CSS 色字串轉成 0-255 三通道。只支援本檔用到的 #rgb / #rrggbb。"""
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", text):
        raise AssertionError(f"無法解析色值：{value!r}（預期 #rgb 或 #rrggbb）")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


# ------------------------------------------------------------ WCAG 公式

def relative_luminance(color: str) -> float:
    """WCAG 2.1 相對亮度：sRGB → 線性化 → 0.2126/0.7152/0.0722 加權。"""
    channels = []
    for raw in _rgb(color):
        c = raw / 255.0
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.1 對比度：(較亮 + 0.05) / (較暗 + 0.05)，範圍 1:1 ~ 21:1。"""
    a, b = relative_luminance(fg), relative_luminance(bg)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def channel_spread(color: str) -> int:
    """三通道的最大落差。0 為純中性灰，數字越大色相越明顯。"""
    r, g, b = _rgb(color)
    return max(r, g, b) - min(r, g, b)


def hsl_saturation(color: str) -> float:
    """HSL 飽和度（0=中性灰，1=純色）。

    判斷「淡底到底有沒有上色」要用飽和度而非通道落差：接近白色的淡藍
    （--accent-soft #e4eef8）通道落差只有 20，和一個中性淺灰差不多，
    但飽和度 0.59 明顯高於中性灰。
    """
    r, g, b = (c / 255.0 for c in _rgb(color))
    high, low = max(r, g, b), min(r, g, b)
    if high == low:
        return 0.0
    lightness = (high + low) / 2
    return (high - low) / (2 - high - low) if lightness > 0.5 else (high - low) / (high + low)


class ContrastFormulaTest(unittest.TestCase):
    """先驗證公式本身，否則後面所有斷言都建立在未驗證的計算上。"""

    def test_known_luminance_endpoints(self):
        self.assertAlmostEqual(relative_luminance("#000000"), 0.0, places=6)
        self.assertAlmostEqual(relative_luminance("#ffffff"), 1.0, places=6)

    def test_black_on_white_is_21_to_1(self):
        self.assertAlmostEqual(contrast_ratio("#000000", "#ffffff"), 21.0, places=4)

    def test_ratio_is_symmetric_and_self_is_one(self):
        self.assertAlmostEqual(contrast_ratio("#2563a8", "#f5f6f8"),
                               contrast_ratio("#f5f6f8", "#2563a8"), places=9)
        self.assertAlmostEqual(contrast_ratio("#2563a8", "#2563a8"), 1.0, places=9)

    def test_shorthand_hex_expands(self):
        self.assertEqual(_rgb("#fff"), (255, 255, 255))

    def test_rejects_unparseable_color(self):
        with self.assertRaises(AssertionError):
            _rgb("rgba(37, 99, 168, 0.4)")


class ThemeTokenTestBase(unittest.TestCase):
    """讀一次 CSS，兩個主題的測試共用。"""

    selector = ""

    @classmethod
    def setUpClass(cls):
        if cls is ThemeTokenTestBase:
            return
        css = _strip_comments(STYLES_CSS.read_text(encoding="utf-8"))
        cls.tokens = _parse_block(css, cls.selector)

    def token(self, name: str) -> str:
        self.assertIn(name, self.tokens, f"{self.selector} 缺少 {name}")
        return self.tokens[name]

    def assertContrastAtLeast(self, fg_token: str, bg_token: str, minimum: float):
        fg, bg = self.token(fg_token), self.token(bg_token)
        ratio = contrast_ratio(fg, bg)
        self.assertGreaterEqual(
            ratio, minimum,
            f"{self.selector} {fg_token} ({fg}) vs {bg_token} ({bg}) "
            f"對比只有 {ratio:.2f}:1，需 ≥ {minimum}:1",
        )


class DarkThemeContrastTest(ThemeTokenTestBase):
    """深色模式。門檻與 N19 spec §1.5 一致（地端實測值）。"""

    selector = ':root[data-theme="dark"]'

    def test_accent_on_page_background_is_readable(self):
        self.assertContrastAtLeast("--accent", "--bg", TEXT_CONTRAST)

    def test_accent_text_on_page_background_is_readable(self):
        self.assertContrastAtLeast("--accent-text", "--bg", TEXT_CONTRAST)

    def test_accent_border_on_card_meets_non_text_threshold(self):
        # 這正是當初 1.76:1 出包的那一條。
        self.assertContrastAtLeast("--accent-border", "--surface", NON_TEXT_CONTRAST)

    def test_foreground_on_accent_is_readable(self):
        # 深色 accent 是亮藍，上面必須配深色字：白字只有 2.52:1。
        self.assertContrastAtLeast("--on-accent", "--accent", TEXT_CONTRAST)

    def test_accent_soft_is_visibly_tinted_against_page(self):
        self.assertContrastAtLeast("--accent-soft", "--bg", TINTED_SURFACE_CONTRAST)


class LightThemeContrastTest(ThemeTokenTestBase):
    """淺色模式。承載文字的三條同樣 4.5:1；兩條淡色 token 見檔頭說明。"""

    selector = ":root"

    def test_accent_on_page_background_is_readable(self):
        self.assertContrastAtLeast("--accent", "--bg", TEXT_CONTRAST)

    def test_accent_text_on_page_background_is_readable(self):
        self.assertContrastAtLeast("--accent-text", "--bg", TEXT_CONTRAST)

    def test_foreground_on_accent_is_readable(self):
        self.assertContrastAtLeast("--on-accent", "--accent", TEXT_CONTRAST)

    def test_accent_border_is_at_least_as_visible_as_a_plain_border(self):
        """相對斷言：強調邊框不得比普通邊框還不明顯。

        淺色主題整體用的就是低對比的淡框（--border 對白卡只有 1.29:1），
        套 3:1 會逼得整套卡片重畫；但「強調框比普通框還淡」一定是錯的。
        """
        accent_border = self.token("--accent-border")
        plain_border = self.token("--border")
        surface = self.token("--surface")
        accent_ratio = contrast_ratio(accent_border, surface)
        plain_ratio = contrast_ratio(plain_border, surface)
        self.assertGreaterEqual(
            accent_ratio, plain_ratio,
            f"--accent-border ({accent_border}) 對卡片底只有 {accent_ratio:.2f}:1，"
            f"比普通 --border ({plain_border}) 的 {plain_ratio:.2f}:1 還不明顯",
        )


class AccentHueTest(unittest.TestCase):
    """N-19 的要求是「藍色主題」。對比度管得住可讀性，管不住色相——
    有人把 accent 改回綠色但對比度照樣達標時，上面的測試不會失敗。"""

    @classmethod
    def setUpClass(cls):
        css = _strip_comments(STYLES_CSS.read_text(encoding="utf-8"))
        cls.light = _parse_block(css, ":root")
        cls.dark = _parse_block(css, ':root[data-theme="dark"]')

    def assertIsBlue(self, label: str, color: str):
        r, g, b = _rgb(color)
        self.assertGreater(b, r, f"{label} ({color}) 的藍通道未高於紅通道，不是藍色")
        self.assertGreater(b, g, f"{label} ({color}) 的藍通道未高於綠通道，不是藍色")

    def test_accent_is_blue_in_both_themes(self):
        self.assertIsBlue("淺色 --accent", self.light["--accent"])
        self.assertIsBlue("深色 --accent", self.dark["--accent"])

    def test_accent_text_is_blue_in_both_themes(self):
        self.assertIsBlue("淺色 --accent-text", self.light["--accent-text"])
        self.assertIsBlue("深色 --accent-text", self.dark["--accent-text"])

    def test_accent_soft_is_actually_tinted_not_neutral_grey(self):
        # 淺色的 --accent-soft 對比度只有 1.09:1，純看數字分不出淡藍與淡灰，
        # 改用飽和度＋藍通道優勢確認它真的有上色。
        # 現況：淺色 #e4eef8 為 0.59、深色 #233648 為 0.35；中性灰遠低於 0.25。
        for label, tokens in (("淺色", self.light), ("深色", self.dark)):
            with self.subTest(theme=label):
                soft = tokens["--accent-soft"]
                self.assertIsBlue(f"{label} --accent-soft", soft)
                saturation = hsl_saturation(soft)
                self.assertGreaterEqual(
                    saturation, MIN_TINT_SATURATION,
                    f"{label} --accent-soft ({soft}) 飽和度僅 {saturation:.2f}，看起來會像中性灰",
                )


class ChartPaletteCollisionTest(unittest.TestCase):
    """N19 spec §1.4：圖表色系不隨主題色改動。

    accent 改藍之後最容易踩的坑，是有人為了「視覺統一」把圖表色也一起改藍——
    那會讓同一張圖上的兩條線分不出來。
    """

    @classmethod
    def setUpClass(cls):
        css = _strip_comments(STYLES_CSS.read_text(encoding="utf-8"))
        cls.light = _parse_block(css, ":root")
        cls.dark = _parse_block(css, ':root[data-theme="dark"]')

    def test_accent_does_not_collide_with_distance_series(self):
        for label, tokens in (("淺色", self.light), ("深色", self.dark)):
            with self.subTest(theme=label):
                self.assertNotEqual(
                    _rgb(tokens["--accent"]), _rgb(tokens["--c-distance"]),
                    f"{label} --accent 與 --c-distance 同色，圖表線與 UI 強調色會混淆",
                )

    def test_pace_series_stays_green(self):
        # --c-pace 同時是 --activity-running 的來源；改藍會與已是藍色的
        # --c-distance 撞色，同一張圖上兩條線分不出來。
        for label, tokens in (("淺色", self.light), ("深色", self.dark)):
            with self.subTest(theme=label):
                pace = tokens["--c-pace"]
                r, g, b = _rgb(pace)
                self.assertGreater(g, r, f"{label} --c-pace ({pace}) 已不是綠色")
                self.assertGreater(g, b, f"{label} --c-pace ({pace}) 已不是綠色")

    def test_activity_running_still_references_pace(self):
        self.assertEqual(self.light["--activity-running"], "var(--c-pace)")


SPORT_ICONS_JS = STYLES_CSS.parent / "js" / "core" / "sport-icons.js"


class SportChipContrastTest(unittest.TestCase):
    """E.1 運動種類膠囊：每種運動的 --sport-<key>-fg 疊在 --sport-<key>-bg 上
    必須 ≥ 4.5:1（膠囊裡是公里數／時間文字）。淺色 8 組＋深色 8 組共 16 組。

    種類清單直接從 sport-icons.js 的 SPORT_KEYS 讀，不在這裡另抄一份——
    之後新增種類卻忘了補 token，這裡會直接失敗。
    """

    @classmethod
    def setUpClass(cls):
        css = _strip_comments(STYLES_CSS.read_text(encoding="utf-8"))
        cls.themes = {
            "淺色": _parse_block(css, ":root"),
            "深色": _parse_block(css, ':root[data-theme="dark"]'),
        }
        js = SPORT_ICONS_JS.read_text(encoding="utf-8")
        match = re.search(r"SPORT_KEYS\s*=\s*\[([^\]]*)\]", js)
        if match is None:
            raise AssertionError("sport-icons.js 找不到 SPORT_KEYS")
        cls.sport_keys = re.findall(r"'(\w+)'", match.group(1))

    def test_sport_keys_are_the_eight_defined_in_spec(self):
        self.assertEqual(
            self.sport_keys,
            ["run", "strength", "walk", "hike", "bike", "swim", "race", "other"],
        )

    def test_every_sport_text_is_readable_on_its_chip(self):
        checked = 0
        for label, tokens in self.themes.items():
            for key in self.sport_keys:
                with self.subTest(theme=label, sport=key):
                    fg_name, bg_name = f"--sport-{key}-fg", f"--sport-{key}-bg"
                    self.assertIn(fg_name, tokens, f"{label} 缺少 {fg_name}")
                    self.assertIn(bg_name, tokens, f"{label} 缺少 {bg_name}")
                    ratio = contrast_ratio(tokens[fg_name], tokens[bg_name])
                    self.assertGreaterEqual(
                        ratio, TEXT_CONTRAST,
                        f"{label} {fg_name} ({tokens[fg_name]}) vs {bg_name} "
                        f"({tokens[bg_name]}) 對比只有 {ratio:.2f}:1，需 ≥ {TEXT_CONTRAST}:1",
                    )
                    checked += 1
        self.assertEqual(checked, 16)


class NeutralTokenTest(unittest.TestCase):
    """N19 spec §1.4 的兩條底線：正文不染藍、大面積背景維持中性。

    藍色深色主題最常見的失敗就是把背景整片染藍、正文也跟著偏藍，
    結果「一片藍看不清楚」。這裡不比對硬編碼色號，而是驗證這些 token
    的三通道落差夠小 —— 真正被染上色相時落差會明顯拉開
    （對照：--accent-soft #233648 的落差是 37）。
    """

    @classmethod
    def setUpClass(cls):
        css = _strip_comments(STYLES_CSS.read_text(encoding="utf-8"))
        cls.themes = {
            "淺色": _parse_block(css, ":root"),
            "深色": _parse_block(css, ':root[data-theme="dark"]'),
        }

    def test_body_text_tokens_stay_neutral(self):
        for label, tokens in self.themes.items():
            for name in ("--text", "--text-muted"):
                with self.subTest(theme=label, token=name):
                    spread = channel_spread(tokens[name])
                    self.assertLessEqual(
                        spread, NEUTRAL_TEXT_SPREAD,
                        f"{label} {name} ({tokens[name]}) 通道落差 {spread}，正文被染上色相",
                    )

    def test_large_background_tokens_stay_neutral(self):
        for label, tokens in self.themes.items():
            for name in ("--bg", "--surface", "--surface-sunken"):
                with self.subTest(theme=label, token=name):
                    spread = channel_spread(tokens[name])
                    self.assertLessEqual(
                        spread, NEUTRAL_SURFACE_SPREAD,
                        f"{label} {name} ({tokens[name]}) 通道落差 {spread}，大面積背景被染色",
                    )


if __name__ == "__main__":
    unittest.main()
