"""Shared markup for the MAR screens.

Every fragment here is copied verbatim from the exported design
(`design/markup.html`): the same tags, the same inline `style` attributes, the
same PRIZM custom properties. Nothing is restyled — the only edits are the
template bindings (`{{ … }}`) becoming Python values and the export's tag
aliases (`sc-raw-td`, `sc-camel-view-box`) being restored to real HTML.

Anything the operator can click or type into is a real Streamlit widget; the
CSS in styles/app.css lays those out inside these fragments and gives them the
design's control spec.
"""

from __future__ import annotations

import html

# --------------------------------------------------------------------------- #
# Icons — lifted from the design, unchanged.
# --------------------------------------------------------------------------- #

# The AI sparkle is painted with a gradient defined once per page.
AI_GRADIENT_DEFS = (
    '<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>'
    '<linearGradient id="aig" x1="9" y1="6" x2="4.6946" y2="7.27033" '
    'gradientUnits="userSpaceOnUse">'
    '<stop stop-color="#00DCFF"></stop><stop offset="1" stop-color="#1B65F8"></stop>'
    '</linearGradient></defs></svg>'
)

_SPARKLE_PATH = (
    '<path d="M6.33333 12.6667L5.44255 10.7069C4.9423 9.6064 4.06027 8.72436 '
    '2.95973 8.22412L1 7.33333L2.95973 6.44255C4.06027 5.9423 4.9423 5.06027 '
    '5.44255 3.95973L6.33333 2L7.22412 3.95973C7.72436 5.06027 8.6064 5.9423 '
    '9.70694 6.44255L11.6667 7.33333L9.70694 8.22412C8.6064 8.72436 7.72437 '
    '9.6064 7.22412 10.7069L6.33333 12.6667ZM11.6667 14L11.6092 13.8736C11.109 '
    '12.7731 10.2269 11.891 9.1264 11.3908L9 11.3333L9.1264 11.2759C10.2269 '
    '10.7756 11.109 9.8936 11.6092 8.79306L11.6667 8.66667L11.7241 8.79306C12.2244 '
    '9.8936 13.1064 10.7756 14.2069 11.2759L14.3333 11.3333L14.2069 11.3908C13.1064 '
    '11.891 12.2244 12.7731 11.7241 13.8736L11.6667 14Z" fill="url(#aig)"></path>'
)


def sparkle(size: int, style: str = "") -> str:
    attr = f' style="{style}"' if style else ""
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 16 16" fill="none"'
            f'{attr}>{_SPARKLE_PATH}</svg>')


ICON_LOGO = ('<svg width="20" height="20" viewBox="0 0 24 24" fill="none" '
             'stroke="var(--prizm-color-accent)"><path d="M12 2L22 20H2L12 2Z"></path>'
             '<path d="M12 2L12 20"></path></svg>')

ICON_HELP = ('<svg width="18" height="18" viewBox="0 0 24 24" fill="none" '
             'stroke="currentColor"><circle cx="12" cy="12" r="10"></circle>'
             '<path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path>'
             '<path d="M12 17h.01"></path></svg>')

ICON_INFO = ('<svg width="13" height="13" viewBox="0 0 24 24" fill="none" '
             'stroke="currentColor"><circle cx="12" cy="12" r="10"></circle>'
             '<path d="M12 16v-4"></path><path d="M12 8h.01"></path></svg>')

ICON_LIST = ('<svg width="17" height="17" viewBox="0 0 24 24" fill="none" '
             'stroke="currentColor"><path d="M3 5h2"></path><path d="M3 12h2"></path>'
             '<path d="M3 19h2"></path><path d="M9 5h12"></path>'
             '<path d="M9 12h12"></path><path d="M9 19h12"></path></svg>')

ICON_CHEVRON_RIGHT_15 = ('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" '
                         'stroke="var(--prizm-color-fg-subtle)">'
                         '<path d="M9 18l6-6-6-6"></path></svg>')

ICON_CHEVRON_RIGHT_13 = ('<svg width="13" height="13" viewBox="0 0 24 24" fill="none" '
                         'stroke="currentColor"><path d="m9 18 6-6-6-6"></path></svg>')

ICON_FOLDER = ('<svg width="20" height="20" viewBox="0 0 24 24" fill="none" '
               'stroke="var(--prizm-color-fg-subtle)">'
               '<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 '
               '1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 '
               '2Z"></path></svg>')

