"""Asset Management Extraction Pipeline — CLI orchestrator.

Usage:
    uv run main.py --receipt samples/receipt1.png --system oxn \
        --out output/assets.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.config import DEFAULT_SYSTEM, VALID_SYSTEMS
from src.enrichment import enrich_items, reconcile
from src.logging_config import get_logger
from src.stage1_extractor import extract_invoice
from src.stage2_categorizer import categorize
from src.stage3_writer import write_assets, write_assets_to_db

log = get_logger("cli")


def run(receipt: str, system: str, out: str) -> int:
    log.info("pipeline start: receipt=%s system=%s", receipt, system)
    po_ref, items = extract_invoice(receipt)       # Stage 1 (items + PO ref)
    if not items:
        log.warning("no line items found; nothing to categorize")

    items = enrich_items(items, po_ref)            # split composites + price
    records = categorize(items, system)            # Stage 2 (category/group)
    records = reconcile(records, items)            # keep enriched price/qty
    mar_ids = write_assets_to_db(records)          # Stage 3 -> Postgres 'mar'

    path = write_assets(records, out)
    log.info("pipeline done: %d record(s), mar rows %s, JSON -> %s",
             len(records), mar_ids, path)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", required=True, help="Path to a receipt image or PDF.")
    parser.add_argument(
        "--system",
        default=DEFAULT_SYSTEM,
        choices=VALID_SYSTEMS,
        help=f"Classification system (default: {DEFAULT_SYSTEM}).",
    )
    parser.add_argument(
        "--out",
        default="output/assets.json",
        help="Output JSON path (default: output/assets.json).",
    )
    args = parser.parse_args()

    if not Path(args.receipt).exists():
        print(f"error: receipt not found: {args.receipt}", file=sys.stderr)
        return 2

    return run(args.receipt, args.system, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
