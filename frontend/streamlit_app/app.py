"""My Assets Record (MAR) — the operator front end.

A Streamlit port of the exported design bundle. Screens, layout, type, colour
and workflow are the design's; every value on screen comes from the pipeline's
API rather than the design's placeholders.

Screen state mirrors the design's component exactly: one `screen` string
switching between "home", "form" and "assets", `go()` scrolling to the top and
clearing the error, and `submit()` refusing to advance until the CAT B/C/Dev
call has been made.

Run:  streamlit run app.py          (API_BASE points at the FastAPI backend)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

import api  # noqa: E402
import viewer  # noqa: E402
from screens import assets as assets_screen  # noqa: E402
from screens import home as home_screen  # noqa: E402
from screens import add_asset as add_screen  # noqa: E402
from screens import review as review_screen  # noqa: E402
from screens import progress  # noqa: E402
from screens import upload as upload_control  # noqa: E402
from ui import AI_GRADIENT_DEFS, header_left, header_right, toast  # noqa: E402

# The design's header identity. It has no backing API — it is chrome, not data.
USER_NAME, USER_ROLE, USER_INITIALS = "Tan Li Wei", "Project manager", "TL"

TOAST_SECONDS = 7  # the design dismisses its toast after 7s

POLL_SECONDS = 2       # the design bridge's upload poll interval
POLL_TIMEOUT = 420.0   # a full invoice run is ~100s; leave real headroom

st.set_page_config(
    page_title="My Assets Record",
    page_icon="▲",  # the design's mark
    layout="wide",
    initial_sidebar_state="collapsed",
)


# --------------------------------------------------------------------------- #
# Styles
# --------------------------------------------------------------------------- #

def _styles() -> str:
    """Read on every run so an edit to the stylesheets shows up on refresh."""
    prizm = (APP_DIR / "styles" / "prizm.css").read_text(encoding="utf-8")
    app = (APP_DIR / "styles" / "app.css").read_text(encoding="utf-8")
    return f"<style>{prizm}\n{app}</style>"


# --------------------------------------------------------------------------- #
# State — the design's component state, in session_state
# --------------------------------------------------------------------------- #

_DEFAULTS = {
    "screen": "home",
    "add_mode": "manual",
    "err": False,
    "registered": False,
    "toast_title": "",
    "toast_body": "",
    "toast_at": 0.0,
    "new_asset_no": None,
    "search": "",
    "job_id": None,
    # Which line item of the current job is on screen. An invoice decomposes
    # into several, and only this one is registered when the review completes.
    "record_index": 0,
    "review_job": None,
    "pending_job": None,
    "pending_since": 0.0,
    # Which pipeline stage the pending job is at, as reported by the poll; ""
    # while it is still queued. screens/progress.py renders it as a checklist.
    "upload_stage": "",
    "upload_error": "",
    # Versions the uploader's widget key so a finished or failed upload leaves a
    # fresh, empty control behind — see screens/upload.py.
    "upload_nonce": 0,
    "submit_error": "",
    "open_doc": None,
}


def _init_state() -> None:
    for key, value in _DEFAULTS.items():
        st.session_state.setdefault(key, value)


def go(screen: str) -> None:
    st.session_state.screen = screen
    st.session_state.err = False
    st.rerun()


def _clear_field_state() -> None:
    """Drop the reviewer's in-progress values — a different record is loading."""
    for key in [k for k in st.session_state
                if k.startswith("v_") or k.startswith("seed_")]:
        del st.session_state[key]


def _field_values(review: dict) -> dict[str, str]:
    """Extracted values, overlaid with whatever the reviewer has on screen."""
    values = {k: (v or {}).get("value", "") for k, v in
              (review.get("fields") or {}).items()}
    for key in values:
        state_key = f"v_{key}"
        if state_key in st.session_state:
            values[key] = str(st.session_state[state_key] or "")
    return values


