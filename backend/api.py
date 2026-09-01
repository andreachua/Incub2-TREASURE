"""FastAPI server: receive a receipt/invoice upload, store it in pgvector, and
enqueue a Redis job that triggers the pipeline worker.

Flow
----
frontend --POST /receipts (file + system)--> API
  -> store receipt in pgvector (receipts table) -> receipt_id
  -> LPUSH Redis message {job_id, system, receipt_id} onto "object-categorization"
  -> 201 Created {job_id, receipt_id, system, status: "queued"}
The Stage 1 consumer (src/stage1_extractor.py: run_consumer) reads the message,
fetches the receipt by id, and runs the pipeline with the system's category set
(oxn / ehab). Run it with:  uv run python -m src.stage1_extractor

Run:  uv run uvicorn api:app --host 0.0.0.0 --port 8000
  or  uv run api.py
"""

from __future__ import annotations

import json
import uuid

import redis
from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.config import VALID_SYSTEMS, get_settings
from src.logging_config import get_logger
from src.store.postgres_store import PostgresStore

log = get_logger("api")
settings = get_settings()
store = PostgresStore()
redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)

app = FastAPI(title="Asset Pipeline API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # hackathon: allow the frontend from any origin
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    log.info("API starting: db=%s redis=%s queue=%s model=%s",
             settings.postgres_dsn.rsplit("@", 1)[-1], settings.redis_url,
             settings.redis_job_queue, settings.llm_model)
    # Ensure the receipts / project_documents / mar tables exist.
    store.init_schema()
    log.info("API ready")


@app.get("/health")
def health() -> dict:
    db_ok = True
    try:
        store.get_receipt(-1)  # cheap round-trip
    except Exception:
        db_ok = False
    try:
        redis_ok = bool(redis_client.ping())
    except Exception:
        redis_ok = False
    return {"status": "ok", "db": db_ok, "redis": redis_ok}

async def _store_and_enqueue(
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


@app.post("/invoice", status_code=201)
async def upload_invoice(
    file: UploadFile,
    system: str = Form(..., description="Classification system: oxn or ehab"),
) -> JSONResponse:
    """Upload an invoice — runs the split + price enrichment workflow."""
    return await _store_and_enqueue(file, system, "invoice")


@app.post("/receipts", status_code=201)
async def upload_receipt(
    file: UploadFile,
    system: str = Form(..., description="Classification system: oxn or ehab"),
) -> JSONResponse:
    """Upload a receipt — extracts items straight into the asset register."""
    return await _store_and_enqueue(file, system, "receipt")


@app.get("/results/{job_id}")
def get_result(job_id: str) -> dict:
    """Fetch a pipeline result once the consumer has published it (else 404)."""
    payload = redis_client.get(f"{settings.redis_result_prefix}{job_id}")
    if payload is None:
        log.debug("result not ready for job %s", job_id)
        raise HTTPException(status_code=404, detail="result not ready")
    log.info("served result for job %s", job_id)
    return json.loads(payload)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=False)
