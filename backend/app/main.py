"""FastAPI application: receive an upload, store it, and queue the pipeline.

Flow
----
frontend --POST /invoice (file + system)--> API
  -> store the bytes in pgvector (receipts table) -> receipt_id
  -> LPUSH {job_id, system, receipt_id, type} onto "object-categorization"
  -> 201 Created {job_id, receipt_id, system, type, status: "queued"}

The consumer (``app/pipeline/consumer.py``) reads the message, fetches the
receipt by id, and runs the pipeline with the system's category set (oxn / ehab).
Run it with:  uv run python -m app.pipeline.consumer

Router order is not arbitrary — see ``app/api/routers/review.py``.

Run:  uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.deps import log, settings, store
from app.api.paths import FRONTEND_DIR, STATIC_DIR
from app.api.routers import (
    documents,
    frontend,
    health,
    register,
    results,
    review,
    uploads,
)

app = FastAPI(title="Asset Pipeline API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # hackathon: allow the frontend from any origin
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registration order is the matching order. review must come before any router
# that could shadow its paths, and within it /api/review/latest is declared
# before /api/review/{job_id} — tests/test_routes.py asserts both.
app.include_router(health.router)
app.include_router(uploads.router)
app.include_router(results.router)
app.include_router(register.router)
app.include_router(review.router)
app.include_router(documents.router)
app.include_router(frontend.router)


@app.on_event("startup")
def _startup() -> None:
    log.info("API starting: db=%s redis=%s queue=%s model=%s",
             settings.postgres_dsn.rsplit("@", 1)[-1], settings.redis_url,
             settings.redis_job_queue, settings.llm_model)
    # Ensure the receipts / project_documents / mar tables exist.
    store.init_schema()
    # And the po/sow tables plus the searchable index they feed. Both containers
    # do this so a restart is all a deployment needs to pick up a schema change.
    store.init_po_sow_schema()
    log.info("API ready")


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
if FRONTEND_DIR.is_dir():
    app.mount("/app-assets", StaticFiles(directory=FRONTEND_DIR), name="app-assets")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
