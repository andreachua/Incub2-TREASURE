"""Assets screen — the design's `onAssets` block, driven by GET /api/assets.

The table is the design's markup with rows built from the register. Component
sub-rows collapse and expand exactly as they do in the design (chevron rotates,
rows show and hide), but with no JavaScript: a visually hidden checkbox sits
next to the table and a `#id:checked ~ table …` rule drives both. The rules are
generated per render because they are keyed on each parent row's id.
"""

from __future__ import annotations

import streamlit as st

from ui import (
    ICON_CARET_DOWN,
    ICON_CHEVRON_RIGHT_13,
    ICON_FILTER,
    ICON_PLUS,
    esc,
    format_updated,
)

_BORDER = "border-bottom:1px solid var(--prizm-color-border)"
_MONO = "font-family:var(--prizm-font-mono)"
_NUM = f"text-align:right;{_MONO};font-variant-numeric:tabular-nums"

_HEAD_CELL = ("text-align:left;padding:10px 12px;font-size:11px;font-weight:600;"
              "letter-spacing:0.05em;text-transform:uppercase;"
              f"color:var(--prizm-color-fg-subtle);{_BORDER}")

_COLUMNS = ("Asset", "Tag no.", "Category", "Serial no.", "Custodian",
            "Unit price", "Status")

_NEW_BADGE = ('<span style="padding:2px 7px;border-radius:99px;'
              'background:color-mix(in oklab, var(--prizm-color-accent) 13%, '
              'var(--prizm-color-surface));font-size:10.5px;font-weight:600;'
              'color:var(--prizm-color-accent)">NEW</span>')

# The design paints "Registered" green, "Calibration due" amber; anything else
# (e.g. "Pending review") keeps the muted treatment.
_PILL_TONE = {
    "Registered": "success",
    "Linked": "",
    "Calibration due": "warning",
    "Pending review": "warning",
}


def _pill(status: str) -> str:
    tone = _PILL_TONE.get(status, "success" if status else "")
    if not tone:
        return ('<span style="font-size:11px;color:var(--prizm-color-fg-subtle)">'
                f'{esc(status or "—")}</span>')
    return ('<span style="display:inline-flex;align-items:center;gap:5px;'
            'padding:2px 8px;border-radius:99px;background:color-mix(in oklab, '
            f'var(--prizm-color-{tone}) 14%, var(--prizm-color-surface));'
            f'font-size:11px;font-weight:500;color:var(--prizm-color-{tone})">'
            f'{esc(status)}</span>')


def _head() -> str:
    cells = "".join(
        f'<th style="{_HEAD_CELL}'
        f'{";text-align:right" if c == "Unit price" else ""}">{c}</th>'
        for c in _COLUMNS
    )
    return ('<thead><tr style="background:var(--prizm-color-bg-subtle)">'
            f'<th style="width:34px;{_BORDER}"></th>{cells}</tr></thead>')


def _parent_row(asset: dict, is_new: bool, expandable: bool) -> str:
    no = asset["no"]
    tint = ('background:color-mix(in oklab, var(--prizm-color-accent) 5%, '
            'var(--prizm-color-surface))') if is_new else ""
    chevron = (f'<label for="exp-{no}" class="mar-chev">{ICON_CHEVRON_RIGHT_13}</label>'
               if expandable else "")
    name = (f'<div style="display:flex;align-items:center;gap:8px">'
            f'<span style="font-weight:600">{esc(asset["name"])}</span>{_NEW_BADGE}'
            f'</div>') if is_new else esc(asset["name"])
    weight = "" if is_new else ";font-weight:500"
    return (
        f'<tr style="{tint}">'
        f'<td style="padding:0 0 0 8px;{_BORDER}">{chevron}</td>'
        f'<td style="padding:11px 12px;{_BORDER}{weight}">{name}</td>'
        f'<td style="padding:11px 12px;{_BORDER};{_MONO};'
        f'color:var(--prizm-color-fg-subtle)">{esc(asset["tag_no"] or "—")}</td>'
        f'<td style="padding:11px 12px;{_BORDER}">{esc(asset["category"] or "—")}</td>'
        f'<td style="padding:11px 12px;{_BORDER};{_MONO}">'
        f'{esc(asset["serial_no"] or "—")}</td>'
        f'<td style="padding:11px 12px;{_BORDER}">{esc(asset["custodian"] or "—")}</td>'
        f'<td style="padding:11px 12px;{_BORDER};{_NUM}">'
        f'{esc(asset["unit_price"] or "—")}</td>'
        f'<td style="padding:11px 12px;{_BORDER}">{_pill(asset["status"])}</td></tr>'
    )


