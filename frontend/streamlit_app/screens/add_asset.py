"""Add asset — the same registration card, reached two ways.

Either the operator types the record in, or they drop in an invoice and the
pipeline fills the form for them (and the crawler finds the purchase order and
scope of work, which then appear in the rail).

Both modes render ``form.form_card`` with the same container keys the review
screen uses, so every rule in styles/app.css already applies. Only this screen's
own chrome — the breadcrumb, the mode switch and the upload panel — is new.
"""

from __future__ import annotations

import streamlit as st

from screens import form, review
from screens.progress import stage_card
from screens.upload import invoice_uploader
from ui import esc, sparkle

_H1 = "margin:0;font-size:28px;font-weight:600;letter-spacing:-0.025em"
_EYEBROW = ("font-size:12px;font-weight:600;letter-spacing:0.04em;"
            "text-transform:uppercase;color:var(--prizm-color-fg-subtle)")


def _breadcrumb() -> str:
    sep = ('<span style="color:var(--prizm-color-fg-subtle);font-size:12px">/</span>')
    return (
        f'{sep}<span style="font-size:12px;color:var(--prizm-color-fg-muted)">'
        f'Assets</span>{sep}'
        '<span style="font-size:12px;color:var(--prizm-color-fg)">Add asset</span>'
    )


def _title_block() -> str:
    return (
        '<div style="display:flex;flex-direction:column;gap:6px;margin:18px 0 4px">'
        f'<span style="{_EYEBROW}">Asset registration</span>'
        f'<h1 style="{_H1}">Add asset</h1></div>'
    )


def _mode_card(title: str, body: str, active: bool) -> str:
    """One of the two entry-mode cards. The active one takes the accent tint."""
    border = ("color-mix(in srgb, var(--prizm-color-accent) 35%, "
              "var(--prizm-color-border))" if active else "var(--prizm-color-border)")
    background = ("color-mix(in srgb, var(--prizm-color-accent) 5%, "
                  "var(--prizm-color-surface))" if active else "var(--prizm-color-surface)")
    return (
        f'<div style="border:1px solid {border};border-radius:8px;'
        f'background:{background};padding:16px 18px;display:flex;'
        'flex-direction:column;gap:5px;height:100%;cursor:pointer">'
        '<span style="font-size:13.5px;font-weight:600;letter-spacing:-0.01em">'
        f'{esc(title)}</span>'
        '<span style="font-size:12.5px;color:var(--prizm-color-fg-muted);'
        f'text-wrap:pretty">{esc(body)}</span></div>'
    )


def _upload_panel(pending: bool, stage: str, on_upload) -> None:
    """Where the invoice goes in, before a job exists to show."""
    with st.container(key="add_upload_panel"):
        st.markdown(
            '<div style="display:flex;flex-direction:column;gap:4px">'
            '<span style="font-size:13.5px;font-weight:600;display:flex;'
            f'align-items:center;gap:7px">{sparkle(14)}Upload an invoice</span>'
            '<span style="font-size:12.5px;color:var(--prizm-color-fg-muted);'
            'text-wrap:pretty">The line items are extracted, and the matching '
            'purchase order and scope of work are found for you. You review '
            'everything before it is registered.</span></div>',
            unsafe_allow_html=True,
        )
        invoice_uploader(on_upload, label="Upload invoice")
        if pending:
            st.markdown(stage_card(stage), unsafe_allow_html=True)


def _mode_switch(mode: str, on_mode) -> None:
    with st.container(key="add_modes"):
        with st.container(key="mode_manual"):
            st.markdown(
                _mode_card("Enter manually",
                           "Type the record in yourself. Every field starts empty.",
                           mode == "manual"),
                unsafe_allow_html=True)
            if st.button("Enter manually", key="mode_btn_manual"):
                on_mode("manual")
        with st.container(key="mode_upload"):
            st.markdown(
                _mode_card("Upload an invoice",
                           "Extract the details from a document, then review them.",
                           mode == "upload"),
                unsafe_allow_html=True)
            if st.button("Upload an invoice", key="mode_btn_upload"):
                on_mode("upload")


def render(payload: dict, categories: list[dict], *, mode: str, prefilled: bool,
           err: bool, go, on_mode, on_upload, pending: bool, stage: str, submit,
           open_doc, go_record) -> None:
    """``payload`` is the blank form, or a pipeline result once one is ready."""
    fields = payload.get("fields") or {}

    with st.container(key="main_add"):
        with st.container(key="add_wrap"):
            with st.container(key="add_crumbs"):
                if st.button("Home", key="crumb_add_home"):
                    go("home")
                st.markdown(_breadcrumb(), unsafe_allow_html=True)

            st.markdown(_title_block(), unsafe_allow_html=True)
            _mode_switch(mode, on_mode)

            if mode == "upload" and not prefilled:
                _upload_panel(pending, stage, on_upload)
                return

            if prefilled:
                review.record_pager(payload, go_record)
                counts = payload.get("counts") or {}
                st.markdown(
                    review._ai_banner(counts.get("ai", 0) + counts.get("system", 0),
                                      len(fields) or 23),
                    unsafe_allow_html=True)
            st.markdown(review._LEGEND, unsafe_allow_html=True)

            body_key = "add_body_upload" if prefilled else "add_body_manual"
            with st.container(key=body_key):
                def _footer() -> None:
                    with st.container(key="review_footer"):
                        st.markdown(
                            '<span style="font-size:12px;'
                            'color:var(--prizm-color-fg-muted)">The asset is '
                            'registered when you add it. Tag number is assigned '
                            'later.</span>',
                            unsafe_allow_html=True)
                        with st.container(key="review_actions"):
                            if st.button("Cancel", key="add_cancel"):
                                go("assets")
                            if st.button("Add asset", key="add_submit"):
                                submit()

                form.form_card(
                    fields, categories,
                    footer=_footer,
                    explanation=payload.get("explanation") if prefilled else None,
                    err=err,
                    err_banner=review._ERR_BANNER,
                )

                if prefilled:
                    review.rail(payload, open_doc)
