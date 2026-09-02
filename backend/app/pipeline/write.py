"""Stage 3 — persist categorized asset records to Postgres ("mar" table).

Also provides a JSON serialization helper (used for the API/Redis result payload
and the CLI's file output).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.logging_config import get_logger
from app.schemas.domain import AssetRecord

log = get_logger("stage3")

# JSON output key order. `group` (Assets/Inventories/Expenses) is derived from
# the chosen category per config/categories.<system>.yaml.
_KEYS = (
    "name", "description", "category", "group", "reasoning", "quantity", "price",
    "invoice_no", "po_no", "do_no", "do_date", "gl_date", "vendor", "project",
    "period_contract", "purchase_type", "serial_no", "taggable",
    "asset_capitalisation_date",
)


def to_records(records: list[AssetRecord]) -> list[dict]:
    return [{k: getattr(r, k) for k in _KEYS} for r in records]


def _to_mar_row(
    rec: AssetRecord, job_id: str | None = None, receipt_id: int | str | None = None,
    context_match: str | None = None,
) -> dict:
    """Map an AssetRecord onto the `mar` table columns.

    Fields not sourced from any document (asset_id, location, and the
    ERP/warehouse-assigned tag number/material number/receiving plant/SLOC) are
    left NULL — they require a separate lookup/integration, and the review form
    marks them for manual entry. ``status`` starts as "Pending review" and only
    becomes "Registered" once a human completes the review.
    """
    from app.schemas.mapping import STATUS_PENDING, default_custodian

    return {
        "asset_id": None,
        "context_match": context_match,
        "asset_model": rec.name,
        "serial_no": rec.serial_no or None,
        "description": rec.description,
        "quantity": rec.quantity,
        "location": None,
        "status": STATUS_PENDING,
        "price": rec.price,
        "main_category": rec.group,   # Assets / Inventories / Expenses
        "sub_category": rec.category,  # the specific category
        "reasoning": rec.reasoning,   # why this classification was chosen
        "invoice_no": rec.invoice_no or None,
        "po_no": rec.po_no or None,
        "do_no": rec.do_no or None,
        "do_date": rec.do_date or None,
        "gl_date": rec.gl_date or None,
        "vendor": rec.vendor or None,
        "project": rec.project or None,
        "period_contract": rec.period_contract or None,
        "purchase_type": rec.purchase_type or None,
        "taggable": rec.taggable or None,
        "asset_capitalisation_date": rec.asset_capitalisation_date or None,
        # Awaiting the reviewer / a downstream ERP lookup.
        "tag_no": None,
        "custodian": default_custodian(),
        "mindef_cat": None,
        "material_number": None,
        "receiving_plant": None,
        "receiving_sloc": None,
        "job_id": job_id,
        # The uploaded file these rows came from, so the review screen can serve
        # the original invoice back even after the Redis result has expired.
        "receipt_id": str(receipt_id) if receipt_id is not None else None,
        "parent_no": None,
    }


def write_assets_to_db(
    records: list[AssetRecord],
    job_id: str | None = None,
    receipt_id: int | str | None = None,
    context_match: Any = None,
) -> list[int]:
    """Insert the records into the Postgres `mar` table; returns the "No" ids.

    ``job_id`` ties the rows back to the pipeline run, so the review screen can
    reload them after the Redis result has expired; ``receipt_id`` ties them to
    the uploaded file so the source-document viewer can serve it.
    ``context_match`` records which purchase order the crawler matched and why,
    so the evidence survives the Redis result expiring.
    """
    from app.store.postgres_store import PostgresStore

    evidence = (
        json.dumps(context_match.model_dump(), ensure_ascii=False)
        if context_match is not None and getattr(context_match, "found", False)
        else None
    )
    log.info("Stage 3: writing %d record(s) to Postgres 'mar' ...", len(records))
    ids = PostgresStore().insert_mar(
        [_to_mar_row(r, job_id, receipt_id, evidence) for r in records]
    )
    log.info("Stage 3: inserted %d row(s) into mar (No=%s)", len(ids), ids)
    return ids


def write_assets(records: list[AssetRecord], out_path: str | Path) -> Path:
    """Write the records to ``out_path`` as a pretty JSON list (CLI convenience)."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = to_records(records)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out
