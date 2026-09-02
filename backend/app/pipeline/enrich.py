"""Item enrichment between extraction and categorization.

Steps, all grounded in the invoice's matching PO (and its SOW):

1. detect_and_decompose (agents.decomposer) — flags line items whose price does
   not make sense (a generic name with a very large price = a bundle) and breaks
   such lines into individual items.
2. split_items — an LLM breaks explicit composite lines (e.g. "table + chair")
   into individual products, using the PO first, then the SOW.
3. price_items (agents.pricer) — determines each item's unit price: PO first,
   then SOW, else a web lookup.

This module owns the orchestration and the two plain-LLM steps; the two agents
live in ``app/agents/``. The invoice's PO reference (extracted in Stage 1) is
matched against the ``po`` table (po.ref_no); the SOW is fetched via the same
ref (sow.ref_no).
"""

from __future__ import annotations

import json
import time

from app.agents.decomposer import detect_and_decompose
from app.agents.pricer import price_items
from app.core.json_utils import extract_json, extract_json_list
from app.core.llm import get_openai_client
from app.core.logging_config import get_logger
from app.prompts.po_header import HEADER_PROMPT
from app.prompts.split import SPLIT_PROMPT
from app.schemas.domain import (
    ContextMatch,
    InvoiceHint,
    ProcurementHeader,
    RawLineItem,
    coerce_line_items,
)
from app.store.postgres_store import PostgresStore

log = get_logger("enrich")

_store = PostgresStore()
_MAX_DOC_CHARS = 8000  # cap PO/SOW text sent to the model


# --------------------------------------------------------------------------- #
# Context (PO -> SOW), found by the crawler
# --------------------------------------------------------------------------- #
def resolve_context_match(
    po_ref: str | None, hint: InvoiceHint | None = None
) -> ContextMatch:
    """Which purchase order (and so which SOW) this invoice belongs to."""
    from app.agents.document_crawler import crawl_context

    return crawl_context(po_ref, hint)


def load_match_texts(match: ContextMatch) -> tuple[str, str]:
    """(po_text, sow_text) for a resolved match — the only place bodies are loaded.

    Deliberately not a tool: the crawler decides *which* documents, and code
    fetches them. Truncation is unchanged, so every downstream step sees exactly
    the shape of input it saw before the crawler existed.
    """
    if not match.found:
        return "", ""
    po_rows = _store.get_po_by_ref(match.po_ref)
    sow_row = _store.get_sow_by_ref(match.sow_ref or match.po_ref)
    po_text = "\n\n".join(r["content"] for r in po_rows)[:_MAX_DOC_CHARS]
    sow_text = (sow_row["content"] if sow_row else "")[:_MAX_DOC_CHARS]
    log.info("resolved context for ref %s: PO %s, SOW %s (%s, confidence %.2f)",
             match.po_ref, "found" if po_rows else "missing",
             "found" if sow_row else "missing", match.method, match.confidence)
    return po_text, sow_text


def resolve_context(
    po_ref: str | None, hint: InvoiceHint | None = None
) -> tuple[str, str]:
    """Return (po_text, sow_text) for an invoice (empty strings if unavailable).

    Same contract as before the crawler existed; it is simply better at finding
    the documents now. Callers that want the evidence should use
    ``resolve_context_match`` and ``load_match_texts`` instead.
    """
    return load_match_texts(resolve_context_match(po_ref, hint))


