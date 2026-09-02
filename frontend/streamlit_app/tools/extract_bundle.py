"""Extract the readable design source out of the exported design bundle.

`frontend/My Assets Record (MAR)_Project Assets/My Assets Record (MAR)_Incubation.html`
is a 6.7 MB self-extracting export: a gzip+base64 manifest of 42 assets (fonts,
React UMD, the dc-runtime, the PRIZM design-system bundle) plus a JSON-encoded
`__bundler/template` string holding the real document.

This script writes, next to the Streamlit app:

  design/template.html   the decoded document (PRIZM <style>, markup, DCLogic)
  design/markup.html     just the markup, with the export's tag/attribute
                         aliases undone (sc-raw-td -> td, sc-camel-view-box ->
                         viewBox, ...) so it can be copied verbatim into the app
  styles/prizm.css       colors_and_type.css with the @font-face srcs repointed
                         at app/static/fonts/ and unused weights dropped
  static/fonts/*.ttf     only the weights the app actually renders

Run:  python tools/extract_bundle.py
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
REPO = APP_DIR.parent.parent
BUNDLE_DIR = REPO / "frontend" / "My Assets Record (MAR)_Project Assets"
BUNDLE = BUNDLE_DIR / "My Assets Record (MAR)_Incubation.html"

# Weights the markup actually uses (font-weight 400/500/600) plus the 700 the
# PRIZM heading tokens reach. No italics, no 100-300/800/900.
KEEP_FONTS = {
    "Inter_18pt-Regular.ttf",
    "Inter_18pt-Medium.ttf",
    "Inter_18pt-SemiBold.ttf",
    "Inter_18pt-Bold.ttf",
    "JetBrainsMono-Regular.ttf",
    "JetBrainsMono-Medium.ttf",
    "JetBrainsMono-SemiBold.ttf",
}

# The export renames tags it cannot nest and camel-cases React-only attributes.
RAW_TAGS = ("table", "thead", "tbody", "tr", "th", "td", "select")
CAMEL_ATTRS = {
    "sc-camel-view-box": "viewBox",
    "sc-camel-gradient-units": "gradientUnits",
}


def read_template(html: str) -> str:
    m = re.search(r'<script type="__bundler/template">(.*?)</script>', html, re.S)
    if not m:
        raise SystemExit("no __bundler/template block in the bundle")
    return json.loads(m.group(1))


def split_template(template: str) -> tuple[str, str, str]:
    """Return (helmet_css, markup, logic_script)."""
    logic_at = template.find('<script type="text/x-dc"')
    head, logic = template[:logic_at], template[logic_at:]
    helmet = re.search(r"<helmet>(.*?)</helmet>", head, re.S)
    if not helmet:
        raise SystemExit("no <helmet> block in the template")
    return helmet.group(1), head[helmet.end():], logic


def de_alias(markup: str) -> str:
    """Undo the export's tag and attribute aliases."""
    for tag in RAW_TAGS:
        markup = markup.replace(f"<sc-raw-{tag}", f"<{tag}")
        markup = markup.replace(f"</sc-raw-{tag}>", f"</{tag}>")
    for alias, real in CAMEL_ATTRS.items():
        markup = markup.replace(alias, real)
    # editor-only metadata
    markup = re.sub(r'\s*hint-placeholder-val="[^"]*"', "", markup)
    return markup


def rewrite_font_faces(css: str) -> str:
    """Point @font-face at Streamlit's static route; drop the unused weights."""
    fonts_dir = BUNDLE_DIR / "_ds"
    src_fonts = next(fonts_dir.glob("*/fonts"))
    by_uuid: dict[str, str] = {}

    def keep(block: str) -> bool:
        return any(name in block for name in by_uuid.values())

    # The bundled CSS references assets by uuid; the on-disk copy of the same
    # stylesheet references real filenames. Use the on-disk copy as the source.
    disk_css = next(fonts_dir.glob("*/colors_and_type.css")).read_text(encoding="utf-8")
    out_lines = []
    for line in disk_css.splitlines(keepends=True):
        m = re.search(r'url\("fonts/([^"]+)"\)', line)
        if m:
            if m.group(1) not in KEEP_FONTS:
                continue
            line = line.replace(
                f'url("fonts/{m.group(1)}")',
                f'url("app/static/fonts/{m.group(1)}")',
            )
        out_lines.append(line)
    del by_uuid, src_fonts, keep
    return "".join(out_lines)


def copy_fonts() -> int:
    src = next((BUNDLE_DIR / "_ds").glob("*/fonts"))
    dst = APP_DIR / "static" / "fonts"
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for name in sorted(KEEP_FONTS):
        f = src / name
        if not f.is_file():
            raise SystemExit(f"missing font {f}")
        shutil.copy2(f, dst / name)
        n += 1
    return n


def main() -> None:
    html = BUNDLE.read_text(encoding="utf-8", errors="replace")
    template = read_template(html)
    helmet, markup, logic = split_template(template)

    (APP_DIR / "design").mkdir(exist_ok=True)
    (APP_DIR / "design" / "template.html").write_text(template, encoding="utf-8")
    (APP_DIR / "design" / "markup.html").write_text(de_alias(markup), encoding="utf-8")
    (APP_DIR / "design" / "logic.js").write_text(logic, encoding="utf-8")

    (APP_DIR / "styles").mkdir(exist_ok=True)
    (APP_DIR / "styles" / "prizm.css").write_text(rewrite_font_faces(helmet), encoding="utf-8")

    n_fonts = copy_fonts()
    print(f"template.html {len(template):>7} chars")
    print(f"markup.html   {len(markup):>7} chars")
    print(f"prizm.css     {(APP_DIR / 'styles' / 'prizm.css').stat().st_size:>7} bytes")
    print(f"fonts         {n_fonts:>7} files")


if __name__ == "__main__":
    main()
