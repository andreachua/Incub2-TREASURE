"""Render the screens for real and check what comes out.

Streamlit renders over a websocket, so a request to :8501 only ever returns the
page shell — you cannot tell from it whether a screen threw. AppTest actually
runs the script, which is the only way to catch the failure this codebase is
most prone to: a name that exists at import but is missing inside a function
body, which is exactly what splitting one screen module into three introduces.
It has already caught two (a dropped ICON_ALERT, and sparkle() called without
its size).

Needs the API reachable at $API_BASE.

Run (inside the compose network):
    docker run --rm --network incub2_default -e API_BASE=http://api:8000 \\
      -v "$PWD:/app:ro" -w /app --entrypoint python mar-frontend:latest \\
      tests/test_screens.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

APP = str(Path(__file__).resolve().parent.parent / "app.py")
sys.path.insert(0, str(Path(APP).parent))

from streamlit.testing.v1 import AppTest  # noqa: E402

# The registration card, whichever screen draws it.
FORM_INPUTS, FORM_SELECTS, FORM_TEXTAREAS = 18, 4, 1

APP_CSS = Path(APP).parent / "styles" / "app.css"


def _run(**state):
    at = AppTest.from_file(APP, default_timeout=180)
    for key, value in state.items():
        at.session_state[key] = value
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def test_review_screen_renders() -> None:
    at = _run(screen="form")
    assert len(at.text_input) == FORM_INPUTS
    assert len(at.selectbox) == FORM_SELECTS
    assert len(at.text_area) == FORM_TEXTAREAS


def test_add_asset_manual_renders_the_same_card() -> None:
    """The whole point of extracting form.py: one card, two screens."""
    at = _run(screen="add", add_mode="manual")
    assert len(at.text_input) == FORM_INPUTS
    assert len(at.selectbox) == FORM_SELECTS
    assert len(at.text_area) == FORM_TEXTAREAS
    labels = [b.label for b in at.button]
    assert "Add asset" in labels and "Cancel" in labels


def test_add_asset_upload_shows_the_uploader_first() -> None:
    at = _run(screen="add", add_mode="upload")
    assert len(at.get("file_uploader")) == 1
    assert not at.text_input, "the form should not appear until a job is ready"


def test_every_invisible_overlay_button_is_actually_positioned() -> None:
    """A card-as-button needs both halves of the overlay, or it is a dead card.

    The design's cards are markdown; a Streamlit button is made invisible
    (`opacity: 0`) and stretched over the card (`position: absolute`) to become
    the click target. A key that gets only the first half is an invisible
    control sitting in normal flow *below* its card — the card then looks
    normal and does nothing.

    This is not hypothetical: `mode_btn_manual`/`mode_btn_upload` and `add_btn`
    were invisible but never positioned, which made "Add asset -> Upload an
    invoice" unreachable and the whole upload feature unusable. AppTest clicks
    the widget directly and so cannot see it; only the stylesheet can.
    """
    css = APP_CSS.read_text(encoding="utf-8")
    # Strip comments so a key mentioned in prose is not read as a selector.
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    invisible: set[str] = set()
    positioned: set[str] = set()
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        keys = set(re.findall(r"st-key-([A-Za-z0-9_]+)", selector))
        if not keys:
            continue
        if re.search(r"opacity:\s*0\s*[;}]", body):
            invisible |= keys
        if re.search(r"position:\s*absolute", body):
            positioned |= keys

    assert invisible, "no invisible overlay buttons found — has app.css moved?"
    dead = sorted(invisible - positioned)
    assert not dead, (
        f"overlay button(s) {dead} are opacity:0 but never position:absolute — "
        "they render as invisible controls in normal flow, so the card they "
        "should cover is unclickable"
    )


def test_the_upload_callback_reads_the_uploader_that_is_on_screen() -> None:
    """Every surface mounts one uploader, under the key the callback reads.

    The surfaces used to declare their own widget keys, and one of them was
    handed a callback that read a *different* one — so an invoice dropped there
    hit the `if upload is None: return` guard and silently did nothing. Sharing
    one key is what prevents that, and it is only safe while no two uploaders
    are ever mounted at once; both halves are asserted here. The review screen
    is not in this list: its rail lists source documents but does not take them.
    """
    for state in ({"screen": "add", "add_mode": "upload"},
                  {"screen": "home"}):
        at = _run(**state)
        mounted = at.get("file_uploader")
        assert len(mounted) == 1, f"{state}: expected one uploader, got {len(mounted)}"

        # uploader_key() needs a script context, so rebuild it the same way
        # the callback does and check the widget actually answers to it.
        expected = f"invoice_uploader_{at.session_state['upload_nonce']}"
        in_state = [k for k in at.session_state.filtered_state if "uploader" in k]
        assert in_state == [expected], (
            f"{state}: the uploader on screen is {in_state}, but the callback "
            f"reads {expected!r}"
        )

    review = _run(screen="form")
    assert not review.get("file_uploader"), (
        "the review rail is read-only — documents arrive with the invoice"
    )


def test_the_progress_checklist_tracks_the_reported_stage() -> None:
    """A pure render test — no API, no upload, just the card's three row states.

    The stage keys mirror the backend's (app/pipeline/orchestrator.py STAGES).
    If they drift, the operator watches the wrong row spin, which nothing else
    here would catch.
    """
    from screens.progress import STAGES, stage_card

    keys = [k for k, _ in STAGES]
    for i, key in enumerate(keys):
        html = stage_card(key)
        for _, label in STAGES:
            assert label in html, f"{key}: {label!r} missing from the card"
        # Done rows carry the tick, the current row carries the spinner.
        assert html.count("mar-spin") == 1, f"{key}: expected exactly one spinner"
        assert html.count("m9 12 2 2 4-4") == i, (
            f"{key}: expected {i} completed row(s)")

    queued = stage_card("")
    assert "mar-spin" not in queued, "nothing is running before the job is picked up"
    assert "m9 12 2 2 4-4" not in queued, "nothing is done before the job starts"
    for _, label in STAGES:
        assert label in queued, f"queued card is missing {label!r}"

    unknown = stage_card("teleporting")
    assert "Working" in unknown and "m9 12 2 2 4-4" not in unknown, (
        "an unrecognised stage must not tick rows that may not be true")


def test_both_entry_points_open_the_add_screen() -> None:
    at = _run(screen="assets")
    [b for b in at.button if b.label == "Add"][0].click().run()
    assert at.session_state["screen"] == "add"
    assert at.session_state["add_mode"] == "manual"

    at2 = _run(screen="home")
    [b for b in at2.button if b.label == "Add asset"][0].click().run()
    assert at2.session_state["screen"] == "add"


def test_submitting_an_empty_form_shows_the_error_rather_than_crashing() -> None:
    at = _run(screen="add", add_mode="manual")
    [b for b in at.button if b.label == "Add asset"][-1].click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert at.session_state["screen"] == "add", "should stay put"
    assert at.session_state["err"] is True, "the inline error should be raised"


def test_a_manual_add_registers_and_lands_on_assets() -> None:
    at = _run(screen="add", add_mode="manual")

    def text(label: str, value: str) -> None:
        next(i for i in at.text_input if i.label == label).set_value(value)

    def select(label: str, value: str) -> None:
        next(s for s in at.selectbox if s.label == label).set_value(value)

    text("Brand/Model", "Test Asset (automated)")
    text("Vendor/Supplier", "TechNova Systems Pte Ltd")
    text("Invoice No.", "TEST-0001")
    text("Unit Price", "1000.00")
    select("MINDEF Category", "Office Equipment")
    select("CAT B or C or Dev?", "B")
    at.text_area[0].set_value("Created by tests/test_screens.py.")
    at.run()

    [b for b in at.button if b.label == "Add asset"][-1].click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert at.session_state["screen"] == "assets", "should land on the register"
    assert at.session_state["new_asset_no"], "the new row should be highlighted"
    assert at.session_state["toast_title"] == "Asset record registered"
    assert not at.session_state["submit_error"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"OK — {len(tests)} screen tests")
