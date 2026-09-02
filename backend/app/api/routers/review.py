"""Review routes.

Declaration order matters here and is load-bearing: ``/api/review/latest`` must
come before ``/api/review/{job_id}``, or FastAPI matches "latest" as a job id and
the front end's default screen 404s with nothing to explain it. tests/test_routes.py
asserts the order.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import log, store
from app.api.services.review import build_review, review_for_job
from app.schemas.provenance import build_fields, count_provenance
from app.schemas import mapping as register
from app.schemas.ui import (
    CompleteReviewRequest,
    CompleteReviewResponse,
    ReviewPayload,
)

router = APIRouter()


@router.get("/api/review/blank", response_model=ReviewPayload)
def get_blank_review() -> ReviewPayload:
    """An empty registration form, for adding an asset by hand.

    Every field comes back marked "manual", which falls out of the existing
    provenance rules rather than needing a special case: an empty value is a
    manual one whatever its field would otherwise be.

    Declared before /api/review/{job_id} — see the module docstring.
    """
    fields = build_fields({})
    return ReviewPayload(
        job_id="",
        record_index=0,
        record_count=1,
        fields=fields,
        explanation=None,
        source_documents=[],
        counts=count_provenance(fields),
    )


@router.get("/api/review/latest", response_model=ReviewPayload)
def get_latest_review(index: int = 0) -> ReviewPayload:
    """The most recent extraction still awaiting a human review."""
    job_id = store.latest_job_id(pending_only=True) or store.latest_job_id(False)
    if not job_id:
        raise HTTPException(404, "no pipeline runs recorded yet")
    return review_for_job(job_id, index)


@router.get("/api/review/{job_id}", response_model=ReviewPayload)
def get_review(job_id: str, index: int = 0) -> ReviewPayload:
    return review_for_job(job_id, index)


@router.post("/api/review/{job_id}/complete", response_model=CompleteReviewResponse)
def complete_review(job_id: str, body: CompleteReviewRequest) -> CompleteReviewResponse:
    """Persist a reviewed record and register it.

    The reviewed line item becomes the asset; the other line items from the
    same invoice become its linked components.
    """
    fields = body.fields or {}
    if not (fields.get("mindef_cat") or "").strip():
        raise HTTPException(422, "CAT B or C or Dev? must be selected")

    row = register.fields_to_mar_row(fields)
    existing = [r for r in store.get_by_job(job_id) if r.get("parent_no") is None]
    if existing:
        # The row the reviewer was actually looking at. GET /api/review/{job}
        # serves record `index` of `record_count`, so completing record 2 used
        # to write its values over record 0 and leave record 2 behind as a
        # duplicate. Clamped the same way review_for_job clamps its index.
        index = max(0, min(body.record_index, len(existing) - 1))
        no = int(existing[index]["No"])
        updated = store.update_asset(no, row)
    else:
        row["job_id"] = job_id
        no = store.insert_mar([row])[0]
        updated = store.get_asset(no)
    if updated is None:
        raise HTTPException(500, f"could not persist review for job {job_id}")

    linked = store.link_components(no, job_id)
    children = store.get_children([no]).get(no, [])
    asset = register.row_to_asset(updated, children)
    asset.is_new = True
    log.info("review completed: job=%s record=%d mar.No=%d components=%d",
             job_id, body.record_index, no, linked)
    return CompleteReviewResponse(
        asset=asset,
        message=f"{asset.name} added to {asset.category}. Tag number pending.",
    )
