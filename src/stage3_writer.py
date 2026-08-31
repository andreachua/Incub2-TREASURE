"""Stage 3 — write categorized asset records to a JSON file."""

from __future__ import annotations

import json
from pathlib import Path

from .models import AssetRecord

# Exact output key order requested by the spec.
_KEYS = ("name", "description", "category", "quantity", "price")


def to_records(records: list[AssetRecord]) -> list[dict]:
    return [{k: getattr(r, k) for k in _KEYS} for r in records]


def write_assets(records: list[AssetRecord], out_path: str | Path) -> Path:
    """Validate and write the records to ``out_path`` as a pretty JSON list."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = to_records(records)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out
