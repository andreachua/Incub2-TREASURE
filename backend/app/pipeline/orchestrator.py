"""Running the three stages over one job.

``process_job`` is the single place that knows the order of the pipeline and how
the two document types differ: an "invoice" gets the PO/SOW enrichment path
(context resolution, header extraction, bundle decomposition, splitting,
pricing); a "receipt" goes straight from extraction to categorization.

It never raises. One malformed upload must not take down the consumer, so every
failure comes back as a result dict with ``status="error"`` and the message.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

from app.core.logging_config import get_logger
from app.pipeline.extract import extract_invoice
from app.schemas.domain import ContextMatch, InvoiceHint, InvoiceHeader, RawLineItem

log = get_logger("stage1")

# The stages a job reports as it runs, in order. The operator watches these go
# by on the upload screen, so they are the boundaries worth naming rather than
# every function call: sub-second steps are folded into the stage they precede.
# The front end mirrors these keys with the design's labels — see
# frontend/streamlit_app/screens/progress.py. A "receipt" job skips "context"
# and "enrich" and simply never reports them.
STAGES = ("extract", "context", "enrich", "categorize", "write")


def _write_temp(data: bytes, filename: str | None) -> str:
    import tempfile

    suffix = Path(filename or "receipt.png").suffix or ".png"
    fd = tempfile.NamedTemporaryFile(prefix="receipt_", suffix=suffix, delete=False)
    fd.write(data)
    fd.close()
    return fd.name


def _resolve_receipt(job: dict) -> tuple[str, bool, dict]:
    """Return (path, is_temp, meta). Supports receipt_id / receipt_path / receipt_b64."""
    from app.store.postgres_store import PostgresStore

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


def invoice_hint(
    po_ref: str | None, header: InvoiceHeader, items: list[RawLineItem]
) -> InvoiceHint:
    """Everything the crawler can match on, assembled from what Stage 1 already read.

    No second look at the document and no extra model call — the payment event
    and the totals came back in the same extraction response.
    """
    return InvoiceHint(
        po_ref=po_ref or "",
        po_no=header.po_no,
        vendor=header.vendor,
        invoice_no=header.invoice_no,
        invoice_date=header.invoice_date,
        total_amount=sum(i.price * i.quantity for i in items),
        payment_event=header.payment_event,
        item_names=[i.name for i in items][:12],
    )


def stamp_records(asset_records, items, header, proc, po_ref, match=None) -> None:
    """Stamp document-level + derived fields onto every asset record in place.

    Document-level fields (invoice/PO/DO details) are the same across every
    record from one job; ``serial_no`` is per-item and only overlaid when the
    record/item counts line up (same guarded pattern as ``enrichment.reconcile``).
    """
    from app.core.derive import asset_capitalisation_date, taggable_for_group

    cap_date = asset_capitalisation_date(header.do_date, header.invoice_date)
    for rec in asset_records:
        rec.invoice_no, rec.gl_date = header.invoice_no, header.invoice_date
        rec.do_no, rec.do_date = header.do_no, header.do_date
        # A crawler-corrected reference beats the digits read off the page: it
        # is the one that resolves in the register and lights up the rail.
        rec.po_no = (proc.po_no or header.po_no
                     or (match.po_ref if match and match.found else "")
                     or (po_ref or ""))
        rec.vendor = proc.vendor or header.vendor
        rec.project, rec.period_contract, rec.purchase_type = (
            proc.project, proc.period_contract, proc.purchase_type)
        rec.taggable = taggable_for_group(rec.group)
        rec.asset_capitalisation_date = cap_date
    if len(asset_records) == len(items):
        for rec, item in zip(asset_records, items):
            rec.serial_no = item.serial_no
    else:
        log.warning("record/item count mismatch (%d vs %d); not overlaying serial_no",
                    len(asset_records), len(items))


def process_job(job: dict, on_stage=None) -> dict:
    """Run the full pipeline for one Redis message and return the result dict.

    ``on_stage`` is called with each key in ``STAGES`` as the job reaches it, so
    a caller that has somewhere to publish progress can (the consumer writes it
    to Redis for the upload poll). It defaults to doing nothing, which keeps
    this function unaware of Redis and lets any other caller ignore progress
    entirely.
    """
    import uuid

    from app.core.config import DEFAULT_SYSTEM
    from app.pipeline.categorize import categorize
    from app.pipeline.write import to_records, write_assets_to_db

    job_id = job.get("job_id") or uuid.uuid4().hex
    # "invoice" -> split + price enrichment; "receipt" -> straightforward wholesale.
    doc_type = (job.get("type") or "receipt").lower()
    log.info("=== job %s: start (type=%s) ===", job_id, doc_type)
    t0 = time.perf_counter()

    def stage(name: str) -> None:
        """Report the stage about to start. Never fails the job over telemetry."""
        if on_stage is None:
            return
        try:
            on_stage(name)
        except Exception as exc:  # progress is cosmetic; the run is not
            log.warning("job %s: could not report stage %s: %s", job_id, name, exc)

    try:
        receipt_path, is_temp, meta = _resolve_receipt(job)
    except Exception as exc:
        log.error("job %s: could not resolve receipt: %s", job_id, exc)
        return {"job_id": job_id, "system": job.get("system"), "type": doc_type,
                "receipt_id": job.get("receipt_id"), "status": "error", "count": 0,
                "records": [], "mar_ids": [], "error": str(exc)}

    system = job.get("system") or meta.get("system") or DEFAULT_SYSTEM
    # The review screen serves the uploaded file back from this id.
    receipt_id = job.get("receipt_id") or meta.get("receipt_id")
    try:
        log.info("job %s: type=%s system=%s receipt=%s",
                 job_id, doc_type, system, receipt_path)
        stage("extract")
        po_ref, header, items = extract_invoice(receipt_path)  # Stage 1 (items + header)

        if doc_type == "invoice":
            from app.pipeline.enrich import (
                enrich_items,
                extract_po_header,
                load_match_texts,
                reconcile,
                resolve_context_match,
            )

            stage("context")
            match = resolve_context_match(po_ref, invoice_hint(po_ref, header, items))
            po_text, sow_text = load_match_texts(match)
            proc = extract_po_header(po_text)              # vendor/PO/contract/project
            stage("enrich")
            items = enrich_items(items, po_text, sow_text)  # split composites + price
            stage("categorize")
            asset_records = categorize(items, system)     # Stage 2 (category/group)
            asset_records = reconcile(asset_records, items)  # keep enriched price/qty
        else:  # "receipt" — extract items straight into the register
            from app.schemas.domain import ProcurementHeader

            proc = ProcurementHeader()  # no PO to resolve for a plain receipt
            match = ContextMatch()
            stage("categorize")
            asset_records = categorize(items, system)     # Stage 2 (category/group)

        stamp_records(asset_records, items, header, proc, po_ref, match)

        stage("write")
        mar_ids = write_assets_to_db(asset_records, job_id, receipt_id, match)  # Stage 3 -> Postgres "mar"
        records = to_records(asset_records)
        log.info("job %s: done in %.2fs — %d record(s), mar rows %s",
                 job_id, time.perf_counter() - t0, len(records), mar_ids)
        result = {"job_id": job_id, "system": system, "type": doc_type,
                  "receipt_id": receipt_id, "status": "ok", "count": len(records),
                  "records": records, "mar_ids": mar_ids,
                  "context_match": match.model_dump(), "error": None}
    except Exception as exc:  # never crash the consumer on one bad message
        log.exception("job %s: pipeline failed", job_id)
        result = {"job_id": job_id, "system": system, "type": doc_type,
                  "receipt_id": receipt_id, "status": "error", "count": 0,
                  "records": [], "mar_ids": [], "error": str(exc)}
    finally:
        if is_temp:
            try:
                Path(receipt_path).unlink()
            except OSError:
                pass
    return result

