"""Storing an upload and putting a job on the queue.

Two routes share this (invoice and receipt), and it does three things with side
effects — validate, persist the bytes, enqueue — none of which are HTTP concerns
beyond the two 422s. It returns a JSONResponse rather than a model so the 201
body stays byte-identical to what the front end already parses.
"""

from __future__ import annotations

import json
import uuid

from fastapi import HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.api.deps import log, redis_client, settings, store
from app.core.config import VALID_SYSTEMS


async def store_and_enqueue(
    file: UploadFile, system: str, doc_type: str
) -> JSONResponse:
    """Store the upload in pgvector and enqueue a job stamped with ``doc_type``.

    ``doc_type`` is "invoice" (new split+price enrichment workflow) or "receipt"
    (straightforward wholesale-into-mar workflow); the consumer branches on it.
    """
    log.info("upload received: type=%s filename=%s content_type=%s system=%s",
             doc_type, file.filename, file.content_type, system)
    system = (system or "").strip().lower()
    if system not in VALID_SYSTEMS:
        log.warning("rejected upload: invalid system=%r", system)
        raise HTTPException(
            status_code=422,
            detail=f"system must be one of {list(VALID_SYSTEMS)}, got {system!r}",
        )

    data = await file.read()
    if not data:
        log.warning("rejected upload: empty file %s", file.filename)
        raise HTTPException(status_code=422, detail="uploaded file is empty")

    # 1. Store the receipt in pgvector, get its id.
    receipt_id = store.save_receipt(
        system=system,
        filename=file.filename,
        content_type=file.content_type,
        data=data,
    )

    # 2. Enqueue a Redis message that triggers the Stage 1 consumer / pipeline.
    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "system": system,
        "receipt_id": receipt_id,
        "type": doc_type,
    }
    redis_client.lpush(settings.redis_job_queue, json.dumps(job))
    log.info("enqueued job %s (receipt_id=%d, system=%s, type=%s) -> queue '%s' — 201",
             job_id, receipt_id, system, doc_type, settings.redis_job_queue)

    # 3. Respond 201 Created to the frontend.
    return JSONResponse(
        status_code=201,
        content={
            "job_id": job_id,
            "receipt_id": receipt_id,
            "system": system,
            "type": doc_type,
            "status": "queued",
        },
    )
