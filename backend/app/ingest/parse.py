"""Pulling the facts out of a purchase order or scope of work, without a model.

Every pattern here was checked against all 25 documents in ``data/po`` and
``data/sow``: the vendor line and the total order value each match 25/25. That
matters more than it sounds — it means the search index can be built from the
markdown already sitting in Postgres, deterministically, with no LLM calls and
no dependency on the model endpoint being up. An LLM is only worth reaching for
where a regex genuinely cannot go.

These are parse artefacts, not pipeline output, which is why they live here and
not in ``schemas/domain.py``.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

# "| Purchase Order Ref No.: 1000672403 | Date: ... |"  — also matches "Contract No.:"
PO_REF_RE = re.compile(r"Purchase Order Ref\.?\s*No\.?:?\s*([0-9]{6,})", re.IGNORECASE)
# "| To: ApexForge Computing Pte Ltd, 3 Fusionopolis Way, ... |"
PO_VENDOR_RE = re.compile(r"^\|\s*To:\s*([^,|]+?)\s*,", re.MULTILINE)
PO_UEN_RE = re.compile(r"UEN:\s*([A-Z0-9]+)")
# "1. Total Order Value: **S$430,000.00** only."
PO_TOTAL_RE = re.compile(r"Total Order Value:\s*\*\*S\$([\d,]+\.\d{2})\*\*")
PO_OFFICER_RE = re.compile(
    r"Contact Person:\s*(?:Mr|Ms|Mrs|Dr)\s+([^,|]+),\s*Procurement Officer"
)
# Annex C: "| 1 | Advance Payment | SGD | 43,000.00 | 1. Electronic Invoice |"
PAY_EVENT_RE = re.compile(
    r"^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*SGD\s*\|\s*([\d,]+\.\d{2})\s*\|", re.MULTILINE
)


def normalise_ref(value: str | None) -> str:
    """A reference number as bare digits.

    Invoices print the reference every which way — "PO 1000672008",
    "1000672008", "Contract/PO No. 1000672008" — and the VLM transcribes what it
    sees. Comparing digits is the only stable form.
    """
    return re.sub(r"\D", "", str(value or ""))


class PaymentEvent(BaseModel):
    """One row of the purchase order's Annex C payment schedule."""

    no: int
    description: str = ""
    amount: float = 0.0


class PoFacts(BaseModel):
    """What a purchase order says, in the fields worth searching on."""

    ref_no: str = ""
    vendor: str = ""
    vendor_uen: str = ""
    officer: str = ""
    total_value: float = 0.0
    item_names: list[str] = []
    payment_events: list[PaymentEvent] = []


class SowFacts(BaseModel):
    """What a scope of work says. ``ref_no`` comes from the filename, not here."""

    ref_no: str = ""
    item_names: list[str] = []


def _amount(text: str) -> float:
    try:
        return float(text.replace(",", ""))
    except (ValueError, AttributeError):
        return 0.0


def _first(pattern: re.Pattern[str], content: str) -> str:
    match = pattern.search(content or "")
    return match.group(1).strip() if match else ""


def _table_rows(content: str, heading: str, min_cols: int = 4) -> list[list[str]]:
    """The rows of the first markdown table after ``heading``.

    Stops at the first line that is not a table row, so a table is not allowed
    to run on into the prose that follows it.
    """
    start = content.find(heading)
    if start == -1:
        return []
    rows: list[list[str]] = []
    started = False
    for line in content[start:].splitlines()[1:]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            if started:
                break
            continue
        started = True
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < min_cols:
            continue
        if all(set(c) <= {"-", ":", " "} for c in cells if c):  # separator row
            continue
        rows.append(cells)
    return rows


def _clean(cell: str) -> str:
    """Strip the markdown emphasis the tables use for section headers."""
    return cell.replace("**", "").strip()


def parse_po(content: str) -> PoFacts:
    """Read a purchase order's identifying facts.

    Item names come from Annex A's item list. Rows whose quantity column is empty
    are section headers ("**1** | **Supply of Goods**"), not items, so they are
    skipped — otherwise the index fills up with headings.
    """
    items: list[str] = []
    for cells in _table_rows(content, "Table A-1", min_cols=6):
        name = _clean(cells[1]) if len(cells) > 1 else ""
        qty = _clean(cells[3]) if len(cells) > 3 else ""
        if not name or not qty or name.lower() in {"item", "s/n"}:
            continue
        if name not in items:
            items.append(name)

    events = [
        PaymentEvent(no=int(no), description=_clean(desc), amount=_amount(amt))
        for no, desc, amt in PAY_EVENT_RE.findall(content or "")
    ]

    return PoFacts(
        ref_no=normalise_ref(_first(PO_REF_RE, content)),
        vendor=_first(PO_VENDOR_RE, content),
        vendor_uen=_first(PO_UEN_RE, content),
        officer=_first(PO_OFFICER_RE, content),
        total_value=_amount(_first(PO_TOTAL_RE, content)),
        item_names=items,
        payment_events=events,
    )


def parse_sow(content: str) -> SowFacts:
    """Read a scope of work's schedule of items (Table A-1, first column)."""
    items: list[str] = []
    for cells in _table_rows(content, "Table A-1", min_cols=4):
        name = _clean(cells[0]) if cells else ""
        if not name or name.lower() in {"item", "s/n", "milestone"}:
            continue
        if name not in items:
            items.append(name)
    return SowFacts(
        ref_no=normalise_ref(_first(re.compile(r"Purchase Order No\.?\s*([0-9]{6,})"), content)),
        item_names=items,
    )
