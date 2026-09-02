"""The PO/SOW crawler, against the real corpus.

These need Postgres with the documents loaded and the index built; they do NOT
need the model endpoint, because they exercise the deterministic rung — the one
that has to work when the model is down, slow, or bad at this. If that rung is
right, the agent above it is an optimisation rather than a dependency.

The cases are the ones the feature exists for, and two of them are refusals: a
wrong purchase order is worse than none, so "declines to guess" is a passing
result, not a gap.

Run (inside the compose network, where Postgres is reachable):
    docker run --rm --network incub2_default -e POSTGRES_HOST=db \\
      -e POSTGRES_DB=assets -e POSTGRES_USER=assets -e POSTGRES_PASSWORD=assets \\
      --entrypoint uv asset-backend:latest run python tests/test_crawler.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.document_crawler import deterministic_match  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.ingest.parse import parse_po, parse_sow  # noqa: E402
from app.schemas.domain import InvoiceHint  # noqa: E402

# (name, reference as read off the page, what else the invoice says, expected PO)
CASES: list[tuple[str, str | None, dict, str | None]] = [
    # The reference is correct — must resolve, and resolve to itself.
    ("exact reference", "1000672008",
     dict(vendor="ApexForge Computing Pte Ltd", total_amount=18750.0,
          payment_event="Payment Event 1"), "1000672008"),
    # A digit misread as a letter. This is the case the crawler exists for:
    # the old exact-match lookup returned nothing at all here.
    ("misread digit", "100067200B",
     dict(vendor="ApexForge Computing Pte Ltd", total_amount=18750.0,
          payment_event="Payment Event 1"), "1000672008"),
    # No reference at all — vendor plus a payment-event amount must carry it.
    ("no reference", None,
     dict(vendor="ApexForge Computing Pte Ltd", total_amount=18750.0,
          payment_event="Payment Event 1"), "1000672008"),
    ("no reference, other vendor", None,
     dict(vendor="NetLink Infrastructure Pte Ltd", total_amount=7625.0), "1000672003"),
    # Meridian holds four purchase orders, so the vendor alone cannot decide.
    # Refusing is the correct answer.
    ("ambiguous vendor alone", None,
     dict(vendor="Meridian Office Furnishings Pte Ltd"), None),
    # The same vendor, with an amount that does separate them.
    ("ambiguous vendor + amount", None,
     dict(vendor="Meridian Office Furnishings Pte Ltd", total_amount=63500.0),
     "1000672004"),
    # Nothing on file. Must not invent a match.
    ("reference not in corpus", "9999999999",
     dict(vendor="Nobody Pte Ltd", total_amount=12.0), None),
    ("nothing to go on", None, dict(), None),
]


def test_deterministic_matching() -> None:
    threshold = get_settings().po_crawler_min_confidence
    failures: list[str] = []
    for name, ref, invoice, expected in CASES:
        match = deterministic_match(ref, InvoiceHint(po_ref=ref or "", **invoice))
        got = match.po_ref if (match.found and match.confidence >= threshold) else None
        if got != expected:
            failures.append(
                f"  {name}: expected {expected}, got {got} "
                f"(confidence {match.confidence:.2f})"
            )
    assert not failures, "crawler mismatches:\n" + "\n".join(failures)


def test_a_confident_match_always_carries_evidence() -> None:
    """Nothing may be reported as matched without a stated reason."""
    threshold = get_settings().po_crawler_min_confidence
    for name, ref, invoice, expected in CASES:
        if expected is None:
            continue
        match = deterministic_match(ref, InvoiceHint(po_ref=ref or "", **invoice))
        if match.confidence >= threshold:
            assert match.evidence, f"{name}: matched {match.po_ref} with no evidence"
            assert all(e.detail for e in match.evidence), f"{name}: blank evidence detail"


def test_sow_reference_is_derived_not_guessed() -> None:
    """The SOW comes from the chosen PO's own reference, never from a model."""
    match = deterministic_match(
        "1000672008",
        InvoiceHint(vendor="ApexForge Computing Pte Ltd", total_amount=18750.0),
    )
    assert match.sow_ref == match.po_ref, "SOW reference must follow the PO's"


def test_parsers_cover_the_whole_corpus() -> None:
    """Every PO must yield a reference, vendor, total, items and payment events.

    The index is only as good as this, and it is regex — so it either covers the
    corpus or it silently does not.
    """
    data = Path(__file__).resolve().parents[2] / "data"
    if not (data / "po").is_dir():        # not mounted (e.g. in the API image)
        return
    gaps: list[str] = []
    for f in sorted((data / "po").glob("*.md")):
        facts = parse_po(f.read_text(encoding="utf-8"))
        for field in ("ref_no", "vendor", "total_value", "item_names", "payment_events"):
            if not getattr(facts, field):
                gaps.append(f"  {f.name}: no {field}")
    for f in sorted((data / "sow").glob("*.md")):
        if not parse_sow(f.read_text(encoding="utf-8")).item_names:
            gaps.append(f"  {f.name}: no items")
    assert not gaps, "parser gaps:\n" + "\n".join(gaps)


if __name__ == "__main__":
    test_deterministic_matching()
    test_a_confident_match_always_carries_evidence()
    test_sow_reference_is_derived_not_guessed()
    test_parsers_cover_the_whole_corpus()
    print(f"OK — {len(CASES)} crawler cases, evidence present, parsers cover the corpus")
