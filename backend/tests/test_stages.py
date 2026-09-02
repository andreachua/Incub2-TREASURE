"""The stages a job reports as it runs, in the order it reports them.

The upload screen renders these as a checklist, so a stage that is renamed,
reordered or dropped silently turns into a checklist that ticks the wrong row —
and nothing else in the suite would notice, since the pipeline itself does not
care what it announces. This drives ``process_job`` with every real stage
stubbed out and asserts the sequence, which needs no models, no Redis and no
Postgres.

Run:  uv run python -m pytest tests/test_stages.py -q
  or: uv run python tests/test_stages.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pipeline import orchestrator  # noqa: E402
from app.schemas.domain import ContextMatch, InvoiceHeader  # noqa: E402


def test_stage_keys_are_unique() -> None:
    assert len(set(orchestrator.STAGES)) == len(orchestrator.STAGES)
    assert all(s and s.islower() for s in orchestrator.STAGES)


def _run(doc_type: str, monkeypatch) -> list[str]:
    """Run process_job with every stage stubbed, and collect what it reported."""
    import app.pipeline.categorize as categorize_mod
    import app.pipeline.enrich as enrich_mod
    import app.pipeline.write as write_mod

    monkeypatch.setattr(orchestrator, "_resolve_receipt",
                        lambda job: ("/tmp/nothing.png", False, {}))
    monkeypatch.setattr(orchestrator, "extract_invoice",
                        lambda path: ("PO-1", InvoiceHeader(), []))
    monkeypatch.setattr(orchestrator, "stamp_records",
                        lambda *a, **k: None)
    monkeypatch.setattr(enrich_mod, "resolve_context_match",
                        lambda *a, **k: ContextMatch())
    monkeypatch.setattr(enrich_mod, "load_match_texts", lambda match: ("", ""))
    monkeypatch.setattr(enrich_mod, "extract_po_header", lambda text: object())
    monkeypatch.setattr(enrich_mod, "enrich_items", lambda items, po, sow: items)
    monkeypatch.setattr(enrich_mod, "reconcile", lambda records, items: records)
    monkeypatch.setattr(categorize_mod, "categorize", lambda items, system: [])
    monkeypatch.setattr(write_mod, "write_assets_to_db", lambda *a, **k: [])
    monkeypatch.setattr(write_mod, "to_records", lambda records: [])

    seen: list[str] = []
    result = orchestrator.process_job(
        {"job_id": "j1", "type": doc_type, "receipt_id": 1, "system": "oxn"},
        on_stage=seen.append,
    )
    assert result["status"] == "ok", result.get("error")
    return seen


def test_an_invoice_reports_every_stage_in_order(monkeypatch) -> None:
    assert _run("invoice", monkeypatch) == list(orchestrator.STAGES)


def test_a_receipt_skips_the_two_enrichment_stages(monkeypatch) -> None:
    """A receipt has no PO to find and nothing to split or price."""
    assert _run("receipt", monkeypatch) == ["extract", "categorize", "write"]


def test_a_broken_reporter_does_not_fail_the_job(monkeypatch) -> None:
    """Progress is cosmetic — Redis blinking must not lose the operator's work."""
    import app.pipeline.categorize as categorize_mod
    import app.pipeline.enrich as enrich_mod
    import app.pipeline.write as write_mod

    monkeypatch.setattr(orchestrator, "_resolve_receipt",
                        lambda job: ("/tmp/nothing.png", False, {}))
    monkeypatch.setattr(orchestrator, "extract_invoice",
                        lambda path: ("PO-1", InvoiceHeader(), []))
    monkeypatch.setattr(orchestrator, "stamp_records", lambda *a, **k: None)
    monkeypatch.setattr(enrich_mod, "resolve_context_match",
                        lambda *a, **k: ContextMatch())
    monkeypatch.setattr(enrich_mod, "load_match_texts", lambda match: ("", ""))
    monkeypatch.setattr(enrich_mod, "extract_po_header", lambda text: object())
    monkeypatch.setattr(enrich_mod, "enrich_items", lambda items, po, sow: items)
    monkeypatch.setattr(enrich_mod, "reconcile", lambda records, items: records)
    monkeypatch.setattr(categorize_mod, "categorize", lambda items, system: [])
    monkeypatch.setattr(write_mod, "write_assets_to_db", lambda *a, **k: [])
    monkeypatch.setattr(write_mod, "to_records", lambda records: [])

    def boom(_stage: str) -> None:
        raise RuntimeError("redis is down")

    result = orchestrator.process_job(
        {"job_id": "j2", "type": "invoice", "receipt_id": 1, "system": "oxn"},
        on_stage=boom,
    )
    assert result["status"] == "ok", result.get("error")


if __name__ == "__main__":
    class _Patch:
        """The two things monkeypatch gives us, without pytest."""

        def __init__(self) -> None:
            self._undo: list[tuple] = []

        def setattr(self, target, name, value) -> None:
            self._undo.append((target, name, getattr(target, name)))
            setattr(target, name, value)

        def undo(self) -> None:
            for target, name, old in reversed(self._undo):
                setattr(target, name, old)
            self._undo.clear()

    test_stage_keys_are_unique()
    for case in (test_an_invoice_reports_every_stage_in_order,
                 test_a_receipt_skips_the_two_enrichment_stages,
                 test_a_broken_reporter_does_not_fail_the_job):
        patch = _Patch()
        try:
            case(patch)
        finally:
            patch.undo()
        print(f"  ok  {case.__name__}")
    print(f"OK — {len(orchestrator.STAGES)} stages, reported in order")
