"""Splitting composite invoice lines ("table + chair") into individual products.

A ``.format()`` template — takes ``{po}``, ``{sow}`` and ``{items}``. Its JSON
example is brace-escaped (``{{"name": ...}}``) because of that."""

from __future__ import annotations

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

`quantity` is a WHOLE COUNT OF UNITS (1, 2, 15) — never a fraction and never a
component's share of the original line. If the documents do not say how many,
use 1.

Respond with ONLY a JSON array (no prose, no markdown fences). Each element:
{{"name": <product>, "description": <specs or "">, "quantity": <whole number>}}.
"""
