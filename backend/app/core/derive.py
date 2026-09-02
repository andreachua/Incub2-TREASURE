"""Small deterministic derivations that don't need an LLM call."""

from __future__ import annotations


def taggable_for_group(group: str) -> str:
    """Assets are physically tagged; Inventories/Expenses items are not."""
    return "Yes" if group == "Assets" else "No"


def asset_capitalisation_date(do_date: str, invoice_date: str) -> str:
    """Defaults to the delivery date, falling back to the invoice date."""
    return do_date or invoice_date