def _child_row(parent_no: int, child: dict) -> str:
    muted = "color:var(--prizm-color-fg-muted)"
    return (
        f'<tr class="mar-child mar-child-{parent_no}">'
        f'<td style="{_BORDER}"></td>'
        f'<td style="padding:9px 12px 9px 26px;{_BORDER};{muted}">'
        f'{esc(child["name"])}</td>'
        f'<td style="padding:9px 12px;{_BORDER};{_MONO};'
        f'color:var(--prizm-color-fg-subtle)">{esc(child["tag_no"] or "—")}</td>'
        f'<td style="padding:9px 12px;{_BORDER};{muted}">'
        f'{esc(child["category"] or "Component")}</td>'
        f'<td style="padding:9px 12px;{_BORDER};{_MONO};{muted}">'
        f'{esc(child["serial_no"] or "—")}</td>'
        f'<td style="padding:9px 12px;{_BORDER};{muted}">'
        f'{esc(child["custodian"] or "—")}</td>'
        f'<td style="padding:9px 12px;{_BORDER};{_NUM};{muted}">'
        f'{esc(child["unit_price"] or "—")}</td>'
        f'<td style="padding:9px 12px;{_BORDER}">{_pill(child["status"])}</td></tr>'
    )


def _empty_row() -> str:
    return ('<tr><td colspan="8" style="padding:28px 12px;text-align:center;'
            'font-size:13px;color:var(--prizm-color-fg-subtle)">'
            'No assets registered yet.</td></tr>')


def _table(assets: list[dict], new_no: int | None) -> str:
    checkboxes, rules, rows = [], [], []
    for asset in assets:
        no = asset["no"]
        children = asset.get("components") or []
        if children:
            # Newly registered records open expanded, as they do in the design.
            checked = " checked" if no == new_no else ""
            checkboxes.append(
                f'<input type="checkbox" id="exp-{no}" class="mar-exp"{checked}>')
            rules.append(
                f'#exp-{no}:checked ~ table .mar-child-{no}{{display:table-row}}'
                f'#exp-{no}:checked ~ table label[for="exp-{no}"]'
                f'{{transform:rotate(90deg)}}'
            )
        rows.append(_parent_row(asset, no == new_no, bool(children)))
        rows.extend(_child_row(no, c) for c in children)

    body = "".join(rows) or _empty_row()
    return (
        f'<style>{"".join(rules)}</style>'
        '<div style="border:1px solid var(--prizm-color-border);border-radius:8px;'
        'background:var(--prizm-color-surface);box-shadow:var(--prizm-shadow-sm);'
        'overflow:hidden">'
        f'{"".join(checkboxes)}'
        '<table style="width:100%;border-collapse:collapse;font-size:13px">'
        f'{_head()}<tbody>{body}</tbody></table></div>'
    )


# The toolbar's three buttons. Filter and Manage stay inert (out of scope); Add
# is split out so a transparent Streamlit button can be laid over it — the same
# trick home.py uses for its action card.
_TOOLBAR_INERT = (
    '<div style="display:flex;align-items:center;gap:10px">'
    '<button style="height:36px;padding:0 12px;display:inline-flex;'
    'align-items:center;gap:7px;border:1px solid var(--prizm-color-border);'
    'border-radius:6px;background:var(--prizm-color-surface);font-size:13px;'
    'font-weight:500;color:var(--prizm-color-fg);cursor:pointer" '
    f'class="mar-hover-muted">{ICON_FILTER}Filter</button>'
    '<button style="height:36px;padding:0 12px;display:inline-flex;'
    'align-items:center;gap:7px;border:1px solid var(--prizm-color-border);'
    'border-radius:6px;background:var(--prizm-color-surface);font-size:13px;'
    'font-weight:500;color:var(--prizm-color-fg);cursor:pointer" '
    f'class="mar-hover-muted">Manage{ICON_CARET_DOWN}</button>'
    '</div>'
)

_ADD_BUTTON = (
    '<button style="height:36px;padding:0 14px;display:inline-flex;'
    'align-items:center;gap:7px;border:0;border-radius:6px;'
    'background:var(--prizm-color-accent);font-size:13px;font-weight:600;'
    'color:var(--prizm-color-accent-fg);cursor:pointer;width:100%" '
    f'class="mar-hover-accent">{ICON_PLUS}Add</button>'
)


def render(page: dict, new_no: int | None, on_search, on_add) -> None:
    total = page.get("total", 0)
    heading = (f'{total:,} record' + ("" if total == 1 else "s")
               + f' · updated {format_updated(page.get("updated_at", ""))}')

    with st.container(key="main_assets"):
        with st.container(key="assets_wrap"):
            st.markdown(
                '<div style="display:flex;align-items:flex-end;'
                'justify-content:space-between;gap:20px;flex-wrap:wrap">'
                '<div style="display:flex;flex-direction:column;gap:6px">'
                '<h1 style="margin:0;font-size:28px;font-weight:600;'
                'letter-spacing:-0.025em">Assets</h1></div>'
                '<span style="font-size:12px;font-family:var(--prizm-font-mono);'
                'color:var(--prizm-color-fg-subtle);font-variant-numeric:'
                f'tabular-nums">{esc(heading)}</span></div>',
                unsafe_allow_html=True,
            )

            with st.container(key="assets_toolbar"):
                st.text_input(
                    "Search",
                    key="assets_search",
                    placeholder="Search by tag number, serial, model or vendor",
                    label_visibility="collapsed",
                    on_change=on_search,
                )
                st.markdown(_TOOLBAR_INERT, unsafe_allow_html=True)
                with st.container(key="assets_add"):
                    st.markdown(_ADD_BUTTON, unsafe_allow_html=True)
                    if st.button("Add", key="add_btn"):
                        on_add()

            st.markdown(_table(page.get("assets", []), new_no),
                        unsafe_allow_html=True)
