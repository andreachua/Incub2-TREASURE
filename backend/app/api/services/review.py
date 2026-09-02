"""Assembling one review payload.

Three things live here rather than in a router because two routes need them and
because they mint API URLs — ``/api/documents/po/{ref}`` and friends — which is
HTTP-layer knowledge the pipeline has no business carrying.

``review_for_job`` reads Redis first and falls back to the stored ``mar`` rows,
so a review survives REDIS_RESULT_TTL expiring.
"""

from __future__ import annotations

import json
import re

from fastapi import HTTPException

from app.api.deps import redis_client, settings, store
from app.schemas import mapping as register
from app.schemas.provenance import build_explanation, build_fields, count_provenance
from app.schemas.ui import ContextMatchView, ReviewPayload, SourceDoc


def source_docs(
    values: dict[str, str], receipt_id: int | str | None = None
,
    match: ContextMatchView | None = None,
) -> list[SourceDoc]:
    """The rail's document list, derived from the references actually extracted.

    Each entry carries the API path that serves the document, so the review
    screen can open it. ``url`` stays empty when the reference was extracted
    but nothing is held for it (a delivery order, or a PO ref with no match).

    When the crawler resolved a match, its reference is used as the lookup key
    rather than the digits printed on the invoice — that is the point of the
    crawler, and it is what makes a corrected reference open the right document.
    """
    docs: list[SourceDoc] = []
    if values.get("invoice_no"):
        docs.append(SourceDoc(
            title=f"Invoice {values['invoice_no']}",
            meta=values.get("vendor", ""),
            kind="invoice",
            url=f"/api/documents/receipt/{receipt_id}" if receipt_id else "",
        ))
    po_no = (values.get("po_no") or "").strip()
    if po_no:
        # `po`/`sow` are keyed by the digits-only reference the loader pulled
        # out of the PO ("Purchase Order Ref No.: 1000672403"), whereas po_no is
        # whatever the invoice printed. Match on the digits.
        ref = (match.po_ref if match and match.po_ref else re.sub(r"\D", "", po_no))
        docs.append(SourceDoc(
            title=f"Purchase order {po_no}",
            meta=_match_meta(match) or values.get("period_contract", ""),
            kind="po",
            url=f"/api/documents/po/{ref}" if ref and store.get_po_by_ref(ref) else "",
        ))
        sow = store.get_sow_by_ref(ref) if ref else None
        if sow:
            docs.append(SourceDoc(
                title=f"Scope of work {po_no}",
                meta=(sow.get("category") or "").replace("_", " ").strip(),
                kind="sow",
                url=f"/api/documents/sow/{ref}",
            ))
    if values.get("do_no"):
        docs.append(SourceDoc(title=f"Delivery order {values['do_no']}",
                              meta=values.get("do_date", ""), kind="do"))
    return docs


def _match_meta(match: ContextMatchView | None) -> str:
    """One line under the PO entry saying how it was found and how sure we are."""
    if not match or not match.po_ref:
        return ""
    top = match.evidence[0].signal.replace("_", " ") if match.evidence else match.method
    return f"matched by {top} · confidence {match.confidence:.0%}"


def _match_view(raw: object) -> ContextMatchView | None:
    """A stored or published match payload as the UI model, or None."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict) or not raw.get("po_ref"):
        return None
    try:
        return ContextMatchView(**{
            k: raw[k] for k in
            ("po_ref", "sow_ref", "vendor", "confidence", "method", "reasoning",
             "evidence")
            if k in raw
        })
    except Exception:
        return None


def build_review(
    job_id: str, values: dict[str, str], reasoning: str, index: int, count: int,
    receipt_id: int | str | None = None,
    match: ContextMatchView | None = None,
) -> ReviewPayload:
    fields = build_fields(values)
    docs = source_docs(values, receipt_id, match)
    return ReviewPayload(
        job_id=job_id,
        record_index=index,
        record_count=count,
        fields=fields,
        explanation=build_explanation(
            values.get("category", ""), reasoning, len(docs)
        ),
        source_documents=docs,
        counts=count_provenance(fields),
        context_match=match,
    )


def review_for_job(job_id: str, index: int) -> ReviewPayload:
    """Build the review payload from Redis if it's still there, else from `mar`."""
    payload = redis_client.get(f"{settings.redis_result_prefix}{job_id}")
    if payload:
        result = json.loads(payload)
        records = result.get("records") or []
        if records:
            index = max(0, min(index, len(records) - 1))
            record = records[index]
            return build_review(
                job_id, register.record_to_values(record),
                record.get("reasoning", ""), index, len(records),
                result.get("receipt_id"),
                _match_view(result.get("context_match")),
            )

    # Redis result expired (REDIS_RESULT_TTL) — rebuild from the stored rows.
    rows = [r for r in store.get_by_job(job_id) if r.get("parent_no") is None]
    if not rows:
        raise HTTPException(404, f"no records for job {job_id}")
    index = max(0, min(index, len(rows) - 1))
    row = rows[index]
    # The stored evidence is why this survives REDIS_RESULT_TTL expiring.
    return build_review(
        job_id, register.mar_row_to_values(row),
        row.get("reasoning") or "", index, len(rows),
        row.get("receipt_id"),
        _match_view(row.get("context_match")),
    )
