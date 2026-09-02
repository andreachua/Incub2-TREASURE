"""Stage 1 — the vision prompt that reads an invoice or receipt.

Not a ``.format()`` template: it carries no placeholders and is sent verbatim."""

from __future__ import annotations

EXTRACTION_PROMPT = (
    "You are an expert at reading invoices, receipts and purchase records. "
    "Return ONLY a JSON object (no prose, no markdown fences) with these keys:\n"
    '  "po_ref": the Purchase Order reference number this document refers to '
    '(look for "PO", "Purchase Order", "Ref", "Contract No."; typically a '
    '10-digit number). Transcribe the digits exactly as printed — do not guess, '
    'complete or correct them. Use "" if none is present.\n'
    '  "po_no": the Purchase Order number exactly as printed on the document '
    '(may be formatted differently from po_ref); "" if none is present.\n'
    '  "invoice_no": the invoice number as printed; "" if none is present.\n'
    '  "invoice_date": the invoice/document date, as "YYYY-MM-DD" if you can '
    'determine it, else as printed; "" if none is present.\n'
    '  "vendor": the seller/supplier company name printed on the invoice '
    '(letterhead or "From"/"Bill From" field); "" if none is present.\n'
    '  "do_no": the Delivery Order number, if this document also states one '
    '(look for "DO", "Delivery Order", "D/O"); "" if none is present.\n'
    '  "do_date": the Delivery Order date, if stated; "" if none is present.\n'
    '  "payment_event": the payment event / milestone this invoice claims '
    'against, if stated (e.g. "Payment Event 1", "Event 2 - Delivery"); "" if '
    'none is present.\n'
    '  "items": an array of the purchased line items. Each element is an object '
    'with keys "name" (short product name), "description" (any specs/details on '
    'the line, else ""), "quantity" (number), "price" (unit price as a number, no '
    'currency symbol), "serial_no" (the item\'s serial number if printed on the '
    'line or an attached label, else "").\n'
    "Ignore non-item lines such as subtotal, tax, discount, shipping, total, "
    "store info, and payment details. If a value is missing use 1 for quantity "
    "and 0 for price. Use an empty array if there are no line items."
)