def _chat(prompt: str) -> str:
    from app.core.config import get_settings

    resp = get_openai_client().chat.completions.create(
        model=get_settings().llm_model,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content or ""


# --------------------------------------------------------------------------- #
# Procurement header — extracted from the Purchase Order document only
# --------------------------------------------------------------------------- #
_PURCHASE_TYPES = ("One-time purchase", "Period contract call-off", "Framework demand")



def extract_po_header(po_text: str) -> ProcurementHeader:
    """Extract vendor/PO/contract/project details from the PO document text."""
    if not po_text:
        return ProcurementHeader()
    try:
        data = extract_json(_chat(HEADER_PROMPT.format(po=po_text)),
                            prefer="object")
    except Exception as exc:
        log.warning("PO header extraction failed (%s); leaving header blank", exc)
        return ProcurementHeader()
    if not isinstance(data, dict):
        return ProcurementHeader()
    header = ProcurementHeader(**{k: data.get(k, "") for k in
                                   ("po_no", "vendor", "project", "period_contract",
                                    "purchase_type")})
    if header.purchase_type not in _PURCHASE_TYPES:
        header.purchase_type = "One-time purchase"
    return header


# --------------------------------------------------------------------------- #
# Step 1 — split composite items (plain LLM, PO then SOW as context)
# --------------------------------------------------------------------------- #


def split_items(
    items: list[RawLineItem], po_text: str, sow_text: str
) -> list[RawLineItem]:
    if not items:
        return []
    t0 = time.perf_counter()
    prompt = SPLIT_PROMPT.format(
        po=po_text or "(none)",
        sow=sow_text or "(none)",
        items=json.dumps([i.model_dump() for i in items], indent=2),
    )
    try:
        out = coerce_line_items(extract_json_list(_chat(prompt)))
        if out:
            log.info("split: %d -> %d item(s) in %.2fs",
                     len(items), len(out), time.perf_counter() - t0)
            return out
        log.warning("split produced nothing usable; keeping original items")
    except Exception as exc:
        log.warning("split failed (%s); keeping original items", exc)
    return items



# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _combined_total(items: list[RawLineItem]) -> float:
    """Sum of line totals (unit price x quantity)."""
    return sum(i.price * i.quantity for i in items)


def _warn_if_price_basis_is_ambiguous(items: list[RawLineItem]) -> None:
    """Say so when we cannot tell a unit price from a line total.

    Everything downstream assumes ``price`` is a *unit* price — the extraction
    prompt asks for one, and ``_combined_total`` multiplies by quantity. VLMs
    routinely return the line total instead, and when they do the invoice total
    is silently overstated by a factor of the quantity, which then scales every
    enriched price down through ``_cap_to_invoice_total``.

    The two readings only differ once something has quantity > 1, and nothing in
    the document tells us which one we got. So log both totals and let the run
    be checked, rather than guessing and correcting the wrong one.
    """
    if all(i.quantity == 1 for i in items):
        return          # the two readings coincide; nothing to disambiguate
    as_unit = _combined_total(items)
    as_line_total = sum(i.price for i in items)
    log.info("invoice total is %.2f reading price as a unit price, %.2f reading "
             "it as a line total; using the unit-price reading (as extracted)",
             as_unit, as_line_total)


def _cap_to_invoice_total(items: list[RawLineItem], invoice_total: float) -> list[RawLineItem]:
    """Ensure the combined price of the (possibly split) items does not exceed
    the invoice's own line-item total.

    Independent PO/SOW/web pricing can push the parts above what the invoice
    actually billed; when that happens, scale every unit price down
    proportionally so the combined total equals the invoice total.
    """
    if invoice_total <= 0 or not items:
        return items
    total = _combined_total(items)
    if total > invoice_total:
        factor = invoice_total / total
        for i in items:
            i.price = round(i.price * factor, 2)
        log.info("capped combined price %.2f -> %.2f (invoice total) via x%.4f",
                 total, invoice_total, factor)
    return items


def enrich_items(
    items: list[RawLineItem], po_text: str, sow_text: str
) -> list[RawLineItem]:
    """Detect implausibly-priced generic bundles and decompose them, split
    remaining composites, then price everything (PO -> SOW -> web).

    ``po_text``/``sow_text`` are the already-resolved documents (see
    ``resolve_context``) — callers that also need ``extract_po_header`` should
    resolve once and pass the same text into both.

    ``detect_and_decompose`` runs first because it compares against the invoice
    price, which ``split_items`` discards. The combined enriched total is finally
    capped to the invoice's own line-item total (parts cannot exceed the whole).
    """
    if not items:
        return []
    _warn_if_price_basis_is_ambiguous(items)
    invoice_total = _combined_total(items)                  # from the original invoice lines
    items = detect_and_decompose(items, po_text, sow_text)  # generic-bundle breakdown
    items = split_items(items, po_text, sow_text)           # separator composites
    items = price_items(items, po_text, sow_text)           # final PO->SOW->web pricing
    items = _cap_to_invoice_total(items, invoice_total)     # parts must not exceed the invoice
    return items


def reconcile(records: list, items: list[RawLineItem]) -> list:
    """Overlay the enriched price/quantity onto categorized records (index-aligned).

    Enrichment is authoritative for price/quantity; categorization only adds the
    category/group. Only applied when the counts match.
    """
    if len(records) == len(items):
        for rec, it in zip(records, items):
            rec.price = it.price
            rec.quantity = it.quantity
    else:
        log.warning("record/item count mismatch (%d vs %d); not overlaying prices",
                    len(records), len(items))
    return records
