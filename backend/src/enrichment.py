"""Item enrichment between extraction and categorization.

Steps, all grounded in the invoice's matching PO (and its SOW):

1. detect_and_decompose — a deepagents agent flags line items whose price does
   not make sense (a generic name with a very large price = a bundle) by
   comparing the invoice price against the expected price found via
   PO -> SOW -> query_item_price, then breaks such lines into individual items.
2. split_items — an LLM breaks explicit composite lines (e.g. "table + chair")
   into individual products, using the PO first, then the SOW.
3. price_items — a deepagents agent determines each item's unit price:
   PO first, then SOW, else the ``query_item_price`` web-search tool (retrying
   when the result is unsatisfactory).

The invoice's PO reference (extracted in Stage 1) is matched against the ``po``
table (po.ref_no); the SOW is fetched via the same ref (sow.ref_no).
"""

from __future__ import annotations

import json
import time

from langchain_core.tools import tool

from .internet_tool import query_item_price
from .json_utils import extract_json_list
from .llm import get_openai_client
from .logging_config import get_logger
from .models import RawLineItem
from .store.postgres_store import PostgresStore

log = get_logger("enrich")

_store = PostgresStore()
_MAX_DOC_CHARS = 8000  # cap PO/SOW text sent to the model


# --------------------------------------------------------------------------- #
# Context (PO -> SOW) resolved by the invoice's PO ref
# --------------------------------------------------------------------------- #
def resolve_context(po_ref: str | None) -> tuple[str, str]:
    """Return (po_text, sow_text) for a PO ref (empty strings if unavailable)."""
    if not po_ref:
        log.info("no PO ref on the invoice — enriching without PO/SOW context")
        return "", ""
    po_rows = _store.get_po_by_ref(po_ref)
    sow_row = _store.get_sow_by_ref(po_ref)
    po_text = "\n\n".join(r["content"] for r in po_rows)[:_MAX_DOC_CHARS]
    sow_text = (sow_row["content"] if sow_row else "")[:_MAX_DOC_CHARS]
    log.info("resolved context for ref %s: PO %s, SOW %s",
             po_ref, "found" if po_rows else "missing",
             "found" if sow_row else "missing")
    return po_text, sow_text


