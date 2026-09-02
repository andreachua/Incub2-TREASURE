"""Home screen — the design's `onHome` block, driven by GET /api/summary.

Layout is the design's: a title block, an "Action items" section holding one
card per record awaiting review, and a three-up grid of category tiles. The
card and the tiles are buttons in the design, so each is an invisible Streamlit
button laid over the card's own markup (see .mar-overlay in styles/app.css).
"""

from __future__ import annotations

import streamlit as st

from screens.progress import stage_card
from screens.upload import invoice_uploader
from ui import (
    ICON_CHEVRON_RIGHT_15,
    ICON_FOLDER,
    ICON_LIST,
    esc,
    plural,
    sparkle,
)

_TITLE = (
    '<div style="display:flex;flex-direction:column;gap:6px">'
    '<h1 style="margin:0;font-size:28px;font-weight:600;letter-spacing:-0.025em">'
    'Home</h1>'
    '<p style="margin:2px 0 0;font-size:14px;color:var(--prizm-color-fg-muted);'
    'max-width:60ch;text-wrap:pretty">Track resources, equipment, and assets on '
    'My Assets Record (MAR)</p></div>'
)


def _action_heading(pending: int) -> str:
    return (
        '<div style="display:flex;align-items:baseline;gap:10px">'
        '<h2 style="margin:0;font-size:16px;font-weight:600;'
        'letter-spacing:-0.015em">Action items</h2>'
        '<span style="font-size:12px;font-family:var(--prizm-font-mono);'
        'color:var(--prizm-color-fg-subtle);font-variant-numeric:tabular-nums">'
        f'{pending} pending</span></div>'
    )


def _action_card(subtitle: str) -> str:
    return (
        '<div style="display:flex;align-items:center;gap:18px;width:100%;'
        'text-align:left;padding:16px 18px;border:1px solid '
        'var(--prizm-color-border);border-radius:8px;'
        'background:var(--prizm-color-surface);box-shadow:var(--prizm-shadow-sm);'
        'cursor:pointer;transition:background 150ms ease-out" class="mar-hover-muted">'
        '<div style="width:34px;height:34px;flex:none;border-radius:6px;'
        'background:color-mix(in oklab, var(--prizm-color-accent) 10%, '
        'var(--prizm-color-surface));color:var(--prizm-color-accent);'
        f'display:grid;place-items:center">{ICON_LIST}</div>'
        '<div style="flex:1;display:flex;flex-direction:column;gap:4px;min-width:0">'
        '<span style="display:inline-flex;align-items:center;gap:6px;font-size:14px;'
        'font-weight:600;letter-spacing:-0.01em">Review asset record'
        f'{sparkle(14)}</span>'
        '<span style="font-size:12.5px;color:var(--prizm-color-fg-muted)">'
        f'{esc(subtitle)}</span></div>'
        '<div style="display:flex;align-items:center;gap:14px;flex:none">'
        f'{ICON_CHEVRON_RIGHT_15}</div></div>'
    )


def _empty_card() -> str:
    return (
        '<div style="display:flex;align-items:center;gap:18px;width:100%;'
        'text-align:left;padding:16px 18px;border:1px solid '
        'var(--prizm-color-border);border-radius:8px;'
        'background:var(--prizm-color-surface);box-shadow:var(--prizm-shadow-sm)">'
        '<div style="width:34px;height:34px;flex:none;border-radius:6px;'
        'background:var(--prizm-color-bg-muted);color:var(--prizm-color-fg-subtle);'
        f'display:grid;place-items:center">{ICON_LIST}</div>'
        '<div style="flex:1;display:flex;flex-direction:column;gap:4px;min-width:0">'
        '<span style="font-size:14px;font-weight:600;letter-spacing:-0.01em">'
        'No record awaiting review</span>'
        '<span style="font-size:12.5px;color:var(--prizm-color-fg-muted)">'
        'Upload an invoice to extract its line items and start a review.</span>'
        '</div></div>'
    )


def _tile(label: str, caption: str, count: int) -> str:
    return (
        '<div style="display:flex;flex-direction:column;gap:14px;text-align:left;'
        'padding:16px;border:1px solid var(--prizm-color-border);border-radius:8px;'
        'background:var(--prizm-color-surface);box-shadow:var(--prizm-shadow-sm);'
        'cursor:pointer;transition:background 150ms ease-out" class="mar-hover-muted">'
        f'{ICON_FOLDER}'
        '<div style="display:flex;flex-direction:column;gap:3px">'
        f'<span style="font-size:14px;font-weight:600">{esc(label)}</span>'
        '<span style="font-size:12px;color:var(--prizm-color-fg-muted)">'
        f'{esc(caption)}</span>'
        '<span style="font-size:12px;font-family:var(--prizm-font-mono);'
        'color:var(--prizm-color-fg-subtle);font-variant-numeric:tabular-nums;'
        f'margin-top:4px">{plural(count, "asset")}</span></div></div>'
    )


def action_subtitle(review: dict | None) -> str:
    """"<name> · Invoice <no> · PO <no>" — describing the record actually waiting."""
    if not review:
        return "Awaiting review"
    fields = review.get("fields", {})

    def value(key: str) -> str:
        return (fields.get(key) or {}).get("value", "")

    parts = [value("name")]
    if value("invoice_no"):
        parts.append(f"Invoice {value('invoice_no')}")
    if value("po_no"):
        parts.append(f"PO {value('po_no')}")
    return " · ".join(p for p in parts if p) or "Awaiting review"


def render(summary: dict, review: dict | None, go, on_upload,
           pending: bool, stage: str) -> None:
    with st.container(key="main_home"):
        with st.container(key="home_wrap"):
            st.markdown(_TITLE, unsafe_allow_html=True)

            with st.container(key="home_actions"):
                st.markdown(_action_heading(summary.get("pending", 0)),
                            unsafe_allow_html=True)
                if review:
                    with st.container(key="home_action_card"):
                        st.markdown(_action_card(action_subtitle(review)),
                                    unsafe_allow_html=True)
                        if st.button("Review asset record", key="go_form"):
                            go("form")
                else:
                    st.markdown(_empty_card(), unsafe_allow_html=True)

                # The design only ever shows this section with a record waiting,
                # and put its upload control on the review screen's rail —
                # unreachable from an empty register, and since removed from the
                # rail, which only lists documents now. So this is the way in
                # from cold. It stays visible with a record waiting too:
                # /api/review/latest falls back to the most recent job when
                # nothing is pending, so a review is almost always present and
                # hiding the uploader behind that left Home with no way to
                # upload at all.
                invoice_uploader(on_upload)
                if pending:
                    st.markdown(stage_card(stage), unsafe_allow_html=True)

            with st.container(key="home_cats"):
                st.markdown(
                    '<h2 style="margin:0;font-size:16px;font-weight:600;'
                    'letter-spacing:-0.015em">Asset categories</h2>',
                    unsafe_allow_html=True,
                )
                with st.container(key="home_tiles"):
                    for tile in summary.get("tiles", []):
                        key = tile.get("key", "")
                        with st.container(key=f"tile_{key}"):
                            st.markdown(
                                _tile(tile.get("label", ""), tile.get("caption", ""),
                                      tile.get("count", 0)),
                                unsafe_allow_html=True,
                            )
                            if st.button(tile.get("label", ""), key=f"tile_btn_{key}"):
                                go("assets")
