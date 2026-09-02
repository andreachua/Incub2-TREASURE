"""Where each review-form field's value comes from.

The review screen colours every field by provenance (extracted / system default
/ manual entry). That is a deterministic property of *which stage can produce
the field*, not something the LLM decides, so it lives here as a plain map
rather than as extra pipeline output.
"""

from __future__ import annotations

from .ui import Explanation, Provenance, ReviewField

# The 23 fields the review form shows, in display order. Keys are the
# AssetRecord / mar column names.
UI_FIELDS: tuple[str, ...] = (
    # Vendor details
    "purchase_type", "invoice_no", "po_no", "do_no", "name", "vendor",
    "quantity", "price", "period_contract",
    # MINDEF/SAF details
    "project", "category", "mindef_cat", "taggable", "description",
    # Finance / logistics
    "gl_date", "do_date", "tag_no", "serial_no",
    "asset_capitalisation_date", "material_number", "receiving_plant",
    "receiving_sloc",
    # Custodianship
    "custodian",
)

# Filled deterministically by src/derive.py or by a configured default, never
# read off a document.
_SYSTEM_FIELDS = frozenset({"taggable", "asset_capitalisation_date", "custodian"})

# No pipeline stage can supply these: the reviewer's own judgement call
# (mindef_cat) or values assigned downstream by tagging/ERP.
_MANUAL_FIELDS = frozenset({
    "mindef_cat", "tag_no", "material_number", "receiving_plant",
    "receiving_sloc",
})


def provenance_for(field: str, value: str) -> Provenance:
    """Classify one field. An empty extracted field is really a manual one."""
    if field in _MANUAL_FIELDS:
        return "manual"
    if field in _SYSTEM_FIELDS:
        return "system" if str(value).strip() else "manual"
    return "ai" if str(value).strip() else "manual"


def build_fields(values: dict[str, object]) -> dict[str, ReviewField]:
    """Wrap a flat {field: value} mapping as provenance-tagged form fields."""
    out: dict[str, ReviewField] = {}
    for field in UI_FIELDS:
        raw = values.get(field, "")
        value = "" if raw is None else str(raw).strip()
        out[field] = ReviewField(value=value, prov=provenance_for(field, value))
    return out


def count_provenance(fields: dict[str, ReviewField]) -> dict[str, int]:
    """Tallies for the review rail ("Extracted 20 fields", etc.)."""
    counts = {"ai": 0, "system": 0, "manual": 0}
    for field in fields.values():
        counts[field.prov] = counts.get(field.prov, 0) + 1
    return counts


def build_explanation(
    category: str, reasoning: str, source_count: int = 0
) -> Explanation | None:
    """Turn the categorizer's free-text rationale into the popover's payload.

    Stage 2 records *why* it picked a category but not quotable spans, so
    ``citations`` stays empty and the UI says so rather than fabricating
    quotes. If a future stage emits structured citations, populate them here.
    """
    if not (category or "").strip() and not (reasoning or "").strip():
        return None
    return Explanation(
        title=f"Why {category}" if category else "Classification rationale",
        rationale=(reasoning or "").strip(),
        citations=[],
        source_count=source_count,
    )
