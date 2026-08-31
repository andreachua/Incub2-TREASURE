"""Seed the Postgres document store with mock IT purchase records.

Each line item is stored as its own row (keyed by project_id) so that:
  * get_by_project_id  -> returns every line item for the project, and
  * search_line_items  -> ranks those line items by vector similarity.

Run:  uv run scripts/seed_db.py            (embeds line items; needs torch)
      uv run scripts/seed_db.py --no-embed (skip embeddings; vector path off)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make ``src`` importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.store.postgres_store import PostgresStore  # noqa: E402

# project_id -> list of line items (the mock "purchase record / catalog")
MOCK_PROJECTS: dict[str, list[dict]] = {
    "PRJ-001": [
        {"name": "Dell Latitude 5540 Laptop", "description": "14in business laptop, Intel Core i7-1355U, 16GB RAM, 512GB SSD, Windows 11 Pro", "quantity": 10, "price": 1299.00},
        {"name": "Dell WD22TB4 Thunderbolt Dock", "description": "Thunderbolt 4 docking station, 130W, dual 4K support", "quantity": 10, "price": 299.00},
        {"name": "Dell UltraSharp U2723QE Monitor", "description": "27in 4K UHD IPS Black monitor with USB-C hub", "quantity": 10, "price": 579.00},
        {"name": "Logitech MX Keys Keyboard", "description": "Wireless illuminated keyboard", "quantity": 10, "price": 119.00},
        {"name": "Logitech MX Master 3S Mouse", "description": "Wireless performance mouse, 8K DPI", "quantity": 10, "price": 99.00},
        {"name": "Microsoft 365 Business Premium", "description": "Annual subscription license per user", "quantity": 10, "price": 264.00},
    ],
    "PRJ-002": [
        {"name": "Cisco Catalyst 9200 Switch", "description": "48-port PoE+ managed network switch, 4x10G uplinks", "quantity": 2, "price": 3499.00},
        {"name": "Ubiquiti UniFi U6-Pro Access Point", "description": "WiFi 6 dual-band ceiling access point", "quantity": 8, "price": 159.00},
        {"name": "APC Smart-UPS 1500VA", "description": "Line-interactive rackmount UPS, 1500VA/1000W", "quantity": 2, "price": 649.00},
        {"name": "Cat6 Ethernet Cable Box", "description": "305m box of Cat6 UTP cable, blue", "quantity": 3, "price": 89.00},
        {"name": "Synology DS923+ NAS", "description": "4-bay network attached storage, AMD Ryzen, 4GB ECC", "quantity": 1, "price": 599.00},
        {"name": "Seagate IronWolf 8TB HDD", "description": "NAS-rated 3.5in hard drive, 7200rpm", "quantity": 4, "price": 189.00},
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the asset document store.")
    parser.add_argument(
        "--no-embed",
        action="store_true",
        help="Skip embeddings (the optional pgvector shortlist stays disabled).",
    )
    args = parser.parse_args()

    embed_fn = None
    if not args.no_embed:
        try:
            from src.embeddings import embed_text

            embed_fn = embed_text
            print("Embeddings enabled (sentence-transformers).")
        except Exception as exc:  # torch missing, download failed, etc.
            print(f"Embeddings unavailable ({exc}); seeding without vectors.")

    store = PostgresStore()
    print(f"Connecting to {store.dsn}")
    store.init_schema()
    store.clear()

    total = 0
    for project_id, items in MOCK_PROJECTS.items():
        for item in items:
            embedding = None
            if embed_fn is not None:
                embedding = embed_fn(f"{item['name']} {item['description']}")
            store.upsert_document(
                project_id=project_id,
                title=item["name"],
                content=item,
                embedding=embedding,
            )
            total += 1
        print(f"  {project_id}: seeded {len(items)} line items")

    print(f"Done. Inserted {total} rows.")


if __name__ == "__main__":
    main()
