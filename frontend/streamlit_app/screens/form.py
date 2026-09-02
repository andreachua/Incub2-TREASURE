"""Rendering the registration card.

Extracted from the review screen so the add-asset screen can render the same
form rather than a second copy of it. The container keys are unchanged —
``sec_vendor``, ``grid_vendor``, ``f_<field>``, ``review_card``,
``review_footer``, ``review_actions`` and the rest — which is what lets both
screens share every rule in styles/app.css without a line of new CSS for the
form itself.

Field state lives in ``st.session_state`` under ``v_<key>``, seeded once from
the payload and thereafter owned by the user; ``seed_<key>`` keeps the original
so the provenance chip can say "Entered by you" once a value is edited.
"""

from __future__ import annotations

import streamlit as st

from screens.fields import (
    ALL_FIELDS,
    CUSTODIAN_FIELD,
    DESCRIPTION_FIELD,
    Field,
    LOGISTICS_FIELDS,
    MINDEF_CAT_OPTIONS,
    MINDEF_FIELDS,
    PROV_CHIP,
    PURCHASE_TYPES,
    TAGGABLE_OPTIONS,
    VENDOR_FIELDS,
)
from ui import ICON_ALERT, ICON_INFO, esc, sparkle

_LABEL_ROW = ("display:flex;align-items:flex-start;flex-wrap:wrap;gap:2px 5px;"
              "min-height:32px;line-height:1.35")
_SECTION = "padding:20px 22px 22px;display:flex;flex-direction:column;gap:16px"
_H2 = "margin:0;font-size:15px;font-weight:600;letter-spacing:-0.015em"
_DIVIDER = '<div style="height:1px;background:var(--prizm-color-border)"></div>'

def _tip_icon(text: str) -> str:
    return (f'<span data-tip="{esc(text)}" style="display:inline-grid;'
            'place-items:center;width:14px;height:14px;cursor:help;'
            f'color:var(--prizm-color-fg-subtle)">{ICON_INFO}</span>')


def _label_html(field: Field, extra: str = "") -> str:
    star = ('<span style="color:var(--prizm-color-danger)"> *</span>'
            if field.required else "")
    hint = ('<span style="font-size:11px;color:var(--prizm-color-fg-subtle);'
            f'font-weight:400">{esc(field.hint)}</span>' if field.hint else "")
    tip = _tip_icon(field.tip) if field.tip else ""
    return (f'<div style="{_LABEL_ROW}">'
            f'<span style="font-size:12px;font-weight:600">{esc(field.label)}{star}'
            f'</span>{hint}{tip}{extra}</div>')


def _chip_html(prov: str) -> str:
    text, color = PROV_CHIP.get(prov, PROV_CHIP["ai"])
    return ('<span style="display:inline-flex;align-items:center;gap:5px;'
            'font-size:10.5px;font-family:var(--prizm-font-mono);'
            f'color:{color}">'
            f'<span style="width:5px;height:5px;border-radius:9px;'
            f'background:{color}"></span>{text}</span>')


# --------------------------------------------------------------------------- #
# The AI "WHY" popover — the design's panel, filled from ReviewPayload.explanation
# --------------------------------------------------------------------------- #

def _why_entry(label: str, quote: str) -> str:
    return ('<div style="display:flex;gap:9px">'
            '<span style="flex:none;width:66px;font-size:10.5px;'
            'font-family:var(--prizm-font-mono);color:var(--prizm-color-fg-subtle);'
            f'padding-top:1px">{esc(label)}</span>'
            '<span style="font-size:12px;color:var(--prizm-color-fg);'
            f'line-height:1.5;text-wrap:pretty">{esc(quote)}</span></div>')