def _finish_registration(result: dict) -> None:
    """What both submit paths do once the row exists: toast, highlight, land on Assets."""
    st.session_state.submit_error = ""
    st.session_state.toast_title = "Asset record registered"
    st.session_state.toast_body = result.get("message", "")
    st.session_state.toast_at = time.time()
    st.session_state.new_asset_no = (result.get("asset") or {}).get("no")
    st.session_state.registered = True
    st.session_state.err = False
    st.session_state.screen = "assets"
    # This record is no longer awaiting review; go back to whatever is.
    st.session_state.job_id = None
    st.session_state.record_index = 0
    _clear_field_state()
    api.clear_caches()
    st.rerun()


def submit(review: dict) -> None:
    values = _field_values(review)
    if not (values.get("mindef_cat") or "").strip():
        st.session_state.err = True
        st.rerun()

    try:
        result = api.complete_review(review["job_id"], values,
                                     record_index=review.get("record_index", 0))
    except api.ApiError as exc:
        st.session_state.submit_error = str(exc)
        st.session_state.err = not (values.get("mindef_cat") or "").strip()
        st.rerun()
        return

    _finish_registration(result)


def submit_new_asset(payload: dict) -> None:
    """Add asset — the same fields, sent to the register rather than to a review."""
    values = _field_values(payload)
    if not (values.get("mindef_cat") or "").strip():
        st.session_state.err = True
        st.rerun()

    try:
        result = api.create_asset(
            values,
            job_id=payload.get("job_id") or "",
            record_index=payload.get("record_index", 0),
        )
    except api.ApiError as exc:
        st.session_state.submit_error = str(exc)
        st.rerun()
        return

    _finish_registration(result)


def go_add() -> None:
    """Open the Add Asset screen with nothing carried over from a review."""
    st.session_state.add_mode = "manual"
    st.session_state.job_id = None
    st.session_state.record_index = 0
    st.session_state.upload_stage = ""
    st.session_state.upload_error = ""
    # Load-bearing: form.field seeds v_<key> only when the key is absent, so a
    # half-reviewed record's values would otherwise fill the "blank" form.
    _clear_field_state()
    go("add")


def set_add_mode(mode: str) -> None:
    st.session_state.add_mode = mode
    st.session_state.job_id = None
    st.session_state.record_index = 0
    st.session_state.upload_stage = ""
    st.session_state.upload_error = ""
    _clear_field_state()
    st.rerun()


def go_record(index: int) -> None:
    """Show another of this job's line items."""
    st.session_state.record_index = max(0, index)
    # A different record: the previous one's in-progress edits do not apply.
    _clear_field_state()
    api.clear_caches()
    st.rerun()


def on_search() -> None:
    st.session_state.search = st.session_state.get("assets_search", "")


def open_doc(doc: dict) -> None:
    st.session_state.open_doc = doc


def close_doc() -> None:
    st.session_state.open_doc = None
    for key in ("doc_zoom", "doc_page"):
        st.session_state.pop(key, None)
    st.rerun()


def _during() -> str:
    """", while <stage>" — which stage the run was on when it fell over."""
    labels = dict(progress.STAGES)
    label = labels.get(st.session_state.get("upload_stage", ""), "")
    return f" while {label[0].lower()}{label[1:]}" if label else ""


def _upload_failed(message: str) -> None:
    """Abandon the upload and say so where the operator will actually see it."""
    st.session_state.upload_error = message
    st.session_state.upload_stage = ""
    st.session_state.pending_job = None
    st.session_state.pending_since = 0.0
    # A fresh uploader, so the same file can be dropped again to retry.
    st.session_state.upload_nonce += 1


