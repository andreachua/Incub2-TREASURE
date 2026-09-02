"""The five tools the document crawler is allowed to use.

Narrow and typed, never SQL. Three reasons, in order of weight:

* The whole corpus is 25 orders, and ``list_po_index()`` renders all of them in
  about 2.5 KB — smaller than a single purchase order. There is nothing to search
  that the model cannot simply read, so SQL buys expressiveness for a problem
  that has none.
* ``PostgresStore._connect()`` opens a read/write connection. Free-form SQL from
  a 31B model on that handle would need a second read-only role, statement
  timeouts and an allow-list before it were safe.
* Typed results carry labels the agent can quote as evidence; a result set does not.

Note what is deliberately absent: nothing here returns a document body. The
agent's job is to choose a reference, not to read 8 KB of markdown per call —
that is how a small model loses the thread. The full text is fetched once, by
``pipeline.enrich.load_match_texts``, after the reference is settled.
"""

from __future__ import annotations

import json

from langchain_core.tools import tool

from app.core.logging_config import get_logger
from app.ingest.parse import normalise_ref, parse_sow
from app.store.postgres_store import PostgresStore

log = get_logger("tools.procurement")

_store = PostgresStore()


def _events(raw: str | None) -> list[dict]:
    try:
        return json.loads(raw) if raw else []
    except (ValueError, TypeError):
        return []


def index_card(row: dict) -> str:
    """One purchase order as a compact, prompt-ready card."""
    events = _events(row.get("payment_events"))
    parts = [
        f"{row.get('ref_no') or '?'} | {row.get('category') or ''} | "
        f"{row.get('vendor') or 'unknown vendor'}"
    ]
    if row.get("vendor_uen"):
        parts[0] += f" (UEN {row['vendor_uen']})"
    total = row.get("total_value")
    line = f"  total S${float(total):,.2f}" if total else "  total unknown"
    if events:
        line += " | events: " + ", ".join(
            f"{e.get('no')}={float(e.get('amount', 0)):,.2f}" for e in events
        )
    parts.append(line)
    if row.get("item_names"):
        parts.append(f"  items: {row['item_names']}")
    return "\n".join(parts)


def format_index() -> str:
    """Every purchase order in the corpus, as cards. ~2.5 KB for 25 orders."""
    rows = _store.list_po_index()
    if not rows:
        return "(the purchase-order index is empty)"
    return "\n".join(index_card(r) for r in rows)


def po_summary(ref: str) -> str:
    """One purchase order's card, with its payment schedule spelled out."""
    row = _store.get_po_index(normalise_ref(ref))
    if not row:
        return json.dumps({"ref_no": ref, "found": False})
    events = _events(row.get("payment_events"))
    return json.dumps({
        "ref_no": row.get("ref_no"),
        "found": True,
        "vendor": row.get("vendor"),
        "vendor_uen": row.get("vendor_uen"),
        "officer": row.get("officer"),
        "category": row.get("category"),
        "total_value": float(row["total_value"]) if row.get("total_value") else None,
        "items": (row.get("item_names") or "").split(" | ") if row.get("item_names") else [],
        "payment_events": events,
    }, ensure_ascii=False)


def sow_summary(ref: str) -> str:
    """One scope of work's schedule of items — never its full text."""
    ref = normalise_ref(ref)
    row = _store.get_sow_by_ref(ref)
    if not row:
        return json.dumps({"ref_no": ref, "found": False})
    facts = parse_sow(row.get("content") or "")
    return json.dumps({
        "ref_no": ref,
        "found": True,
        "filename": row.get("filename"),
        "category": (row.get("category") or "").replace("_", " ").strip(),
        "items": facts.item_names,
    }, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# The tools themselves
# --------------------------------------------------------------------------- #

@tool
def list_po_index() -> str:
    """Every purchase order in the corpus, one compact card each.

    Each card gives the reference number, the category, the contractor and UEN,
    the total order value, the payment events with their amounts, and the item
    names. This is the COMPLETE corpus, not a sample. Always call this first.
    """
    return format_index()


@tool
def find_po_by_ref(ref: str) -> str:
    """Look up a purchase order by reference number, tolerating misread digits.

    Tries an exact match, then a prefix/suffix match, then an edit distance of
    up to 2 — so "1000672OO8" still finds "1000672008". Returns JSON
    {"query", "matches": [{"ref_no", "distance", "exact", "vendor",
    "category", "total_value"}]}. An empty match list means no such reference.
    """
    query = normalise_ref(ref)
    matches = []
    for row in _store.find_po_refs_like(query):
        card = _store.get_po_index(row["ref_no"]) or {}
        matches.append({
            "ref_no": row["ref_no"],
            "distance": row.get("distance"),
            "exact": bool(row.get("exact")),
            "vendor": card.get("vendor"),
            "category": card.get("category") or row.get("category"),
            "total_value": float(card["total_value"]) if card.get("total_value") else None,
        })
    return json.dumps({"query": query, "matches": matches}, ensure_ascii=False)


@tool
def search_corpus(query: str, kind: str = "both", limit: int = 5) -> str:
    """Free-text search over purchase-order and scope-of-work bodies.

    Use for a vendor name, an item description, or a distinctive phrase copied
    off the invoice. ``kind`` is "po", "sow" or "both". Returns JSON
    [{"ref_no", "kind", "score"}], best first.
    """
    kind = (kind or "both").lower()
    out: list[dict] = []
    if kind in ("po", "both"):
        out += [{**r, "kind": "po"} for r in _store.search_po_content(query, limit)]
    if kind in ("sow", "both"):
        out += [{**r, "kind": "sow"} for r in _store.search_sow_content(query, limit)]
    for r in out:
        r["score"] = round(float(r.get("score") or 0.0), 3)
        r.pop("filename", None)
    out.sort(key=lambda r: r["score"], reverse=True)
    return json.dumps(out[: limit * 2], ensure_ascii=False)


@tool
def get_po_summary(ref: str) -> str:
    """One purchase order in full detail: contractor, UEN, procurement officer,
    total value, every item, and every payment event with its amount.

    Use this to confirm a candidate before committing to it.
    """
    return po_summary(ref)


@tool
def get_sow_summary(ref: str) -> str:
    """The scope of work filed under a reference: its category and its schedule
    of items.

    Use this to check that the items on the invoice really belong to this
    contract.
    """
    return sow_summary(ref)


CRAWLER_TOOLS = [
    list_po_index,
    find_po_by_ref,
    search_corpus,
    get_po_summary,
    get_sow_summary,
]