def why_popover(explanation: dict | None) -> str:
    explanation = explanation or {}
    title = explanation.get("title") or "Classification rationale"
    citations = explanation.get("citations") or []
    if citations:
        entries = "".join(_why_entry(c.get("source_label", ""), c.get("quote", ""))
                          for c in citations)
    else:
        # The pipeline recorded a rationale but no quotable spans — say so
        # rather than showing invented quotes.
        entries = _why_entry(
            "Rationale",
            explanation.get("rationale")
            or "No rationale was recorded for this classification.",
        )
    runner_up = explanation.get("runner_up") or ""
    # Only the runner-up line lives down here now, and the pipeline does not
    # record one yet — so draw the divider rule only when there is something
    # under it, rather than an empty strip.
    footer = (
        '<div style="display:flex;align-items:center;gap:10px;padding-top:10px;'
        'border-top:1px solid var(--prizm-color-border)">'
        '<span style="font-size:11.5px;color:var(--prizm-color-fg-muted)">'
        f'Second-best: {esc(runner_up)}.</span></div>'
    ) if runner_up else ""

    trigger = (
        '<span data-ai-trigger="" style="display:inline-flex;align-items:center;'
        'gap:4px;padding:1px 6px 1px 4px;border-radius:99px;border:1px solid '
        'color-mix(in oklab, var(--prizm-color-accent) 30%, '
        'var(--prizm-color-border));background:color-mix(in oklab, '
        'var(--prizm-color-accent) 7%, var(--prizm-color-surface));cursor:help">'
        f'{sparkle(12)}'
        '<span style="font-size:10px;font-weight:600;letter-spacing:0.03em;'
        'color:var(--prizm-color-accent)">WHY</span></span>'
    )
    # The design hides the panel with inline opacity/pointer-events/transform and
    # flips them from JS. An inline style cannot be overridden by a :hover rule,
    # so those three live in the stylesheet instead — same rendered result.
    panel = (
        '<div data-ai-pop="" style="position:absolute;z-index:60;'
        'top:calc(100% + 6px);left:0;width:420px;max-width:calc(100vw - 60px);'
        'padding:14px 15px;border:1px solid var(--prizm-color-border-strong);'
        'border-radius:8px;background:var(--prizm-color-surface-elevated);'
        'box-shadow:var(--prizm-shadow-lg)">'
        '<div style="display:flex;flex-direction:column;gap:11px">'
        '<div style="display:flex;align-items:center;gap:7px">'
        f'{sparkle(13)}'
        '<span style="font-size:12px;font-weight:600;letter-spacing:-0.01em">'
        f'{esc(title)}</span></div>'
        f'<div style="display:flex;flex-direction:column;gap:8px">{entries}</div>'
        f'{footer}'
        '</div></div>'
    )
    return f'<span class="mar-why-wrap">{trigger}{panel}</span>'


# --------------------------------------------------------------------------- #
# Controls
# --------------------------------------------------------------------------- #

def _widget(field: Field, value: str, categories: list[dict]) -> None:
    """Render the field's control. The value lives in session_state[f_<key>]."""
    state_key = f"v_{field.key}"
    if state_key not in st.session_state:
        st.session_state[state_key] = value

    common = dict(label=field.label, key=state_key, label_visibility="collapsed")

    if field.control == "select":
        options = list(field.options)
        current = st.session_state[state_key]
        if current and current not in options:
            options = [current, *options]
        elif not current:
            # Nothing upstream supplied this. Rather than silently defaulting to
            # the first option — which would submit a value nobody chose while
            # the chip still reads "Needs manual entry" — offer the design's own
            # empty prompt, as it does for the CAT B/C/Dev select.
            options = ["", *options]
        st.selectbox(options=options,
                     format_func=lambda v: v or "Select — required", **common)
    elif field.control == "category":
        names = [c["name"] for c in categories]
        current = st.session_state[state_key]
        if current and current not in names:
            names = [current, *names]
        if not current:
            names = ["", *names]
        if st.session_state[state_key] not in names:
            st.session_state[state_key] = names[0] if names else ""
        st.selectbox(options=names,
                     format_func=lambda v: v or "Select — required", **common)
    elif field.control == "mindef_cat":
        values = [v for v, _ in MINDEF_CAT_OPTIONS]
        labels = dict(MINDEF_CAT_OPTIONS)
        if st.session_state[state_key] not in values:
            st.session_state[state_key] = ""
        st.selectbox(options=values, format_func=lambda v: labels[v], **common)
    elif field.control == "textarea":
        st.text_area(height=112, max_chars=field.maxlength, **common)
    else:
        st.text_input(max_chars=field.maxlength,
                      placeholder=field.placeholder, **common)

    # Remember what the control was actually seeded with (a select with no
    # extracted value lands on its first option) so defaulting is never
    # mistaken for an operator edit.
    st.session_state.setdefault(f"seed_{field.key}",
                                str(st.session_state[state_key] or ""))