def on_upload() -> None:
    """POST /invoice, then let the main body poll the job.

    One callback for every uploader: all three surfaces mount the same widget
    key (screens/upload.py), so this cannot read a control that is not on
    screen — which is what previously made the add screen's rail a no-op.
    """
    upload = st.session_state.get(upload_control.uploader_key())
    if upload is None:
        return
    try:
        result = api.upload_invoice(upload.name, upload.getvalue(), upload.type)
    except api.ApiError as exc:
        _upload_failed(f"Upload failed — {exc}")
        return
    # Indexing this would raise KeyError inside the callback, where the except
    # above cannot reach it and Streamlit shows a bare traceback instead.
    job_id = (result or {}).get("job_id")
    if not job_id:
        _upload_failed("Upload failed — the API accepted the file but "
                       "returned no job id")
        return
    st.session_state.pending_job = job_id
    st.session_state.pending_since = time.time()
    st.session_state.upload_error = ""
    # Queued until the consumer picks it up and reports its first stage.
    st.session_state.upload_stage = ""
    st.session_state.upload_nonce += 1


def _poll_pending_job() -> None:
    """Advance the upload poll by one tick, then rerun.

    One tick per script run rather than a blocking loop. The pipeline takes
    around two minutes on a real invoice, and the old loop held the script
    runner for all of it *before* any chrome was drawn — the operator got a
    blank page behind a spinner and no way to tell the upload from a hang.
    Polling last, one tick at a time, keeps the screen drawn and the elapsed
    time ticking over.
    """
    job_id = st.session_state.pending_job
    try:
        status = api.get_job_status(job_id)
    except api.ApiError as exc:
        _upload_failed(f"Upload failed — {exc}")
        st.rerun()

    state = status.get("status")
    if state == "ready":
        st.session_state.job_id = job_id
        st.session_state.record_index = 0
        st.session_state.pending_job = None
        st.session_state.pending_since = 0.0
        st.session_state.upload_stage = ""
        st.session_state.upload_error = ""
        st.session_state.registered = False
        st.session_state.new_asset_no = None
        _clear_field_state()
        api.clear_caches()
        st.rerun()

    if state == "error":
        _upload_failed(f"Extraction failed{_during()} — {status.get('error', '')}")
        st.rerun()

    elapsed = time.time() - (st.session_state.pending_since or time.time())
    if elapsed > POLL_TIMEOUT:
        _upload_failed(f"Extraction timed out{_during()} — drop the invoice in "
                       "again to retry")
        st.rerun()

    # What the pipeline is working on, for the checklist. The elapsed seconds
    # stay here rather than on screen: they drive the timeout above, and a stage
    # name tells the operator far more than a counter did.
    st.session_state.upload_stage = status.get("stage", "")
    time.sleep(POLL_SECONDS)
    st.rerun()


# --------------------------------------------------------------------------- #
# Chrome
# --------------------------------------------------------------------------- #

def _render_header(screen: str) -> None:
    with st.container(key="mar_header"):
        st.markdown(header_left(), unsafe_allow_html=True)
        with st.container(key="mar_nav"):
            if st.button("Home", key="nav_home",
                         type="primary" if screen == "home" else "secondary"):
                go("home")
            if st.button("Assets", key="nav_assets",
                         type="primary" if screen == "assets" else "secondary"):
                go("assets")
            if st.button("Add asset", key="nav_add",
                         type="primary" if screen == "add" else "secondary"):
                go_add()
        st.markdown(header_right(USER_NAME, USER_ROLE, USER_INITIALS),
                    unsafe_allow_html=True)


def _render_toast() -> None:
    elapsed = time.time() - st.session_state.toast_at
    if not st.session_state.toast_title or elapsed > TOAST_SECONDS + 0.5:
        return
    remaining = max(0.0, TOAST_SECONDS - elapsed)
    with st.container(key="mar_toast"):
        st.markdown(
            f'<style>.st-key-mar_toast{{animation:toastIn 200ms ease-out,'
            f'toastOut 300ms ease-in {remaining:.2f}s forwards}}</style>'
            + toast(st.session_state.toast_title, st.session_state.toast_body),
            unsafe_allow_html=True,
        )
        if st.button("Dismiss", key="toast_dismiss"):
            st.session_state.toast_title = ""
            st.rerun()


