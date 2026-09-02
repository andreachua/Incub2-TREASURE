"""Stage 1 — read line items and document header off an invoice via the local VLM.

Images go to the model directly; PDFs are rendered one page at a time (poppler
via pdf2image) and each page is sent as its own image, because the endpoint takes
images, not documents.

Header fields are merged across pages first-non-empty-wins: an invoice number
printed only on page 1 must survive a three-page document.

This module does extraction and nothing else. Driving the stages is
``pipeline/orchestrator.py``; consuming the queue is ``pipeline/consumer.py``.
"""

from __future__ import annotations

import base64
import io
import re
import time
from pathlib import Path

from app.core.json_utils import extract_json
from app.core.llm import get_openai_client
from app.core.logging_config import get_logger
from app.prompts.extraction import EXTRACTION_PROMPT
from app.schemas.domain import InvoiceHeader, RawLineItem

log = get_logger("stage1")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif"}

def _image_bytes_to_data_url(data: bytes, media_type: str = "image/png") -> str:
    b64 = base64.standard_b64encode(data).decode("ascii")
    return f"data:{media_type};base64,{b64}"


def _load_page_data_urls(receipt_path: str | Path) -> list[str]:
    """Return one base64 data URL per page/image of the receipt."""
    path = Path(receipt_path)
    if not path.exists():
        raise FileNotFoundError(f"Receipt not found: {path}")

    ext = path.suffix.lower()

    if ext == ".pdf":
        from pdf2image import convert_from_path

        pages = convert_from_path(str(path), dpi=200)
        log.info("PDF %s -> %d page(s)", path.name, len(pages))
        urls: list[str] = []
        for page in pages:
            buf = io.BytesIO()
            page.save(buf, format="PNG")
            urls.append(_image_bytes_to_data_url(buf.getvalue(), "image/png"))
        return urls

    if ext in IMAGE_EXTS:
        media = "image/jpeg" if ext in {".jpg", ".jpeg"} else f"image/{ext.lstrip('.')}"
        log.debug("image %s (%d bytes)", path.name, path.stat().st_size)
        return [_image_bytes_to_data_url(path.read_bytes(), media)]

    raise ValueError(f"Unsupported receipt type: {ext} (expected image or .pdf)")


def _call_vlm(data_url: str) -> str:
    client = get_openai_client()
    from app.core.config import get_settings

    resp = client.chat.completions.create(
        model=get_settings().llm_model,
        temperature=0,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": EXTRACTION_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
    )
    return resp.choices[0].message.content or ""


_HEADER_KEYS = ("po_no", "invoice_no", "invoice_date", "vendor", "do_no", "do_date",
                "payment_event")


def _parse_page(raw: str) -> tuple[str | None, dict[str, str], list[dict]]:
    """Parse a VLM response into (po_ref, header dict, item dicts).

    Tolerates the model returning a bare array of items instead of an object.
    """
    try:
        data = extract_json(raw, prefer="object")
    except ValueError:
        return None, {}, []
    if isinstance(data, list):
        return None, {}, [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        ref_raw = data.get("po_ref") or data.get("po_reference") or data.get("ref") or ""
        digits = re.sub(r"\D", "", str(ref_raw))
        header = {k: str(data.get(k) or "").strip() for k in _HEADER_KEYS}
        items = data.get("items", [])
        return (digits or None), header, [d for d in items if isinstance(d, dict)]
    return None, {}, []


def extract_invoice(
    receipt_path: str | Path,
) -> tuple[str | None, InvoiceHeader, list[RawLineItem]]:
    """Extract the invoice's PO reference, document header and line items via the VLM."""
    t0 = time.perf_counter()
    pages = _load_page_data_urls(receipt_path)
    log.info("Stage 1: extracting from %s (%d page(s)) via VLM ...",
             Path(receipt_path).name, len(pages))
    po_ref: str | None = None
    header_values: dict[str, str] = {}
    items: list[RawLineItem] = []
    for idx, data_url in enumerate(pages, 1):
        page_ref, page_header, entries = _parse_page(_call_vlm(data_url))
        if page_ref and not po_ref:
            po_ref = page_ref
        for key, value in page_header.items():
            if value and not header_values.get(key):
                header_values[key] = value
        page_items = 0
        for entry in entries:
            try:
                item = RawLineItem(**entry)
            except Exception:
                log.debug("skipping unparseable line item: %r", entry)
                continue
            if item.name:  # skip empty rows
                items.append(item)
                page_items += 1
        log.debug("page %d/%d -> %d item(s)", idx, len(pages), page_items)
    header = InvoiceHeader(**header_values)
    log.info("Stage 1: extracted %d line item(s), po_ref=%s, invoice_no=%s in %.2fs",
             len(items), po_ref, header.invoice_no, time.perf_counter() - t0)
    return po_ref, header, items


def extract_line_items(receipt_path: str | Path) -> list[RawLineItem]:
    """Backwards-compatible helper: just the line items."""
    return extract_invoice(receipt_path)[2]