ICON_FILE = ('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" '
             'stroke="var(--prizm-color-fg-subtle)" style="flex:none">'
             '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 '
             '0 2-2V8z"></path><path d="M14 2v6h6"></path></svg>')

ICON_SEARCH = ('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" '
               'stroke="var(--prizm-color-fg-subtle)" '
               'style="position:absolute;left:10px;top:10px">'
               '<circle cx="11" cy="11" r="8"></circle>'
               '<path d="m21 21-4.3-4.3"></path></svg>')

ICON_FILTER = ('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" '
               'stroke="currentColor"><path d="M3 6h18"></path>'
               '<path d="M7 12h10"></path><path d="M10 18h4"></path></svg>')

ICON_CARET_DOWN = ('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" '
                   'stroke="currentColor"><path d="m6 9 6 6 6-6"></path></svg>')

ICON_PLUS = ('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" '
             'stroke="currentColor"><path d="M12 5v14"></path>'
             '<path d="M5 12h14"></path></svg>')

ICON_ALERT = ('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" '
              'stroke="var(--prizm-color-danger)" style="flex:none">'
              '<circle cx="12" cy="12" r="10"></circle><path d="M12 8v4"></path>'
              '<path d="M12 16h.01"></path></svg>')

ICON_CHECK = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
              'stroke="var(--prizm-color-success)" style="flex:none">'
              '<circle cx="12" cy="12" r="10"></circle>'
              '<path d="m9 12 2 2 4-4"></path></svg>')


def esc(value: object) -> str:
    """Escape a backend-supplied value before it goes into markup."""
    return html.escape("" if value is None else str(value), quote=True)


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def format_updated(iso: str) -> str:
    """01 Sep 2026 09:22 — the register heading's timestamp format."""
    if not iso:
        return "never"
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return (f"{dt.day:02d} {months[dt.month - 1]} {dt.year} "
            f"{dt.hour:02d}:{dt.minute:02d}")


# --------------------------------------------------------------------------- #
# Chrome
# --------------------------------------------------------------------------- #

def header_left() -> str:
    return ('<div style="display:flex;align-items:center;gap:10px">'
            f'{ICON_LOGO}'
            '<span style="font-size:14px;font-weight:600;letter-spacing:-0.02em">'
            'My Assets Record</span></div>')


def header_right(name: str, role: str, initials: str) -> str:
    return (
        '<div style="display:flex;align-items:center;gap:12px">'
        '<button data-tip="Help centre — guides on asset classification, tagging '
        'and capitalisation." style="display:grid;place-items:center;border:0;'
        'padding:0;background:none;color:var(--prizm-color-fg-muted);cursor:pointer;'
        f'transition:color 150ms ease-out" class="mar-hover-fg">{ICON_HELP}</button>'
        '<div style="display:flex;align-items:center;gap:8px;padding-left:12px;'
        'border-left:1px solid var(--prizm-color-border)">'
        '<div style="width:28px;height:28px;border-radius:99px;'
        'background:var(--prizm-color-bg-muted);color:var(--prizm-color-fg-muted);'
        'display:grid;place-items:center;font-size:11px;font-weight:600">'
        f'{esc(initials)}</div>'
        '<div style="display:flex;flex-direction:column;line-height:1.25">'
        f'<span style="font-size:12px;font-weight:600">{esc(name)}</span>'
        '<span style="font-size:11px;color:var(--prizm-color-fg-subtle)">'
        f'{esc(role)}</span></div></div></div>'
    )


def toast(title: str, body: str) -> str:
    """The design's toast, minus its dismiss button (a real widget overlays it)."""
    return (
        '<div style="display:flex;align-items:center;gap:12px;padding:12px 14px;'
        'border:1px solid var(--prizm-color-border-strong);border-radius:8px;'
        'background:var(--prizm-color-fg);color:var(--prizm-color-bg);'
        f'box-shadow:var(--prizm-shadow-lg)">{ICON_CHECK}'
        '<div style="display:flex;flex-direction:column;gap:1px">'
        f'<span style="font-size:13px;font-weight:600">{esc(title)}</span>'
        f'<span style="font-size:12px;opacity:0.75">{esc(body)}</span></div>'
        '<span style="width:24px;height:24px;margin-left:6px"></span></div>'
    )
