"""The source-document viewer — a modal opened from the review screen's rail.

Nothing in the design opens a document, so this is the one screen with no
markup to copy. It is built from the same PRIZM tokens the rest of the app uses
so it reads as part of the design: a surface panel, the design's borders and
shadow, mono metadata.

An invoice is served as bytes from GET /api/documents/receipt/{id} and inlined
as a data: URI; a PDF is rendered page by page through the same route's
`?as=png`. A purchase order or scope of work comes back as markdown text.
"""

from __future__ import annotations

import base64

import streamlit as st

import api
from ui import esc

_PANEL = ("border:1px solid var(--prizm-color-border);border-radius:8px;"
          "background:var(--prizm-color-surface);box-shadow:var(--prizm-shadow-sm)")

ZOOM_LEVELS = ("Fit width", "100%", "150%", "200%")
_ZOOM_WIDTH = {"Fit width": "100%", "100%": "auto", "150%": "150%", "200%": "200%"}


def _header(doc: dict) -> None:
    st.markdown(
        '<div style="display:flex;align-items:flex-end;justify-content:'
        'space-between;gap:16px;flex-wrap:wrap;margin-bottom:14px">'
        '<div style="display:flex;flex-direction:column;gap:4px">'
        '<span style="font-size:11px;font-weight:600;letter-spacing:0.09em;'
        'text-transform:uppercase;color:var(--prizm-color-fg-subtle)">'
        'Source document</span>'
        '<span style="font-size:16px;font-weight:600;letter-spacing:-0.015em">'
        f'{esc(doc.get("title", ""))}</span></div>'
        '<span style="font-size:11.5px;font-family:var(--prizm-font-mono);'
        'color:var(--prizm-color-fg-subtle)">'
        f'{esc(doc.get("meta", ""))}</span></div>',
        unsafe_allow_html=True,
    )


def _image_panel(data: bytes, content_type: str, zoom: str) -> None:
    b64 = base64.b64encode(data).decode("ascii")
    width = _ZOOM_WIDTH.get(zoom, "100%")
    st.markdown(
        f'<div style="{_PANEL};max-height:66vh;overflow:auto;padding:10px">'
        f'<img src="data:{content_type};base64,{b64}" '
        f'style="display:block;width:{width};max-width:none;border-radius:4px">'
        '</div>',
        unsafe_allow_html=True,
    )


def _render_invoice(doc: dict) -> None:
    url = doc["url"]
    try:
        info = api.get_document_info(url)
    except api.ApiError as exc:
        st.markdown(_error(str(exc)), unsafe_allow_html=True)
        return

    pages = max(1, int(info.get("pages", 1)))
    with st.container(key="doc_controls"):
        zoom = st.selectbox("Zoom", ZOOM_LEVELS, key="doc_zoom",
                            label_visibility="collapsed")
        if pages > 1:
            page = st.number_input("Page", min_value=1, max_value=pages, step=1,
                                   key="doc_page", label_visibility="collapsed")
        else:
            page = 1
        st.markdown(
            '<span style="font-size:11.5px;font-family:var(--prizm-font-mono);'
            'color:var(--prizm-color-fg-subtle);white-space:nowrap">'
            f'{esc(info.get("filename", ""))} · '
            f'{"1 page" if pages == 1 else f"{pages} pages"}</span>',
            unsafe_allow_html=True,
        )

    fetch = f"{url}?as=png&page={int(page)}" if info.get("is_pdf") else url
    try:
        data, content_type = api.get_document_bytes(fetch)
    except api.ApiError as exc:
        st.markdown(_error(str(exc)), unsafe_allow_html=True)
        return
    _image_panel(data, content_type, zoom)


def _render_text(doc: dict) -> None:
    try:
        documents = api.get_document_text(doc["url"])
    except api.ApiError as exc:
        st.markdown(_error(str(exc)), unsafe_allow_html=True)
        return
    if not documents:
        st.markdown(_error("This document is not held in the register."),
                    unsafe_allow_html=True)
        return
    for entry in documents:
        st.markdown(
            '<span style="font-size:11.5px;font-family:var(--prizm-font-mono);'
            'color:var(--prizm-color-fg-subtle)">'
            f'{esc(entry.get("filename", ""))}</span>',
            unsafe_allow_html=True,
        )
        with st.container(key="doc_body"):
            st.markdown(entry.get("content", ""))


def _error(message: str) -> str:
    return ('<div style="display:flex;align-items:center;gap:9px;padding:11px 13px;'
            'border:1px solid var(--prizm-color-danger);border-radius:6px;'
            'background:color-mix(in oklab, var(--prizm-color-danger) 8%, '
            'var(--prizm-color-surface));font-size:12.5px;'
            f'color:var(--prizm-color-danger)">{esc(message)}</div>')


@st.dialog("Source document", width="large")
def show(doc: dict, on_close) -> None:
    _header(doc)
    if doc.get("kind") in ("po", "sow"):
        _render_text(doc)
    else:
        _render_invoice(doc)
    with st.container(key="doc_close"):
        if st.button("Close", key="doc_close_btn"):
            on_close()
