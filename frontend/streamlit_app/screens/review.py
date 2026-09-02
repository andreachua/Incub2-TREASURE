"""Review screen — the design's `onForm` block, driven by GET /api/review/…

The registration card itself lives in ``screens/form.py`` and the field
definitions in ``screens/fields.py``, shared with the add-asset screen so the
two forms cannot drift. What stays here is this screen's own chrome: the
breadcrumb, the title block, the "pre-filled" banner, the provenance legend and
the source-document rail — which lists what the pipeline and the crawler found,
and is read-only: documents come in with the invoice, not from here.
"""

from __future__ import annotations

import streamlit as st

from screens import form
from screens.fields import ALL_FIELDS, PROV_CHIP
from screens.form import _DIVIDER, _H2, _SECTION
from ui import ICON_ALERT, ICON_FILE, esc, plural, sparkle

# --------------------------------------------------------------------------- #
# Rail
# --------------------------------------------------------------------------- #

def _doc_entry(title: str, meta: str) -> str:
    return ('<div style="display:flex;align-items:center;gap:9px;padding:9px 10px;'
            'border:1px solid var(--prizm-color-border);border-radius:6px" '
            f'class="mar-hover-muted">{ICON_FILE}'
            '<div style="display:flex;flex-direction:column;min-width:0">'
            f'<span style="font-size:12px;font-weight:500">{esc(title)}</span>'
            '<span style="font-size:11px;color:var(--prizm-color-fg-subtle);'
            f'font-family:var(--prizm-font-mono)">{esc(meta)}</span></div></div>')


_EYEBROW = ("font-size:11px;font-weight:600;letter-spacing:0.09em;"
            "text-transform:uppercase;color:var(--prizm-color-fg-subtle)")
_RAIL_CARD = ("display:flex;flex-direction:column;gap:12px;padding:16px;"
              "border:1px solid var(--prizm-color-border);border-radius:8px;"
              "background:var(--prizm-color-surface);box-shadow:var(--prizm-shadow-sm)")


def _status_card(counts: dict) -> str:
    rows = (("Extracted", counts.get("ai", 0), "var(--prizm-color-fg)"),
            ("System default", counts.get("system", 0), "var(--prizm-color-fg)"),
            ("Manual entry", counts.get("manual", 0), "var(--prizm-color-warning)"))
    body = "".join(
        '<div style="display:flex;justify-content:space-between;gap:8px">'
        f'<span>{label}</span>'
        '<span style="font-family:var(--prizm-font-mono);'
        f'color:{color};font-variant-numeric:tabular-nums">'
        f'{plural(n, "field")}</span></div>'
        for label, n, color in rows
    )
    return ('<div style="display:flex;flex-direction:column;gap:9px;padding:16px;'
            'border:1px solid var(--prizm-color-border);border-radius:8px;'
            'background:var(--prizm-color-surface);'
            'box-shadow:var(--prizm-shadow-sm)">'
            f'<span style="{_EYEBROW}">Review status</span>'
            '<div style="display:flex;flex-direction:column;gap:7px;font-size:12px;'
            f'color:var(--prizm-color-fg-muted)">{body}</div></div>')


def rail(review: dict, open_doc) -> None:
    docs = review.get("source_documents") or []
    with st.container(key="review_rail"):
        with st.container(key="rail_docs"):
            st.markdown(f'<span style="{_EYEBROW}">Source documents</span>',
                        unsafe_allow_html=True)
            with st.container(key="rail_doc_list"):
                if not docs:
                    st.markdown(
                        _doc_entry("No source documents",
                                   "Upload an invoice to populate this record."),
                        unsafe_allow_html=True)
                for i, doc in enumerate(docs):
                    with st.container(key=f"doc_{i}"):
                        st.markdown(_doc_entry(doc.get("title", ""),
                                               doc.get("meta", "")),
                                    unsafe_allow_html=True)
                        if doc.get("url"):
                            if st.button(doc.get("title", "Open"), key=f"doc_btn_{i}"):
                                open_doc(doc)
        st.markdown(_status_card(review.get("counts") or {}), unsafe_allow_html=True)


def record_pager(review: dict, go_record) -> None:
    """"Line item 3 of 7", with the two controls to move between them.

    An invoice decomposes into one record per line item, and only the record on
    screen is registered when the review completes — so without this the other
    six are extracted into `mar` and then unreachable, with no way to correct or
    register them. Hidden for a single-record job, where it would be noise.
    """
    count = int(review.get("record_count") or 1)
    if count <= 1:
        return
    index = int(review.get("record_index") or 0)

    with st.container(key="record_pager"):
        st.markdown(
            '<span style="font-size:12px;font-family:var(--prizm-font-mono);'
            'color:var(--prizm-color-fg-subtle);'
            f'font-variant-numeric:tabular-nums">Line item {index + 1} of {count}'
            '</span>',
            unsafe_allow_html=True)
        with st.container(key="record_pager_nav"):
            if st.button("Previous", key="record_prev", disabled=index == 0):
                go_record(index - 1)
            if st.button("Next", key="record_next", disabled=index >= count - 1):
                go_record(index + 1)


# --------------------------------------------------------------------------- #
# Screen
# --------------------------------------------------------------------------- #