def _chat(prompt: str) -> str:
    from .config import get_settings

    resp = get_openai_client().chat.completions.create(
        model=get_settings().llm_model,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content or ""


# --------------------------------------------------------------------------- #
# Step 1 — split composite items (plain LLM, PO then SOW as context)
# --------------------------------------------------------------------------- #
SPLIT_PROMPT = """You clean up invoice line items into individual products.

Some items bundle several distinct products, e.g. "table + chair",
"laptop and dock", "monitor / stand". The separator varies (+, /, ',', 'and',
'&', 'with', etc.). Split every such bundle into separate items. Leave items
that are already a single product unchanged.

Use the PURCHASE ORDER first, then the STATEMENT OF WORK, to identify the correct
component names and quantities. Do NOT invent items that aren't implied by the
line.

=== PURCHASE ORDER ===
{po}

=== STATEMENT OF WORK ===
{sow}

=== EXTRACTED LINE ITEMS ===
{items}

Respond with ONLY a JSON array (no prose, no markdown fences). Each element:
{{"name": <product>, "description": <specs or "">, "quantity": <number>}}.
"""


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
        entries = extract_json_list(_chat(prompt))
        out: list[RawLineItem] = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            try:
                item = RawLineItem(**e)
            except Exception:
                continue
            if item.name:
                out.append(item)
        if out:
            log.info("split: %d -> %d item(s) in %.2fs",
                     len(items), len(out), time.perf_counter() - t0)
            return out
        log.warning("split produced nothing usable; keeping original items")
    except Exception as exc:
        log.warning("split failed (%s); keeping original items", exc)
    return items


# --------------------------------------------------------------------------- #
# Shared web-price tool (used by both the decompose and price agents)
# --------------------------------------------------------------------------- #
@tool
def query_item_price_tool(item_name: str) -> str:
    """Look up an item's unit price online (DuckDuckGo).

    Use ONLY when the price is not in the PO or SOW. Returns JSON
    {"item_name", "price"} where price is 0.0 if nothing was found — in that
    case try again with a more specific product name.
    """
    price = query_item_price(item_name)
    return json.dumps({"item_name": item_name, "price": price})


# --------------------------------------------------------------------------- #
# Step 1b — price-sanity check + generic-bundle decomposition (deepagents)
# --------------------------------------------------------------------------- #
DECOMPOSE_SYSTEM_PROMPT = """You review invoice line items for price plausibility
and break down bundles into individual items.

Some invoice lines are a single GENERIC item (e.g. "equipment package",
"integrated system", "miscellaneous supplies", "advance payment") tagged with a
VERY LARGE price — really a bundle of several products, not one item.

Do NOT split an item unnecessarily. For EACH item you are given (its invoice
name, quantity and unit price):
1. First check whether the item already exists as a single INDIVIDUAL item in the
   STATEMENT OF WORK (one matching article) — or, failing that, in the PURCHASE
   ORDER. If it does AND the invoice price is consistent with that item's price in
   the SOW (roughly comparable, not far off), then it is NOT a bundle: KEEP it
   UNCHANGED and do not split it, even if the price is large.
2. Otherwise, judge whether the price makes sense: establish the item's EXPECTED
   unit price via (a) the PURCHASE ORDER, (b) the STATEMENT OF WORK,
   (c) query_item_price(name). The line is a BUNDLE when the invoice price is far
   larger than the expected price, or the name is generic with no matching single
   article.
3. If it is a BUNDLE, break it into the individual items it represents. Use the
   PURCHASE ORDER first, then the STATEMENT OF WORK, then query_item_price to
   identify the components and their quantities. Emit one object per component.
4. Otherwise (a plausible single item), KEEP the item UNCHANGED.

Return ONLY a JSON array (no prose, no markdown fences) of the full, possibly
expanded list. Each element: {{"name": <product>, "description": <specs or "">,
"quantity": <number>}}. Do NOT include a price.

=== PURCHASE ORDER ===
{po}

=== STATEMENT OF WORK ===
{sow}
"""


def _build_decompose_agent(po_text: str, sow_text: str):
    from deepagents import create_deep_agent

    from .llm import get_chat_model

    return create_deep_agent(
        model=get_chat_model(),
        tools=[query_item_price_tool],
        system_prompt=DECOMPOSE_SYSTEM_PROMPT.format(
            po=po_text or "(none)", sow=sow_text or "(none)"
        ),
    )


def detect_and_decompose(
    items: list[RawLineItem], po_text: str, sow_text: str
) -> list[RawLineItem]:
    """Flag generic/implausibly-priced lines and break them into individual items.

    Detection compares each line's invoice price against the expected price found
    via PO -> SOW -> query_item_price. Only lines priced at/above
    ``price_sanity_threshold`` are checked; the rest pass through untouched.
    """
    if not items:
        return []
    from .config import get_settings

    threshold = get_settings().price_sanity_threshold
    candidates = [i for i in items if i.price >= threshold]
    passthrough = [i for i in items if i.price < threshold]
    if not candidates:
        log.info("detect: no line >= %.0f; skipping decomposition", threshold)
        return items

    t0 = time.perf_counter()
    log.info("detect: checking %d line(s) >= %.0f for generic-bundle decomposition",
             len(candidates), threshold)
    try:
        agent = _build_decompose_agent(po_text, sow_text)
        payload = "=== ITEMS TO CHECK ===\n" + json.dumps(
            [i.model_dump() for i in candidates], indent=2
        )
        result = agent.invoke({"messages": [{"role": "user", "content": payload}]})
        decomposed = _coerce_items(extract_json_list(_last_message_text(result)))
        if decomposed:
            out = decomposed + passthrough
            log.info("detect: %d checked line(s) -> %d item(s) (+%d untouched) in %.2fs",
                     len(candidates), len(decomposed), len(passthrough),
                     time.perf_counter() - t0)
            return out
        log.warning("detect: agent returned nothing usable; keeping items")
    except Exception as exc:
        log.warning("detect: agent failed (%s); keeping items", exc)
    return items


PRICE_SYSTEM_PROMPT = """You determine the UNIT price of each item.

For EACH item, find its unit price in this order:
1. The PURCHASE ORDER text.
2. If not there, the STATEMENT OF WORK text.
3. If not in either, call query_item_price(item_name) to look it up online. If
   the returned price is 0 or looks implausible for the item, call it again with
   a more specific product name.

Keep each item's name, description and quantity. Respond with ONLY a JSON array
(no prose, no markdown fences) of
{"name": ..., "description": ..., "quantity": <number>, "price": <number>}.
"""


def _price_payload(items: list[RawLineItem], po_text: str, sow_text: str) -> str:
    return (
        "=== PURCHASE ORDER ===\n" + (po_text or "(none)") +
        "\n\n=== STATEMENT OF WORK ===\n" + (sow_text or "(none)") +
        "\n\n=== ITEMS TO PRICE ===\n" +
        json.dumps([i.model_dump() for i in items], indent=2)
    )


def _build_price_agent():
    from deepagents import create_deep_agent

    from .llm import get_chat_model

    return create_deep_agent(
        model=get_chat_model(),
        tools=[query_item_price_tool],
        system_prompt=PRICE_SYSTEM_PROMPT,
    )


def _last_message_text(result: dict) -> str:
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for msg in reversed(messages):
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            joined = "".join(b.get("text", "") for b in content if isinstance(b, dict))
            if joined.strip():
                return joined
    return ""


def price_items(
    items: list[RawLineItem], po_text: str, sow_text: str
) -> list[RawLineItem]:
    if not items:
        return []
    t0 = time.perf_counter()
    try:
        agent = _build_price_agent()
        result = agent.invoke(
            {"messages": [{"role": "user",
                           "content": _price_payload(items, po_text, sow_text)}]}
        )
        priced = _coerce_items(extract_json_list(_last_message_text(result)))
        if priced:
            log.info("priced %d item(s) via agent in %.2fs",
                     len(priced), time.perf_counter() - t0)
            return priced
        log.warning("price agent returned nothing usable; keeping items unpriced")
    except Exception as exc:
        log.warning("price agent failed (%s); keeping items unpriced", exc)
    return items


def _coerce_items(entries: list) -> list[RawLineItem]:
    out: list[RawLineItem] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        try:
            item = RawLineItem(**e)
        except Exception:
            continue
        if item.name:
            out.append(item)
    return out


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _combined_total(items: list[RawLineItem]) -> float:
    """Sum of line totals (unit price x quantity)."""
    return sum(i.price * i.quantity for i in items)


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


def enrich_items(items: list[RawLineItem], po_ref: str | None) -> list[RawLineItem]:
    """Detect implausibly-priced generic bundles and decompose them, split
    remaining composites, then price everything (PO -> SOW -> web).

    ``detect_and_decompose`` runs first because it compares against the invoice
    price, which ``split_items`` discards. The combined enriched total is finally
    capped to the invoice's own line-item total (parts cannot exceed the whole).
    """
    if not items:
        return []
    invoice_total = _combined_total(items)                  # from the original invoice lines
    po_text, sow_text = resolve_context(po_ref)
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
