"""Shared Pydantic data models for the pipeline."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.logging_config import get_logger

log = get_logger("schema.items")


def _coerce_number(value: object) -> float:
    """Best-effort conversion of a model-produced value to a float.

    Local VLMs frequently emit prices/quantities as strings like "$1,299.00"
    or "2 pcs". Strip everything that isn't part of a number rather than fail.
    """
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return 0.0
    text = str(value).strip()
    cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ".-")
    if cleaned in ("", "-", ".", "-."):
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _quantity_before(value: object) -> float:
    """A line with no quantity printed is one unit, not zero.

    Separate from the price rule (where a missing value really is 0.0) so
    ``_whole_units`` below does not warn about every line that simply never
    carried a quantity.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return 1.0
    return _coerce_number(value)


def _whole_units(quantity: float, name: str) -> float:
    """Round a model-produced quantity to a whole count of units, never below one.

    A register row counts units. Local models sometimes return a share of a
    bundle instead of a count: one invoice line decomposed into seven parts came
    back as 0.2, 0.6 and 1.5 "switches", and that is what landed in `mar`. A
    fraction is never a valid asset count, so correct it and say so — a whole
    number the reviewer can fix beats a 0.2 that reads as fact.

    Applied on both models that carry a count, because they are reached by
    different paths: Stage 1 builds RawLineItems directly (skipping
    ``coerce_line_items``), and Stage 2 can rebuild an AssetRecord's quantity
    from its own model output.
    """
    if quantity == int(quantity) and quantity >= 1:
        return quantity
    corrected = float(max(1, round(quantity)))
    log.warning("quantity %g is not a whole count for %r; using %g",
                quantity, name or "(unnamed)", corrected)
    return corrected


class RawLineItem(BaseModel):
    """A single line item as extracted from a receipt in Stage 1."""

    name: str
    description: str = ""
    quantity: float = 1.0
    price: float = 0.0
    serial_no: str = ""

    @field_validator("price", mode="before")
    @classmethod
    def _numbers(cls, v: object) -> float:
        return _coerce_number(v)

    @field_validator("quantity", mode="before")
    @classmethod
    def _quantity(cls, v: object) -> float:
        return _quantity_before(v)

    @field_validator("name", "description", "serial_no", mode="before")
    @classmethod
    def _strings(cls, v: object) -> str:
        return "" if v is None else str(v).strip()

    @model_validator(mode="after")
    def _units(self) -> "RawLineItem":
        self.quantity = _whole_units(self.quantity, self.name)
        return self


class InvoiceHeader(BaseModel):
    """Document-level fields read off the invoice itself in Stage 1.

    ``vendor``/``po_no`` are the invoice's own printed values — a fallback for
    when the PO document can't be resolved/extracted (see ``ProcurementHeader``).
    """

    invoice_no: str = ""
    invoice_date: str = ""
    do_no: str = ""
    do_date: str = ""
    vendor: str = ""
    po_no: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def _strings(cls, v: object) -> str:
        return "" if v is None else str(v).strip()
    payment_event: str = ""


class ProcurementHeader(BaseModel):
    """Header fields extracted from the matched Purchase Order document."""

    po_no: str = ""
    vendor: str = ""
    project: str = ""
    period_contract: str = ""
    purchase_type: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def _strings(cls, v: object) -> str:
        return "" if v is None else str(v).strip()


class AssetRecord(BaseModel):
    """A categorized asset record — the Stage 3 output shape.

    ``group`` is the top-level classification (Assets / Inventories / Expenses)
    derived from the chosen ``category``.
    """

    name: str
    description: str = ""
    category: str
    group: str = ""
    reasoning: str = ""  # why this category/group was chosen
    quantity: float = 1.0
    price: float = 0.0

    # Document-level fields (same across every record from one job) --------- #
    invoice_no: str = ""
    po_no: str = ""
    do_no: str = ""
    do_date: str = ""
    gl_date: str = ""
    vendor: str = ""
    project: str = ""
    period_contract: str = ""
    purchase_type: str = ""

    # Item-level fields ------------------------------------------------------ #
    serial_no: str = ""
    taggable: str = ""
    asset_capitalisation_date: str = ""

    @field_validator("price", mode="before")
    @classmethod
    def _numbers(cls, v: object) -> float:
        return _coerce_number(v)

    @field_validator("quantity", mode="before")
    @classmethod
    def _quantity(cls, v: object) -> float:
        return _quantity_before(v)

    @field_validator(
        "name", "description", "category", "group", "reasoning",
        "invoice_no", "po_no", "do_no", "do_date", "gl_date", "vendor",
        "project", "period_contract", "purchase_type", "serial_no", "taggable",
        "asset_capitalisation_date",
        mode="before",
    )
    @classmethod
    def _strings(cls, v: object) -> str:
        return "" if v is None else str(v).strip()

    @model_validator(mode="after")
    def _units(self) -> "AssetRecord":
        self.quantity = _whole_units(self.quantity, self.name)
        return self


class Category(BaseModel):
    """A single allowed category loaded from config/categories.yaml.

    ``group`` is one of Assets / Inventories / Expenses; ``code`` is the policy
    reference letter (a-j) from categories.txt.
    """

    name: str
    group: str = ""
    code: str = ""
    description: str = ""


def coerce_line_items(entries: list) -> list[RawLineItem]:
    """Turn whatever a model returned into RawLineItems, dropping what will not fit.

    Local models return the odd string, null or malformed object inside an
    otherwise good array; a nameless item is not a line item. Skipping those
    rather than raising is what keeps one bad element from losing a whole
    invoice. Shared by every stage that parses items out of model output.
    """
    out: list[RawLineItem] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            item = RawLineItem(**entry)
        except Exception:
            continue
        if item.name:
            out.append(item)
    return out


# --------------------------------------------------------------------------- #
# Matching an invoice to its purchase order and scope of work
# --------------------------------------------------------------------------- #

MatchSignal = Literal[
    "ref_exact", "ref_fuzzy", "vendor", "total_amount",
    "payment_event", "item_overlap", "category", "none",
]


class MatchEvidence(BaseModel):
    """One reason the crawler believes an invoice belongs to a purchase order.

    ``detail`` is written for a human and is rendered verbatim in the review
    screen, so a reviewer can judge the match rather than trust a number.
    """

    signal: MatchSignal = "none"
    detail: str = ""
    score: float = 0.0


class ContextMatch(BaseModel):
    """Which PO and SOW an invoice was matched to, and on what grounds.

    ``confidence`` is always recomputed from the signals that actually verified
    against the database — never taken from the model, which will happily assert
    certainty about a reference it invented.
    """

    po_ref: str = ""
    sow_ref: str = ""
    vendor: str = ""
    po_filenames: list[str] = []
    sow_filename: str = ""
    confidence: float = 0.0
    method: Literal["exact", "agent", "fallback_llm", "deterministic", "none"] = "none"
    evidence: list[MatchEvidence] = []
    reasoning: str = ""
    candidates_considered: list[str] = []

    @property
    def found(self) -> bool:
        return bool(self.po_ref)


class InvoiceHint(BaseModel):
    """Everything on the invoice the crawler can match against.

    Assembled from what Stage 1 already extracted — no second look at the
    document and no extra model call.
    """

    po_ref: str = ""
    po_no: str = ""
    vendor: str = ""
    invoice_no: str = ""
    invoice_date: str = ""
    total_amount: float = 0.0
    payment_event: str = ""
    item_names: list[str] = []
