"""The invoice uploader — one control, two places.

Home's empty state and the add screen's upload panel both mount the same file
uploader, and only ever one of them at a time — they are different screens. So
they share a single widget key, which is what keeps the upload callback from
reading a widget that is not on screen. (The review screen's rail used to mount
a third copy; it no longer takes documents, so the invoice is the only way in.)

The key is versioned by ``upload_nonce``: Streamlit fires ``on_change`` only
when the widget's value actually changes, so picking the same file again after
a failure would do nothing. Bumping the nonce mints a fresh, empty widget
instead, which is the supported way to reset a file uploader.
"""

from __future__ import annotations

import streamlit as st

# What the pipeline can actually read: pdf2image handles PDFs, the VLM the rest.
UPLOAD_TYPES = ["png", "jpg", "jpeg", "webp", "gif", "bmp", "tif", "tiff", "pdf"]


def uploader_key() -> str:
    """The invoice uploader's widget key for this run. See the module docstring."""
    return f"invoice_uploader_{st.session_state.get('upload_nonce', 0)}"


def invoice_uploader(on_upload, label: str = "") -> None:
    """Mount the uploader inside the design's dashed drop affordance.

    Pass ``label`` on surfaces that caption the control themselves (the add
    screen's panel). Everywhere else the label is a blank placeholder, hidden by
    ``.st-key-rail_upload label:has(p:empty)`` in styles/app.css — what an
    upload in flight is doing is reported by the progress checklist instead
    (screens/progress.py), which has room to say something useful.
    """
    with st.container(key="rail_upload"):
        st.file_uploader(
            label or " ",
            key=uploader_key(),
            type=UPLOAD_TYPES,
            accept_multiple_files=False,
            label_visibility="collapsed" if label else "visible",
            on_change=on_upload,
        )
