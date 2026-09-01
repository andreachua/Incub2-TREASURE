"""Stage 1 — extract line items from a receipt (image or PDF) via a local VLM.

This module is also the **pipeline entry point**: ``run_consumer()`` consumes the
Redis message produced by the FastAPI ``upload_receipt`` endpoint (from the
``object-categorization`` queue), fetches the receipt from pgvector, extracts the
line items (Stage 1), then drives Stage 2 (categorize) and Stage 3 (output).

Run the consumer:  uv run python -m src.stage1_extractor
"""

from __future__ import annotations

import base64
import io
import time
from pathlib import Path

import re

from .json_utils import extract_json, extract_json_list
from .llm import get_openai_client
from .logging_config import get_logger
from .models import RawLineItem

log = get_logger("stage1")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif"}

EXTRACTION_PROMPT = (
    "You are an expert at reading invoices, receipts and purchase records. "
    "Return ONLY a JSON object (no prose, no markdown fences) with two keys:\n"
    '  "po_ref": the Purchase Order reference number this document refers to '
    '(look for "PO", "Purchase Order", "Ref", "Contract No.", usually a number '
    'like 1000672008); use "" if none is present.\n'
    '  "items": an array of the purchased line items. Each element is an object '
    'with keys "name" (short product name), "description" (any specs/details on '
    'the line, else ""), "quantity" (number), "price" (unit price as a number, no '
    "currency symbol).\n"
    "Ignore non-item lines such as subtotal, tax, discount, shipping, total, "
    "store info, and payment details. If a value is missing use 1 for quantity "
    "and 0 for price. Use an empty array if there are no line items."
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


def _parse_page(raw: str) -> tuple[str | None, list[dict]]:
    """Parse a VLM response into (po_ref, item dicts). Tolerates array or object."""
    try:
        data = extract_json(raw)
    except ValueError:
        return None, []
    if isinstance(data, list):
        return None, [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        ref_raw = data.get("po_ref") or data.get("po_reference") or data.get("ref") or ""
        digits = re.sub(r"\D", "", str(ref_raw))
        items = data.get("items", [])
        return (digits or None), [d for d in items if isinstance(d, dict)]
    return None, []


def extract_invoice(receipt_path: str | Path) -> tuple[str | None, list[RawLineItem]]:
    """Extract the invoice's PO reference number and its line items via the VLM."""
    t0 = time.perf_counter()
    pages = _load_page_data_urls(receipt_path)
    log.info("Stage 1: extracting from %s (%d page(s)) via VLM ...",
             Path(receipt_path).name, len(pages))
    po_ref: str | None = None
    items: list[RawLineItem] = []
    for idx, data_url in enumerate(pages, 1):
        page_ref, entries = _parse_page(_call_vlm(data_url))
        if page_ref and not po_ref:
            po_ref = page_ref
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
    log.info("Stage 1: extracted %d line item(s), po_ref=%s in %.2fs",
             len(items), po_ref, time.perf_counter() - t0)
    return po_ref, items


def extract_line_items(receipt_path: str | Path) -> list[RawLineItem]:
    """Backwards-compatible helper: just the line items."""
    return extract_invoice(receipt_path)[1]


# =========================================================================== #
# Redis consumer — the pipeline entry point
# =========================================================================== #
# The message shape is exactly what api.upload_receipt enqueues:
#   {"job_id", "system", "receipt_id"}
# (receipt_path / receipt_b64 are also accepted for local testing).

_running = True


def _consumer_stop(*_a) -> None:
    global _running
    _running = False
    log.info("shutting down after current message ...")


def _write_temp(data: bytes, filename: str | None) -> str:
    import tempfile

    suffix = Path(filename or "receipt.png").suffix or ".png"
    fd = tempfile.NamedTemporaryFile(prefix="receipt_", suffix=suffix, delete=False)
    fd.write(data)
    fd.close()
    return fd.name


def _resolve_receipt(job: dict) -> tuple[str, bool, dict]:
    """Return (path, is_temp, meta). Supports receipt_id / receipt_path / receipt_b64."""
    from .store.postgres_store import PostgresStore

    if job.get("receipt_id") is not None:
        rid = int(job["receipt_id"])
        rec = PostgresStore().get_receipt(rid)
        if rec is None:
            raise ValueError(f"receipt id {rid} not found in pgvector")
        log.info("fetched receipt id=%d (%s, %d bytes) from pgvector",
                 rid, rec.get("filename"), len(rec["data"]))
        path = _write_temp(rec["data"], rec.get("filename"))
        return path, True, {"system": rec.get("system")}
    if job.get("receipt_path"):
        log.info("using receipt_path=%s", job["receipt_path"])
        return str(job["receipt_path"]), False, {}
    if job.get("receipt_b64"):
        log.info("using inline receipt_b64 (%s)", job.get("filename"))
        return _write_temp(base64.b64decode(job["receipt_b64"]), job.get("filename")), True, {}
    raise ValueError("job must include 'receipt_id', 'receipt_path' or 'receipt_b64'")


def process_job(job: dict) -> dict:
    """Run the full pipeline for one Redis message and return the result dict."""
    import uuid

    from .config import DEFAULT_SYSTEM
    from .stage2_categorizer import categorize
    from .stage3_writer import to_records, write_assets_to_db

    job_id = job.get("job_id") or uuid.uuid4().hex
    # "invoice" -> split + price enrichment; "receipt" -> straightforward wholesale.
    doc_type = (job.get("type") or "receipt").lower()
    log.info("=== job %s: start (type=%s) ===", job_id, doc_type)
    t0 = time.perf_counter()
    try:
        receipt_path, is_temp, meta = _resolve_receipt(job)
    except Exception as exc:
        log.error("job %s: could not resolve receipt: %s", job_id, exc)
        return {"job_id": job_id, "system": job.get("system"), "type": doc_type,
                "status": "error", "count": 0, "records": [], "mar_ids": [],
                "error": str(exc)}

    system = job.get("system") or meta.get("system") or DEFAULT_SYSTEM
    try:
        log.info("job %s: type=%s system=%s receipt=%s",
                 job_id, doc_type, system, receipt_path)
        po_ref, items = extract_invoice(receipt_path)     # Stage 1 (items + PO ref)

        if doc_type == "invoice":
            from .enrichment import enrich_items, reconcile

            items = enrich_items(items, po_ref)           # split composites + price
            asset_records = categorize(items, system)     # Stage 2 (category/group)
            asset_records = reconcile(asset_records, items)  # keep enriched price/qty
        else:  # "receipt" — extract items straight into the register
            asset_records = categorize(items, system)     # Stage 2 (category/group)

        mar_ids = write_assets_to_db(asset_records)       # Stage 3 -> Postgres "mar"
        records = to_records(asset_records)
        log.info("job %s: done in %.2fs — %d record(s), mar rows %s",
                 job_id, time.perf_counter() - t0, len(records), mar_ids)
        result = {"job_id": job_id, "system": system, "type": doc_type,
                  "status": "ok", "count": len(records), "records": records,
                  "mar_ids": mar_ids, "error": None}
    except Exception as exc:  # never crash the consumer on one bad message
        log.exception("job %s: pipeline failed", job_id)
        result = {"job_id": job_id, "system": system, "type": doc_type,
                  "status": "error", "count": 0, "records": [], "mar_ids": [],
                  "error": str(exc)}
    finally:
        if is_temp:
            try:
                Path(receipt_path).unlink()
            except OSError:
                pass
    return result


def _publish(client, s, result: dict) -> None:
    import json

    payload = json.dumps(result, ensure_ascii=False)
    key = f"{s.redis_result_prefix}{result['job_id']}"
    client.set(key, payload, ex=s.redis_result_ttl)
    client.publish(s.redis_results_channel, payload)
    out = Path("output") / f"{result['job_id']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload, encoding="utf-8")
    log.info("job %s: published result [%s] -> redis '%s', %s",
             result["job_id"], result["status"], key, out)


def run_consumer() -> None:
    """Consume messages from the ``object-categorization`` queue and run the pipeline."""
    import json
    import signal

    import redis

    from .config import get_settings

    signal.signal(signal.SIGINT, _consumer_stop)
    signal.signal(signal.SIGTERM, _consumer_stop)

    s = get_settings()
    client = redis.Redis.from_url(s.redis_url, decode_responses=True)
    client.ping()
    log.info("connected to redis %s", s.redis_url)
    log.info("waiting for messages on '%s' (Ctrl-C to stop)", s.redis_job_queue)

    while _running:
        item = client.brpop(s.redis_job_queue, timeout=2)  # (queue, value) or None
        if item is None:
            continue
        _, raw = item
        log.info("received message from '%s'", s.redis_job_queue)
        try:
            job = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning("skipping malformed message: %s", exc)
            continue
        _publish(client, s, process_job(job))


if __name__ == "__main__":
    run_consumer()
