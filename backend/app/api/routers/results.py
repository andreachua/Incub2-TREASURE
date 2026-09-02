"""Reading a pipeline run: the raw published result, and the poll-friendly status.

``/results/{job_id}` 404s until the consumer publishes; ``/api/jobs/{id}/status``
is always 200 so a client can poll an in-flight upload without generating 404
noise in the logs.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from app.api.deps import log, redis_client, settings, store
from app.schemas.ui import JobStatus

router = APIRouter()


@router.get("/results/{job_id}")
def get_result(job_id: str) -> dict:
    """Fetch a pipeline result once the consumer has published it (else 404)."""
    payload = redis_client.get(f"{settings.redis_result_prefix}{job_id}")
    if payload is None:
        log.debug("result not ready for job %s", job_id)
        raise HTTPException(status_code=404, detail="result not ready")
    log.info("served result for job %s", job_id)
    return json.loads(payload)
@router.get("/api/jobs/{job_id}/status", response_model=JobStatus)
def get_job_status(job_id: str) -> JobStatus:
    """Poll target for an upload. Always 200 — "not yet" is not an error."""
    payload = redis_client.get(f"{settings.redis_result_prefix}{job_id}")
    if payload is None:
        # Nothing published yet: still queued, unless rows already landed.
        if store.get_by_job(job_id):
            return JobStatus(job_id=job_id, status="ready")
        # How far the consumer has got, if it has picked the job up at all.
        # Absent means "not started" rather than an error, so "" is the answer
        # and the upload screen renders an empty checklist.
        stage = redis_client.get(f"{settings.redis_progress_prefix}{job_id}") or ""
        return JobStatus(job_id=job_id, status="queued", stage=stage)
    result = json.loads(payload)
    if result.get("status") == "error":
        return JobStatus(job_id=job_id, status="error",
                         error=result.get("error") or "pipeline failed")
    return JobStatus(job_id=job_id, status="ready", count=result.get("count", 0))
