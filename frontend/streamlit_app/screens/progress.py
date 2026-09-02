"""What the pipeline is doing right now, as a checklist.

An invoice takes one to two minutes to come back, and the app hides Streamlit's
own running indicator — so for the whole of that the operator used to have a job
id and a seconds counter, neither of which says anything about what the model is
working on. The backend now reports which stage it has reached
(``app/pipeline/orchestrator.py``: ``STAGES``, published per job to Redis and
served on ``/api/jobs/{id}/status``); this renders it.

``STAGES`` below mirrors the backend's keys and pairs each with the design's
copy, the same way ``screens/fields.py`` mirrors ``provenance.UI_FIELDS``. A key
this module does not recognise is not a crisis: the card falls back to a single
"Working…" row rather than ticking the wrong ones.
"""

from __future__ import annotations

from ui import ICON_CHECK, esc

# Keys mirror app.pipeline.orchestrator.STAGES; the labels are ours.
STAGES: tuple[tuple[str, str], ...] = (
    ("extract", "Reading the invoice"),
    ("context", "Finding the purchase order and scope of work"),
    ("enrich", "Splitting and pricing the line items"),
    ("categorize", "Classifying against the MINDEF categories"),
    ("write", "Writing the asset records"),
)

_EYEBROW = ("font-size:11px;font-weight:600;letter-spacing:0.09em;"
            "text-transform:uppercase;color:var(--prizm-color-fg-subtle)")
_CARD = ("display:flex;flex-direction:column;gap:12px;padding:16px;"
         "border:1px solid color-mix(in oklab, var(--prizm-color-accent) 25%, "
         "var(--prizm-color-border));border-radius:8px;"
         "background:color-mix(in oklab, var(--prizm-color-accent) 4%, "
         "var(--prizm-color-surface));box-shadow:var(--prizm-shadow-sm)")
_ROW = "display:flex;align-items:center;gap:9px;font-size:12.5px"

# The design has no spinner, so this is the smallest one consistent with it: the
# same 1.5 stroke as every other icon, a three-quarter arc, turning. The
# keyframe lives in styles/app.css with the toast's.
_SPINNER = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
            'stroke="var(--prizm-color-accent)" style="flex:none;'
            'transform-origin:center;animation:mar-spin 0.9s linear infinite">'
            '<path d="M12 2a10 10 0 1 0 10 10"></path></svg>')
_DOT = ('<span style="flex:none;width:16px;display:grid;place-items:center">'
        '<span style="width:5px;height:5px;border-radius:9px;'
        'background:var(--prizm-color-border-strong)"></span></span>')


def _row(icon: str, label: str, color: str, weight: int) -> str:
    return (f'<div style="{_ROW}">{icon}'
            f'<span style="color:{color};font-weight:{weight};'
            f'text-wrap:pretty">{esc(label)}</span></div>')


def stage_card(stage: str) -> str:
    """The checklist for a job at ``stage``.

    ``""`` is the real state between the upload landing and the consumer picking
    the job off the queue: every row pending, nothing spinning, nothing claimed.
    """
    keys = [key for key, _ in STAGES]
    if stage and stage not in keys:
        # A stage this build does not know (a receipt job, or one added since).
        # Say so plainly rather than tick rows that may not be true.
        rows = _row(_SPINNER, "Working…", "var(--prizm-color-fg)", 500)
    else:
        current = keys.index(stage) if stage else -1
        rows = "".join(
            _row(ICON_CHECK, label, "var(--prizm-color-fg-muted)", 400)
            if i < current else
            _row(_SPINNER, label, "var(--prizm-color-fg)", 500)
            if i == current else
            _row(_DOT, label, "var(--prizm-color-fg-subtle)", 400)
            for i, (_, label) in enumerate(STAGES)
        )

    return (f'<div style="{_CARD}">'
            f'<span style="{_EYEBROW}">Extracting</span>'
            f'<div style="display:flex;flex-direction:column;gap:9px">{rows}</div>'
            '</div>')