def field(spec: Field, review_fields: dict, categories: list[dict],
          extra_label: str = "") -> None:
    """One labelled control with its provenance chip underneath."""
    entry = review_fields.get(spec.key) or {}
    extracted = entry.get("value", "")
    prov = entry.get("prov", "ai")

    with st.container(key=f"f_{spec.key}"):
        st.markdown(_label_html(spec, extra_label), unsafe_allow_html=True)
        _widget(spec, extracted, categories)
        current = str(st.session_state.get(f"v_{spec.key}", extracted))
        seed = st.session_state.get(f"seed_{spec.key}", extracted)
        shown = "edited" if (current.strip() and current != seed) else prov
        st.markdown(_chip_html(shown), unsafe_allow_html=True)


def grid(specs, review_fields, categories, name: str,
         explanation: dict | None = None) -> None:
    """One of the design's three-up field grids."""
    with st.container(key=f"grid_{name}"):
        for spec in specs:
            extra = why_popover(explanation) if spec.control == "category" else ""
            field(spec, review_fields, categories, extra)


def description(fields: dict) -> None:
    """The description spans the full grid width and carries a live counter."""
    entry = fields.get("description") or {}
    extracted = entry.get("value", "")
    prov = entry.get("prov", "ai")
    state_key = "v_description"
    if state_key not in st.session_state:
        st.session_state[state_key] = extracted
    remaining = 800 - len(str(st.session_state.get(state_key, "")))

    with st.container(key="f_description"):
        st.markdown(
            '<div style="display:flex;align-items:center;'
            'justify-content:space-between;gap:8px;min-height:17px">'
            '<span style="font-size:12px;font-weight:600">Description'
            '<span style="color:var(--prizm-color-danger)"> *</span></span>'
            '<span style="font-size:11px;font-family:var(--prizm-font-mono);'
            'color:var(--prizm-color-fg-subtle);font-variant-numeric:tabular-nums">'
            f'{remaining} characters remaining</span></div>',
            unsafe_allow_html=True,
        )
        st.text_area("Description", key=state_key, height=112, max_chars=800,
                     label_visibility="collapsed")
        current = str(st.session_state.get(state_key, ""))
        shown = "edited" if (current.strip() and current != extracted) else prov
        st.markdown(_chip_html(shown), unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# The card both screens render
# --------------------------------------------------------------------------- #

def form_card(
    fields: dict,
    categories: list[dict],
    *,
    footer,
    explanation: dict | None = None,
    err: bool = False,
    err_banner: str = "",
    card_key: str = "review_card",
) -> None:
    """The four-section registration card: vendor, MINDEF/SAF, logistics, custody.

    ``footer`` is a callable that draws whatever sits under the last divider —
    the two screens label their buttons differently but share the layout.

    ``card_key`` defaults to "review_card" and both screens pass it: Streamlit
    only objects to duplicate keys within a single run, and the two screens never
    render together, so sharing the key means sharing the CSS.
    """
    with st.container(key=card_key):
        with st.container(key="sec_vendor"):
            st.markdown(f'<h2 style="{_H2}">Vendor details</h2>',
                        unsafe_allow_html=True)
            grid(VENDOR_FIELDS, fields, categories, "vendor")
        st.markdown(_DIVIDER, unsafe_allow_html=True)

        with st.container(key="sec_mindef"):
            st.markdown(f'<h2 style="{_H2}">MINDEF/SAF details</h2>',
                        unsafe_allow_html=True)
            grid(MINDEF_FIELDS, fields, categories, "mindef", explanation)
            description(fields)
        st.markdown(_DIVIDER, unsafe_allow_html=True)

        with st.container(key="sec_logistics"):
            grid(LOGISTICS_FIELDS, fields, categories, "logistics")
        st.markdown(_DIVIDER, unsafe_allow_html=True)

        with st.container(key="sec_custody"):
            st.markdown(f'<h2 style="{_H2}">Custodianship</h2>',
                        unsafe_allow_html=True)
            with st.container(key="custody_box"):
                field(CUSTODIAN_FIELD, fields, categories)

        if err and err_banner:
            st.markdown(err_banner, unsafe_allow_html=True)

        footer()
