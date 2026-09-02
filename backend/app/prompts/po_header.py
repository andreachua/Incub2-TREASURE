"""Procurement header extraction from a purchase order document.

A ``.format()`` template — takes ``{po}``."""

from __future__ import annotations

HEADER_PROMPT = """Extract procurement header details from the purchase order
text below. Return ONLY a JSON object (no prose, no markdown fences) with keys:
  "po_no": the Purchase Order Ref No. / Contract No. as printed; "" if absent.
  "vendor": the contractor/supplier company name (see Contractor's Particulars); "" if absent.
  "project": the named project this purchase supports, if stated; "" if absent.
  "period_contract": the period contract / blanket ordering agreement reference
    this order is raised against, if any section references one; "" if absent.
  "purchase_type": exactly one of "One-time purchase", "Period contract call-off",
    "Framework demand" — choose "Period contract call-off" if a period contract /
    blanket ordering agreement section applies, "Framework demand" if a framework
    agreement/DPS is referenced, else "One-time purchase".

=== PURCHASE ORDER ===
{po}
"""
