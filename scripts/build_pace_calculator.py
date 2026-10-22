"""把配速計算機打包成單一離線 HTML（雙擊就能開，不需要後端、不需要網路）。

dashboard 的前端是 ES module 分檔載入，直接用 file:// 開會因為瀏覽器擋 module 而空白，
所以這裡把「頁面本體（js/views/tools.js）與它依賴的模組＋styles.css」內嵌成一個檔案。
dashboard 內的 #/tools 頁與離線檔共用同一份程式碼，改一處兩邊都會變。

不使用 esbuild 等打包工具（不新增依賴）：只支援本專案 js/ 內用到的寫法——
`import { a, b as c } from './x.js'`、`import * as N from './x.js'`、
`export function／var／const／let` 與 `export { a, b }`。遇到不支援的寫法會直接報錯，
不會默默打包出壞檔。每個模組包進自己的函式作用域，同名的區域變數不會互相衝突。

用法（專案根目錄）：
    python scripts/build_pace_calculator.py [輸出路徑]    預設 output/pace-calculator.html
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = ROOT / "src" / "main" / "resources" / "assets" / "dashboard"
ENTRY = DASHBOARD_DIR / "js" / "views" / "tools.js"
STYLES = DASHBOARD_DIR / "styles.css"
DEFAULT_OUT = ROOT / "output" / "pace-calculator.html"

# 這幾個是 dashboard 專屬模組（要打 API、讀全域狀態），不得被離線檔拉進來
FORBIDDEN_MODULES = ("core/state.js", "core/api.js", "core/facts.js")

_IMPORT_RE = re.compile(r"^import\s+(.+?)\s+from\s+'([^']+)';?[ \t]*$", re.MULTILINE)
_EXPORT_DECL_RE = re.compile(r"^export\s+(function|var|const|let)\s+([\w$]+)", re.MULTILINE)
_EXPORT_LIST_RE = re.compile(r"^export\s*\{([^}]*)\};?[ \t]*$", re.MULTILINE)

TEMPLATE = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>配速計算機</title>
<script>
  // 跟隨系統深淺色（styles.css 的深色只認 data-theme="dark"）
  (function () {
    var mq = window.matchMedia('(prefers-color-scheme: dark)');
    function apply() { document.documentElement.setAttribute('data-theme', mq.matches ? 'dark' : 'light'); }
    apply();
    if (mq.addEventListener) mq.addEventListener('change', apply);
  })();
</script>
<style>
__STYLES__
</style>
<style>
.standalone { max-width: 1100px; margin: 0 auto; padding: var(--gap); }
.standalone-title { margin: 8px 0 12px; font-size: var(--fs-xl); }
</style>
</head>
<body>
<main class="standalone">
  <h1 class="standalone-title">配速計算機</h1>
  <div id="tools-body"></div>
</main>
<script>
(function () {
var __m = {};
__MODULES__
document.addEventListener('DOMContentLoaded', function () {
  __m[__ENTRY__].renderToolsPage();
});
})();
</script>
</body>
</html>
"""


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _key(path: Path) -> str:
    return path.relative_to(DASHBOARD_DIR / "js").as_posix()


def _resolve(importer: Path, spec: str) -> Path:
    if not spec.startswith("."):
        sys.exit(f"不支援的 import（只允許相對路徑）：{spec}（{importer.name}）")
    target = (importer.parent / spec).resolve()
    if not target.is_file():
        sys.exit(f"找不到 import 的檔案：{spec}（{importer.name}）")
    return target


def _import_prelude(importer: Path, clause: str, target: Path) -> str:
    """把 import 子句換成對 __m 的取值。"""
    ref = f"__m[{json.dumps(_key(target))}]"
    clause = clause.strip()
    ns = re.fullmatch(r"\*\s+as\s+([\w$]+)", clause)
    if ns:
        return f"var {ns.group(1)} = {ref};"
    named = re.fullmatch(r"\{([^}]*)\}", clause)
    if not named:
        sys.exit(f"不支援的 import 寫法：{clause}（{importer.name}）")
    parts = []
    for item in filter(None, (p.strip() for p in named.group(1).split(","))):
        m = re.fullmatch(r"([\w$]+)(?:\s+as\s+([\w$]+))?", item)
        if not m:
            sys.exit(f"不支援的 import 項目：{item}（{importer.name}）")
        parts.append(f"{m.group(2) or m.group(1)} = {ref}.{m.group(1)}")
    return "var " + ", ".join(parts) + ";"


def _bundle_module(path: Path, order: list[str], bodies: dict[str, str]) -> None:
    key = _key(path)
    if key in bodies:
        return
    if key in FORBIDDEN_MODULES:
        sys.exit(f"離線檔不可依賴 dashboard 專屬模組：{key}")
    bodies[key] = ""  # 先佔位，避免循環 import 無限遞迴
    src = _read(path)

    def replace_import(match: re.Match) -> str:
        target = _resolve(path, match.group(2))
        _bundle_module(target, order, bodies)
        return _import_prelude(path, match.group(1), target)

    src = _IMPORT_RE.sub(replace_import, src)
    if re.search(r"^\s*import\s", src, re.MULTILINE):
        sys.exit(f"{path.name} 還有未處理的 import（只支援單行的靜態 import）")

    exports: list[str] = []

    def replace_decl(match: re.Match) -> str:
        exports.append(match.group(2))
        return f"{match.group(1)} {match.group(2)}"

    def replace_list(match: re.Match) -> str:
        exports.extend(n.strip() for n in match.group(1).split(",") if n.strip())
        return ""

    src = _EXPORT_DECL_RE.sub(replace_decl, src)
    src = _EXPORT_LIST_RE.sub(replace_list, src)
    if re.search(r"^\s*export\s", src, re.MULTILINE):
        sys.exit(f"{path.name} 有不支援的 export 寫法")

    returned = ", ".join(f"{n}: {n}" for n in dict.fromkeys(exports))
    bodies[key] = (
        f"__m[{json.dumps(key)}] = (function () {{\n{src}\nreturn {{ {returned} }};\n}})();"
    )
    order.append(key)  # 依賴先於自己完成 → 拓撲順序


def build() -> str:
    order: list[str] = []
    bodies: dict[str, str] = {}
    _bundle_module(ENTRY, order, bodies)
    # 防止 CSS/JS 內容含 </script> 或 </style> 提前結束標籤
    modules = "\n".join(bodies[k] for k in order).replace("</script", "<\\/script")
    styles = _read(STYLES).replace("</style", "<\\/style")
    return (
        TEMPLATE.replace("__STYLES__", styles)
        .replace("__MODULES__", modules)
        .replace("__ENTRY__", json.dumps(_key(ENTRY)))
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("out", nargs="?", type=Path, default=DEFAULT_OUT, help="輸出檔路徑")
    args = parser.parse_args()
    html = build()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"已產生 {args.out}（{len(html.encode('utf-8')) / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
