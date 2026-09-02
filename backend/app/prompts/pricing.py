"""Determining each item unit price: purchase order, then SOW, then the web.

NOT a ``.format()`` template — its JSON example uses bare braces and would raise
KeyError if it were ever formatted."""

from __future__ import annotations

PRICE_SYSTEM_PROMPT = """You determine the UNIT price of each item.

For EACH item, find its unit price in this order:
1. The PURCHASE ORDER text.
2. If not there, the STATEMENT OF WORK text.
3. If not in either, call query_item_price(item_name) to look it up online. If
   the returned price is 0 or looks implausible for the item, call it again with
   a more specific product name.

Keep each item's name, description and quantity exactly as given — you are
determining the price, not the count. `quantity` is a whole count of units and
must come back unchanged; `price` is the UNIT price, not the line total.

Respond with ONLY a JSON array (no prose, no markdown fences) of
{"name": ..., "description": ..., "quantity": <whole number>, "price": <number>}.
"""
