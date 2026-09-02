"""Load data/sow and data/po markdown files into Postgres (tables `sow`, `po`).

- sow.ref_no  = the filename prefix before the first "_"  (e.g. 1000672008)
- po.ref_no   = the Purchase Order Ref No., extracted from the PO via the LLM;
                it is a FK to sow.ref_no (many POs -> 1 SOW).

SOWs are loaded first so the PO foreign keys resolve.

Run (host):       uv run python -m app.ingest.documents
Run (container):  docker compose run --rm loader
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from app.core.config import get_settings
from app.core.llm import get_openai_client
from app.core.logging_config import get_logger
from app.core.paths import REPO_ROOT
from app.ingest.parse import PO_REF_RE, normalise_ref, parse_po, parse_sow
from app.store.postgres_store import PostgresStore

log = get_logger("loader")


def _data_dir() -> Path:
    # DATA_DIR wins (used in the container); else the repo's data/ dir.
    env = os.getenv("DATA_DIR")
    if env:
        return Path(env)
    return REPO_ROOT / "data"


def _sow_ref_and_category(filename: str) -> tuple[str, str]:
    """ref_no = text before first '_'; category = the descriptive remainder."""
    stem = Path(filename).stem
    ref_no, _, rest = stem.partition("_")
    category = rest[4:] if rest.upper().startswith("SOW_") else rest
    return ref_no, category


def _po_category(filename: str) -> str:
    stem = Path(filename).stem
    return stem[3:] if stem.upper().startswith("PO_") else stem


def extract_po_ref(content: str) -> str | None:
    """Extract the Purchase Order Ref No. via the LLM, with a regex fallback."""
    prompt = (
        "This is a government Purchase Order document. Extract the Purchase Order "
        "Reference Number (labelled 'Purchase Order Ref No.' or 'Contract No.'). "
        "Respond with ONLY the reference number (digits), nothing else.\n\n"
        f"{content[:4000]}"
    )
    try:
        resp = get_openai_client().chat.completions.create(
            model=get_settings().llm_model,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        digits = re.sub(r"\D", "", resp.choices[0].message.content or "")
        if digits:
            return digits
        log.warning("LLM returned no digits; falling back to regex")
    except Exception as exc:
        log.warning("LLM extraction failed (%s); falling back to regex", exc)

    ref = normalise_ref(PO_REF_RE.search(content).group(1)) if PO_REF_RE.search(content) else ""
    return ref or None


def load_sows(store: PostgresStore, sow_dir: Path) -> int:
    files = sorted(sow_dir.glob("*.md"))
    log.info("loading %d SOW file(s) from %s", len(files), sow_dir)
    for f in files:
        ref_no, category = _sow_ref_and_category(f.name)
        store.upsert_sow(ref_no, f.name, category, f.read_text(encoding="utf-8"))
        log.info("SOW %s <- %s", ref_no, f.name)
    return len(files)


def load_pos(store: PostgresStore, po_dir: Path) -> tuple[int, int]:
    files = sorted(po_dir.glob("*.md"))
    log.info("loading %d PO file(s) from %s", len(files), po_dir)
    linked = 0
    for f in files:
        content = f.read_text(encoding="utf-8")
        ref = extract_po_ref(content)
        if ref and not store.sow_ref_exists(ref):
            log.warning("PO %s: ref %s has no matching SOW — storing FK as NULL",
                        f.name, ref)
            ref = None
        store.upsert_po(ref, f.name, _po_category(f.name), content)
        if ref:
            linked += 1
        log.info("PO %s -> sow ref %s", f.name, ref)
    return len(files), linked


def build_index(store: PostgresStore) -> int:
    """Derive the searchable po_index rows from the documents already in Postgres.

    Deliberately reads `po.content` / `sow.content` rather than the files: an
    existing deployment can build the index without `data/` being mounted, and
    the index can be rebuilt after a parser change without re-loading anything.
    Parsing is pure regex, so this needs no model endpoint.
    """
    rows = store.list_po_source_rows()
    log.info("building po_index from %d PO row(s)", len(rows))
    built = 0
    for row in rows:
        facts = parse_po(row["content"] or "")
        ref = row["ref_no"] or facts.ref_no or None
        sow = store.get_sow_by_ref(ref) if ref else None
        sow_items = parse_sow(sow["content"]).item_names if sow else []
        keywords = " | ".join(
            filter(None, [facts.vendor, row.get("category") or "",
                          " | ".join(facts.item_names), " | ".join(sow_items)])
        )
        store.upsert_po_index({
            "po_id": row["id"],
            "ref_no": ref,
            "filename": row["filename"],
            "category": row.get("category"),
            "vendor": facts.vendor or None,
            "vendor_uen": facts.vendor_uen or None,
            "officer": facts.officer or None,
            "total_value": facts.total_value or None,
            "item_names": " | ".join(facts.item_names) or None,
            "payment_events": json.dumps([e.model_dump() for e in facts.payment_events]),
            "sow_items": " | ".join(sow_items) or None,
            "keywords": keywords or None,
        })
        built += 1
        log.debug("indexed %s (%s) vendor=%s total=%s items=%d events=%d",
                  ref, row["filename"], facts.vendor, facts.total_value,
                  len(facts.item_names), len(facts.payment_events))
    log.info("po_index: %d row(s)", built)
    return built


def ensure_po_index(store: PostgresStore | None = None, force: bool = False) -> int:
    """Build the index if it is empty (or if ``force``). Cheap and idempotent."""
    store = store or PostgresStore()
    store.init_po_sow_schema()
    if not force and store.po_index_count():
        return store.po_index_count()
    return build_index(store)


def main() -> None:
    data_dir = _data_dir()
    sow_dir, po_dir = data_dir / "sow", data_dir / "po"
    if not sow_dir.is_dir() or not po_dir.is_dir():
        log.error("expected %s and %s to exist", sow_dir, po_dir)
        raise SystemExit(2)

    store = PostgresStore()
    store.init_po_sow_schema()

    n_sow = load_sows(store, sow_dir)          # SOWs first (FK targets)
    n_po, linked = load_pos(store, po_dir)     # then POs (FK -> sow)

    n_indexed = build_index(store)             # then the searchable index

    log.info("done: %d SOW row(s), %d PO row(s) (%d linked to a SOW), %d indexed",
             n_sow, n_po, linked, n_indexed)


if __name__ == "__main__":
    main()
