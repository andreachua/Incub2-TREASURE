"""Load data/sow and data/po markdown files into Postgres (tables `sow`, `po`).

- sow.ref_no  = the filename prefix before the first "_"  (e.g. 1000672008)
- po.ref_no   = the Purchase Order Ref No., extracted from the PO via the LLM;
                it is a FK to sow.ref_no (many POs -> 1 SOW).

SOWs are loaded first so the PO foreign keys resolve.

Run (host):       uv run python scripts/load_documents.py
Run (container):  docker compose run --rm loader
                  # or: docker run --rm --network incub2_default -v ./data:/data \
                  #        -e DATA_DIR=/data -e POSTGRES_HOST=db ... \
                  #        --entrypoint uv asset-backend:latest run python scripts/load_documents.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import get_settings  # noqa: E402
from src.llm import get_openai_client  # noqa: E402
from src.logging_config import get_logger  # noqa: E402
from src.store.postgres_store import PostgresStore  # noqa: E402

log = get_logger("loader")


def _data_dir() -> Path:
    # DATA_DIR wins (used in the container); else the repo's data/ dir.
    env = os.getenv("DATA_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "data"


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

    m = re.search(r"Purchase Order Ref\.?\s*No\.?:?\s*([0-9]+)", content, re.IGNORECASE)
    return m.group(1) if m else None


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

    log.info("done: %d SOW row(s), %d PO row(s) (%d linked to a SOW)",
             n_sow, n_po, linked)


if __name__ == "__main__":
    main()
