"""Online price lookup via DuckDuckGo (the last-resort pricing source)."""

from __future__ import annotations

import re

from .logging_config import get_logger

log = get_logger("internet")

# Matches "S$1,299.00", "SGD 1299", "US$1299", "$1,299", etc.
_PRICE_RE = re.compile(
    r"(?:S\$|SGD|US\$|USD|\$)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.IGNORECASE
)


def _extract_price(text: str) -> float:
    for m in _PRICE_RE.finditer(text or ""):
        try:
            val = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if val > 0:
            return val
    return 0.0


def _search(query: str, max_results: int = 6) -> list[dict]:
    from ddgs import DDGS

    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results))


def query_item_price(item_name: str) -> float:
    """
    Query the price of an item from the internet.

    Args:
        item_name (str): The name of the item to query.

    Returns:
        float: The price of the item, or 0 if not found.
    """
    query = f"{item_name} price"
    try:
        results = _search(query)
    except Exception as exc:  # network / rate-limit -> treat as "not found"
        log.warning("price search failed for %r: %s", item_name, exc)
        return 0.0

    for r in results:
        price = _extract_price(f"{r.get('title', '')} {r.get('body', '')}")
        if price:
            log.info("price for %r: %.2f (source: %s)",
                     item_name, price, r.get("href", ""))
            return price

    log.info("no price found online for %r", item_name)
    return 0.0
