"""Stage 1 — extract line items from a receipt (image or PDF) via a local VLM."""

from __future__ import annotations

import base64
import io
from pathlib import Path

from .json_utils import extract_json_list
from .llm import get_openai_client
from .models import RawLineItem

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif"}

EXTRACTION_PROMPT = (
    "You are an expert at reading receipts and purchase records. "
    "Extract every purchased line item from this document. "
    "Return ONLY a JSON array (no prose, no markdown fences). "
    "Each element must be an object with exactly these keys: "
    '"name" (short product name), "description" (any specs/details on the line, '
    'else ""), "quantity" (number), "price" (unit price as a number, no currency '
    "symbol). "
    "Ignore non-item lines such as subtotal, tax, discount, shipping, total, "
    "store info, and payment details. If a value is missing use 1 for quantity "
    "and 0 for price. Output [] if there are no line items."
)


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
        urls: list[str] = []
        for page in pages:
            buf = io.BytesIO()
            page.save(buf, format="PNG")
            urls.append(_image_bytes_to_data_url(buf.getvalue(), "image/png"))
        return urls

    if ext in IMAGE_EXTS:
        media = "image/jpeg" if ext in {".jpg", ".jpeg"} else f"image/{ext.lstrip('.')}"
        return [_image_bytes_to_data_url(path.read_bytes(), media)]

    raise ValueError(f"Unsupported receipt type: {ext} (expected image or .pdf)")


def _call_vlm(data_url: str) -> str:
    client = get_openai_client()
    from .config import get_settings

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


def extract_line_items(receipt_path: str | Path) -> list[RawLineItem]:
    """Extract and validate line items from every page of the receipt."""
    items: list[RawLineItem] = []
    for data_url in _load_page_data_urls(receipt_path):
        raw = _call_vlm(data_url)
        for entry in extract_json_list(raw):
            if not isinstance(entry, dict):
                continue
            try:
                item = RawLineItem(**entry)
            except Exception:
                continue
            if item.name:  # skip empty rows
                items.append(item)
    return items
