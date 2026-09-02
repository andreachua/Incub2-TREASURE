"""Upload routes: an invoice or a receipt in, a queued job out."""

from __future__ import annotations

from fastapi import APIRouter, Form, UploadFile
from fastapi.responses import JSONResponse

from app.api.services.uploads import store_and_enqueue

router = APIRouter()


@router.post("/invoice", status_code=201)
async def upload_invoice(
    file: UploadFile,
    system: str = Form(..., description="Classification system: oxn or ehab"),
) -> JSONResponse:
    """Upload an invoice — runs the split + price enrichment workflow."""
    return await store_and_enqueue(file, system, "invoice")


@router.post("/receipts", status_code=201)
async def upload_receipt(
    file: UploadFile,
    system: str = Form(..., description="Classification system: oxn or ehab"),
) -> JSONResponse:
    """Upload a receipt — extracts items straight into the asset register."""
    return await store_and_enqueue(file, system, "receipt")