_LEGEND = (
    '<div style="display:flex;flex-wrap:wrap;gap:20px;padding:11px 14px;'
    'border:1px solid var(--prizm-color-border);border-radius:6px;'
    'background:var(--prizm-color-surface)">'
    '<div style="display:flex;align-items:center;gap:7px;font-size:12px;'
    'color:var(--prizm-color-fg-muted)">'
    '<span style="width:22px;height:14px;border-radius:3px;border:1px solid '
    'color-mix(in oklab, var(--prizm-color-accent) 35%, var(--prizm-color-border));'
    'background:color-mix(in oklab, var(--prizm-color-accent) 7%, '
    'var(--prizm-color-surface))"></span>AI suggestion — check it</div>'
    '<div style="display:flex;align-items:center;gap:7px;font-size:12px;'
    'color:var(--prizm-color-fg-muted)">'
    '<span style="width:22px;height:14px;border-radius:3px;border:1px solid '
    'var(--prizm-color-border);background:var(--prizm-color-surface)"></span>'
    'Extracted from documents — editable</div>'
    '<div style="display:flex;align-items:center;gap:7px;font-size:12px;'
    'color:var(--prizm-color-fg-muted)">'
    '<span style="width:22px;height:14px;border-radius:3px;border:1px dashed '
    'var(--prizm-color-border-strong);background:var(--prizm-color-bg-muted)">'
    '</span>System default — confirm the custodian</div></div>'
)

_ERR_BANNER = (
    '<div style="display:flex;align-items:center;gap:9px;margin:0 22px 4px;'
    'padding:11px 13px;border:1px solid var(--prizm-color-danger);'
    'border-radius:6px;background:color-mix(in oklab, var(--prizm-color-danger) '
    f'8%, var(--prizm-color-surface))">{ICON_ALERT}'
    '<span style="font-size:12.5px;color:var(--prizm-color-danger)">'
    'CAT B or C or Dev? must be selected before the review can be completed.'
    '</span></div>'
)


def _breadcrumb() -> str:
    return ('<div style="display:flex;align-items:center;gap:8px">'
            '<span>/</span><span>Action items</span><span>/</span>'
            '<span style="color:var(--prizm-color-fg)">Review asset record</span>'
            '</div>')


def _title_block(name: str, extracted_at: str) -> str:
    return (
        '<div style="display:flex;align-items:flex-end;justify-content:'
        'space-between;gap:24px;flex-wrap:wrap">'
        '<div style="display:flex;flex-direction:column;gap:6px">'
        f'<span style="{_EYEBROW}">Asset registration</span>'
        '<h1 style="margin:0;font-size:26px;font-weight:600;'
        f'letter-spacing:-0.025em">{esc(name)}</h1></div>'
        '<div style="display:flex;align-items:center;gap:8px">'
        '<span style="display:inline-flex;align-items:center;gap:6px;'
        'padding:4px 9px;border-radius:99px;background:color-mix(in oklab, '
        'var(--prizm-color-warning) 13%, var(--prizm-color-surface));'
        'font-size:11.5px;font-weight:500;color:var(--prizm-color-warning)">'
        'Pending review</span>'
        '<span style="font-size:11.5px;font-family:var(--prizm-font-mono);'
        'color:var(--prizm-color-fg-subtle);font-variant-numeric:tabular-nums">'
        f'Extracted {esc(extracted_at)}</span></div></div>'
    )


def _ai_banner(prefilled: int, total: int) -> str:
    return (
        '<div style="display:flex;align-items:flex-start;gap:12px;'
        'padding:14px 16px;border:1px solid color-mix(in oklab, '
        'var(--prizm-color-accent) 25%, var(--prizm-color-border));'
        'border-radius:8px;background:color-mix(in oklab, '
        'var(--prizm-color-accent) 5%, var(--prizm-color-surface))">'
        f'{sparkle(16, "flex:none;margin-top:1px")}'
        '<div style="display:flex;flex-direction:column;gap:3px">'
        '<span style="font-size:13px;font-weight:600;letter-spacing:-0.01em">'
        f'{prefilled} of {total} fields pre-filled from purchase documents</span>'
        '<span style="font-size:12.5px;color:var(--prizm-color-fg-muted);'
        'text-wrap:pretty">Values are read from the invoice, the GeBIZ purchase '
        'order and the scope of work.</span></div></div>'
    )


def render(review: dict, categories: list[dict], extracted_at: str, err: bool,
           go, submit, open_doc, go_record) -> None:
    fields = review.get("fields") or {}
    counts = review.get("counts") or {}
    prefilled = counts.get("ai", 0) + counts.get("system", 0)
    explanation = review.get("explanation")

    with st.container(key="main_form"):
        with st.container(key="review_wrap"):
            with st.container(key="review_crumbs"):
                if st.button("Home", key="crumb_home"):
                    go("home")
                st.markdown(_breadcrumb(), unsafe_allow_html=True)

            name = (fields.get("name") or {}).get("value", "")
            st.markdown(_title_block(name, extracted_at), unsafe_allow_html=True)
            record_pager(review, go_record)
            st.markdown(_ai_banner(prefilled, len(fields) or 23),
                        unsafe_allow_html=True)
            st.markdown(_LEGEND, unsafe_allow_html=True)

            with st.container(key="review_body"):
                def _footer() -> None:
                    with st.container(key="review_footer"):
                        st.markdown(
                            '<span style="font-size:12px;'
                            'color:var(--prizm-color-fg-muted)">Asset record will '
                            'be updated upon review completion.</span>',
                            unsafe_allow_html=True)
                        with st.container(key="review_actions"):
                            if st.button("Save draft", key="save_draft"):
                                go("home")
                            if st.button("Complete review", key="complete_review"):
                                submit()

                form.form_card(
                    fields, categories,
                    footer=_footer,
                    explanation=explanation,
                    err=err,
                    err_banner=_ERR_BANNER,
                )

                rail(review, open_doc)