def _render_error(message: str) -> None:
    st.markdown(
        '<div style="position:fixed;left:50%;transform:translateX(-50%);top:70px;'
        'z-index:90;padding:11px 13px;border:1px solid var(--prizm-color-danger);'
        'border-radius:6px;background:color-mix(in oklab, '
        'var(--prizm-color-danger) 8%, var(--prizm-color-surface));'
        f'font-size:12.5px;color:var(--prizm-color-danger)">{message}</div>',
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> None:
    _init_state()
    st.markdown(_styles(), unsafe_allow_html=True)
    st.markdown(AI_GRADIENT_DEFS, unsafe_allow_html=True)

    try:
        summary = api.get_summary()
        categories = api.get_categories()
        page = api.get_assets(st.session_state.search)
        review = api.get_review(st.session_state.job_id,
                                st.session_state.record_index)
        blank = api.get_blank_review()
    except api.ApiError as exc:
        _render_header(st.session_state.screen)
        _render_error(f"The asset pipeline API is unavailable — {exc}")
        return

    # A different record means the previous reviewer's edits no longer apply —
    # including a different line item of the *same* job, now that the reviewer
    # can page through them.
    record = ((review or {}).get("job_id"), (review or {}).get("record_index"))
    if record != st.session_state.review_job:
        _clear_field_state()
        st.session_state.review_job = record
    # The backend clamps the index to what the job actually has; follow it, or
    # a stale index from a longer previous job sticks around in the pager.
    if review:
        st.session_state.record_index = review.get("record_index", 0)

    screen = st.session_state.screen
    if screen == "form" and not review:
        screen = st.session_state.screen = "home"

    _render_header(screen)

    if screen == "home":
        home_screen.render(summary, review, go, on_upload,
                           pending=bool(st.session_state.pending_job),
                           stage=st.session_state.upload_stage)
    elif screen == "add":
        # In upload mode the pipeline result stands in for the blank form once
        # it is ready; a blank manual form with review=None is a valid state, so
        # the "form" redirect guard above must not extend to this screen.
        prefilled = bool(st.session_state.add_mode == "upload"
                         and st.session_state.job_id and review)
        add_screen.render(
            review if prefilled else blank,
            categories,
            mode=st.session_state.add_mode,
            prefilled=prefilled,
            err=st.session_state.err,
            go=go,
            on_mode=set_add_mode,
            on_upload=on_upload,
            pending=bool(st.session_state.pending_job),
            stage=st.session_state.upload_stage,
            submit=lambda: submit_new_asset(review if prefilled else blank),
            open_doc=open_doc,
            go_record=go_record,
        )
    elif screen == "form":
        review_screen.render(
            review,
            categories,
            extracted_at=_extracted_at(summary),
            err=st.session_state.err,
            go=go,
            submit=lambda: submit(review),
            open_doc=open_doc,
            go_record=go_record,
        )
    else:
        assets_screen.render(page, st.session_state.new_asset_no, on_search, go_add)

    # One banner: both are fixed at the same spot, so they would overlap.
    # An upload problem is the more recent event, so it wins.
    problem = st.session_state.upload_error or st.session_state.submit_error
    if problem:
        _render_error(problem)
    _render_toast()

    # The dialog stays open across the reruns its own controls trigger, so the
    # selection is held in session state and cleared by its Close button.
    if st.session_state.open_doc:
        viewer.show(st.session_state.open_doc, close_doc)

    # Last, so the screen above is fully drawn before the tick blocks and
    # reruns. Everything after this line would be dead code during an upload.
    if st.session_state.pending_job:
        _poll_pending_job()


def _extracted_at(summary: dict) -> str:
    from ui import format_updated

    return format_updated(summary.get("updated_at", ""))


main()
