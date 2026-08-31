"""Asset Management Extraction Pipeline — CLI orchestrator.

Usage:
    uv run main.py --receipt samples/receipt1.png --project-id PRJ-001 \
        --out output/assets.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.stage1_extractor import extract_line_items
from src.stage2_categorizer import categorize
from src.stage3_writer import write_assets


def run(receipt: str, project_id: str, out: str) -> int:
    print(f"[stage1] Extracting line items from {receipt} ...")
    items = extract_line_items(receipt)
    print(f"[stage1] Extracted {len(items)} line item(s).")
    for i in items:
        print(f"         - {i.name} (qty={i.quantity}, price={i.price})")
    if not items:
        print("[stage1] No line items found; nothing to categorize.")

    print(f"[stage2] Categorizing against project {project_id} ...")
    records = categorize(items, project_id)
    print(f"[stage2] Produced {len(records)} categorized record(s).")
    for r in records:
        print(f"         - {r.name} -> {r.category}")

    print(f"[stage3] Writing {out} ...")
    path = write_assets(records, out)
    print(f"[stage3] Wrote {len(records)} record(s) to {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", required=True, help="Path to a receipt image or PDF.")
    parser.add_argument("--project-id", required=True, help="Project id used to look up context.")
    parser.add_argument(
        "--out",
        default="output/assets.json",
        help="Output JSON path (default: output/assets.json).",
    )
    args = parser.parse_args()

    if not Path(args.receipt).exists():
        print(f"error: receipt not found: {args.receipt}", file=sys.stderr)
        return 2

    return run(args.receipt, args.project_id, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
