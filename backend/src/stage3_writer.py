"""Stage 3 — persist categorized asset records to Postgres ("mar" table).

Also provides a JSON serialization helper (used for the API/Redis result payload
and the CLI's file output).
"""

from __future__ import annotations

import json
from pathlib import Path

from .logging_config import get_logger
from .models import AssetRecord

log = get_logger("stage3")

# JSON output key order. `group` (Assets/Inventories/Expenses) is derived from
# the chosen category per config/categories.<system>.yaml.
_KEYS = ("name", "description", "category", "group", "reasoning", "quantity", "price")


def to_records(records: list[AssetRecord]) -> list[dict]:
    return [{k: getattr(r, k) for k in _KEYS} for r in records]


def _to_mar_row(rec: AssetRecord) -> dict:
    """Map an AssetRecord onto the `mar` table columns.

    Fields not available from a receipt (asset_id, serial_no, location, status)
    are left NULL for now.
    """
    return {
        "asset_id": None,
        "asset_model": rec.name,
        "serial_no": None,
        "description": rec.description,
        "quantity": rec.quantity,
        "location": None,
        "status": None,
        "price": rec.price,
        "main_category": rec.group,   # Assets / Inventories / Expenses
        "sub_category": rec.category,  # the specific category
        "reasoning": rec.reasoning,   # why this classification was chosen
    }


def write_assets_to_db(records: list[AssetRecord]) -> list[int]:
    """Insert the records into the Postgres `mar` table; returns the "No" ids."""
    from .store.postgres_store import PostgresStore

    log.info("Stage 3: writing %d record(s) to Postgres 'mar' ...", len(records))
    ids = PostgresStore().insert_mar([_to_mar_row(r) for r in records])
    log.info("Stage 3: inserted %d row(s) into mar (No=%s)", len(ids), ids)
    return ids


def write_assets(records: list[AssetRecord], out_path: str | Path) -> Path:
    """Write the records to ``out_path`` as a pretty JSON list (CLI convenience)."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = to_records(records)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out
