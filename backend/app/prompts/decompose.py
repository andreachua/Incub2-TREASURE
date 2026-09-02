"""Price-sanity review and generic-bundle decomposition.

A ``.format()`` template — takes ``{po}`` and ``{sow}``. Its JSON example is
brace-escaped (``{{"name": ...}}``) because of that."""

from __future__ import annotations

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

`quantity` is a WHOLE COUNT OF UNITS — how many of that product were bought
(1, 2, 15). It is never a fraction, a percentage, or that component's share of
the bundle's price. If the documents do not say how many, use 1.

Return ONLY a JSON array (no prose, no markdown fences) of the full, possibly
expanded list. Each element: {{"name": <product>, "description": <specs or "">,
"quantity": <whole number>}}. Do NOT include a price.

=== PURCHASE ORDER ===
{po}

=== STATEMENT OF WORK ===
{sow}
"""
