"""The bytes and text behind the review screen's source-document rail.

The invoice is the uploaded file itself, read back out of ``receipts``; the PO
and SOW are the procurement documents the loader put in ``po`` / ``sow``.

PDFs are rendered server-side to PNG on request (``?as=png&page=N``) so a viewer
never depends on a browser PDF plugin — poppler is already in the image for
Stage 1.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response

from app.api.deps import log, store

router = APIRouter()


# --------------------------------------------------------------------------- #
# Source documents — the bytes behind the review screen's rail.
#
# The invoice is the uploaded file itself, read back out of `receipts`; the PO
# and SOW are the procurement documents the loader put in `po` / `sow`. Until
# now nothing handed any of these back over HTTP.
# --------------------------------------------------------------------------- #



@router.get("/api/documents/receipt/{receipt_id}")
def get_receipt_document(
    receipt_id: int,
    page: int = Query(1, ge=1, description="PDF page to render (1-based)"),
    as_: str = Query("", alias="as", description="'png' renders a PDF page as PNG"),
) -> Response:
    """Serve an uploaded invoice/receipt back as bytes.

    Images stream as stored. A PDF streams as-is by default; with ``?as=png``
    the requested page is rendered to PNG (pdf2image + poppler are already in
    the image) so a viewer never depends on a browser PDF plugin.
    """
    row = store.get_receipt(receipt_id)
    if row is None:
        raise HTTPException(404, f"no receipt {receipt_id}")
    data: bytes = row["data"]
    content_type = row.get("content_type") or "application/octet-stream"
    filename = row.get("filename") or f"receipt-{receipt_id}"

    is_pdf = content_type == "application/pdf" or filename.lower().endswith(".pdf")
    if is_pdf and as_.lower() == "png":
        data, content_type = _pdf_page_png(data, page), "image/png"
        filename = f"{Path(filename).stem}-p{page}.png"

    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.get("/api/documents/receipt/{receipt_id}/info")
def get_receipt_info(receipt_id: int) -> dict:
    """What the viewer needs before rendering: type, name and PDF page count."""
    row = store.get_receipt(receipt_id)
    if row is None:
        raise HTTPException(404, f"no receipt {receipt_id}")
    filename = row.get("filename") or f"receipt-{receipt_id}"
    content_type = row.get("content_type") or "application/octet-stream"
    is_pdf = content_type == "application/pdf" or filename.lower().endswith(".pdf")
    pages = _pdf_page_count(row["data"]) if is_pdf else 1
    return {
        "receipt_id": receipt_id,
        "filename": filename,
        "content_type": content_type,
        "is_pdf": is_pdf,
        "pages": pages,
        "size": len(row["data"]),
    }


def _content_disposition(filename: str) -> str:
    """`inline` with an ASCII-safe filename plus RFC 5987 for the real one.

    The name comes from whatever the operator uploaded, so it can hold quotes
    or non-latin-1 characters that a raw header value cannot carry.
    """
    ascii_name = filename.encode("ascii", "replace").decode("ascii").replace('"', "'")
    return (f'inline; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(filename, safe='')}")


def _pdf_page_count(data: bytes) -> int:
    import tempfile

    from pdf2image import pdfinfo_from_path

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        return int(pdfinfo_from_path(tmp_path).get("Pages", 1))
    except Exception as exc:  # a malformed PDF should not break the viewer
        log.warning("could not read PDF page count: %s", exc)
        return 1
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass


def _pdf_page_png(data: bytes, page: int) -> bytes:
    """Render one page of a PDF to PNG bytes."""
    import io
    import tempfile

    from pdf2image import convert_from_path

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        images = convert_from_path(tmp_path, first_page=page, last_page=page, dpi=150)
        if not images:
            raise HTTPException(404, f"no page {page} in this document")
        buf = io.BytesIO()
        images[0].save(buf, format="PNG")
        return buf.getvalue()
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass


@router.get("/api/documents/po/{ref_no}")
def get_po_document(ref_no: str) -> dict:
    """The purchase order(s) filed under a PO reference (many PO -> 1 SOW)."""
    rows = store.get_po_by_ref(ref_no)
    if not rows:
        raise HTTPException(404, f"no purchase order for ref {ref_no}")
    return {
        "ref_no": ref_no,
        "documents": [
            {"filename": r["filename"], "category": r.get("category") or "",
             "content": r["content"]}
            for r in rows
        ],
    }


@router.get("/api/documents/sow/{ref_no}")
def get_sow_document(ref_no: str) -> dict:
    """The scope of work filed under a PO reference."""
    row = store.get_sow_by_ref(ref_no)
    if row is None:
        raise HTTPException(404, f"no scope of work for ref {ref_no}")
    return {
        "ref_no": ref_no,
        "documents": [
            {"filename": row["filename"], "category": row.get("category") or "",
             "content": row["content"]}
        ],
    }
