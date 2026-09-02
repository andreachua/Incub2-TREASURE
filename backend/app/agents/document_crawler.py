"""Finding the purchase order and scope of work an invoice belongs to.

Before this, matching was one exact lookup on the digits the VLM read off the
page. One misread digit and the invoice got no context at all, so pricing,
bundle decomposition and the procurement header all quietly degraded — with
nothing in the logs to say why.

The corpus carries far more to go on: the reference, the contractor, the total
order value, the individual payment-event amounts, the item names, the category.
This module uses all of them, on a ladder that gets more expensive as it goes:

    rung 0  the reference matches exactly            -> no model call at all
    rung 1  a deepagents agent with five tools
    rung 2  one non-tool call with the whole index   -> for models without tools
    rung 3  pure-Python signal scoring               -> no model at all
    rung 4  nothing

Rung 0 short-circuits the common case, so traffic that already worked pays no
latency. **Rung 3 is the load-bearing one**: on a corpus this size it resolves
most misread references by itself, and it cannot fail the way a model can. The
agent is the upgrade, not the foundation.

Two rules hold whatever the model says. Any reference it returns is checked
against the database and dropped if it does not exist, which neutralises
hallucination outright. And confidence is recomputed here from the signals that
actually verified — the model's own certainty goes in ``reasoning``, nowhere else.

Everything runs on the local endpoint configured in ``.env``.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.agents.runtime import build_agent, invoke_agent_object
from app.core.config import get_settings
from app.core.json_utils import extract_json
from app.core.logging_config import get_logger
from app.ingest.parse import normalise_ref
from app.prompts.crawler import CRAWLER_FALLBACK_PROMPT, CRAWLER_SYSTEM_PROMPT
from app.schemas.domain import ContextMatch, InvoiceHint, MatchEvidence
from app.store.postgres_store import PostgresStore
from app.tools.procurement import CRAWLER_TOOLS, format_index

log = get_logger("crawler")

_store = PostgresStore()

# How close an invoice amount must be to a payment event or order total to count.
_AMOUNT_TOLERANCE = 0.01          # 1%
_ITEM_OVERLAP_BONUS = 0.15        # capped contribution from matching item names
_MAX_CONFIDENCE_WITHOUT_EXACT_REF = 0.95


# --------------------------------------------------------------------------- #
# Reading the index
# --------------------------------------------------------------------------- #
def _index() -> list[dict[str, Any]]:
    try:
        return _store.list_po_index()
    except Exception as exc:  # an empty index degrades matching, never breaks it
        log.warning("could not read the PO index (%s); exact-ref matching only", exc)
        return []


def _events(row: dict[str, Any]) -> list[dict]:
    try:
        return json.loads(row.get("payment_events") or "[]")
    except (ValueError, TypeError):
        return []


def _norm(text: str | None) -> str:
    return " ".join((text or "").lower().split())


# --------------------------------------------------------------------------- #
# Signals — each returns evidence, or nothing
# --------------------------------------------------------------------------- #
def _vendor_matches(hint_vendor: str, row_vendor: str | None) -> bool:
    """Vendor names agree if either contains the other's distinctive part.

    Invoices print "ApexForge Computing Pte Ltd" and sometimes just "ApexForge",
    so an equality test is too strict and a substring test on the full string is
    too loose.
    """
    a, b = _norm(hint_vendor), _norm(row_vendor)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    # Compare on the first two words, which carry the brand.
    return " ".join(a.split()[:2]) == " ".join(b.split()[:2])


def _amount_signal(amount: float, row: dict[str, Any]) -> MatchEvidence | None:
    """Does the invoice total equal this order's total, or one of its payment events?"""
    if amount <= 0:
        return None
    total = float(row["total_value"]) if row.get("total_value") else 0.0
    if total and abs(amount - total) <= total * _AMOUNT_TOLERANCE:
        return MatchEvidence(
            signal="total_amount", score=0.35,
            detail=f"Invoice total S${amount:,.2f} matches the order total "
                   f"S${total:,.2f}.")
    for event in _events(row):
        value = float(event.get("amount") or 0)
        if value and abs(amount - value) <= value * _AMOUNT_TOLERANCE:
            desc = (event.get("description") or "").strip()
            return MatchEvidence(
                signal="payment_event", score=0.4,
                detail=f"Invoice total S${amount:,.2f} matches payment event "
                       f"{event.get('no')} ({desc}) exactly.")
    return None


def _item_overlap(item_names: list[str], row: dict[str, Any]) -> MatchEvidence | None:
    """How many invoice item names appear among the order's or its SOW's items."""
    if not item_names:
        return None
    corpus = _norm(f"{row.get('item_names') or ''} | {row.get('sow_items') or ''}")
    if not corpus:
        return None
    hits = [n for n in item_names if n and _norm(n) in corpus]
    if not hits:
        # Fall back to a token test: "AI Workstation (Tower)" vs "AI Workstation".
        hits = [n for n in item_names
                if n and len(_norm(n).split()) > 1
                and " ".join(_norm(n).split()[:2]) in corpus]
    if not hits:
        return None
    ratio = len(hits) / len(item_names)
    return MatchEvidence(
        signal="item_overlap", score=round(_ITEM_OVERLAP_BONUS * ratio, 3),
        detail=f"{len(hits)} of {len(item_names)} invoice items appear in this "
               f"contract (e.g. {hits[0]}).")


def _score_candidate(
    row: dict[str, Any], hint: InvoiceHint, ref_evidence: MatchEvidence | None
) -> tuple[float, list[MatchEvidence]]:
    """Confidence for one candidate, from the signals that actually verify."""
    evidence: list[MatchEvidence] = []
    if ref_evidence:
        evidence.append(ref_evidence)

    vendor_hit = _vendor_matches(hint.vendor, row.get("vendor"))
    if vendor_hit:
        evidence.append(MatchEvidence(
            signal="vendor", score=0.3,
            detail=f"Invoice vendor matches the contractor, {row.get('vendor')}."))

    amount = _amount_signal(hint.total_amount, row)
    if amount:
        evidence.append(amount)

    overlap = _item_overlap(hint.item_names, row)
    if overlap:
        evidence.append(overlap)

    confidence = sum(e.score for e in evidence)
    if ref_evidence and ref_evidence.signal == "ref_exact":
        confidence = 1.0
    else:
        confidence = min(confidence, _MAX_CONFIDENCE_WITHOUT_EXACT_REF)
    return round(confidence, 3), evidence


def _match_from(row: dict[str, Any], confidence: float, method: str,
                evidence: list[MatchEvidence], reasoning: str = "",
                considered: list[str] | None = None) -> ContextMatch:
    """Build the result, deriving the SOW reference rather than trusting anyone."""
    ref = row.get("ref_no") or ""
    sow = _store.get_sow_by_ref(ref) if ref else None
    return ContextMatch(
        po_ref=ref,
        sow_ref=ref if sow else "",
        sow_filename=(sow or {}).get("filename", "") if sow else "",
        vendor=row.get("vendor") or "",
        po_filenames=[r["filename"] for r in _store.get_po_by_ref(ref)] if ref else [],
        confidence=confidence,
        method=method,
        evidence=evidence,
        reasoning=reasoning,
        candidates_considered=considered or [],
    )


# --------------------------------------------------------------------------- #
# Rung 3 — deterministic. No model involved.
# --------------------------------------------------------------------------- #
def deterministic_match(po_ref: str | None, hint: InvoiceHint) -> ContextMatch:
    """Score every order in the index against the invoice and take the best.

    This rung is the safety net under the two model rungs, and it is genuinely
    capable on its own: fuzzy reference plus vendor plus a payment-event amount
    identifies an order in this corpus without any model at all.
    """
    index = _index()
    if not index:
        return ContextMatch()

    ref = normalise_ref(po_ref or hint.po_ref or hint.po_no)
    # Candidate references close to what was read off the page.
    near: dict[str, MatchEvidence] = {}
    if ref:
        for row in _store.find_po_refs_like(ref):
            distance = int(row.get("distance") or 0)
            if row.get("exact"):
                near[row["ref_no"]] = MatchEvidence(
                    signal="ref_exact", score=1.0,
                    detail=f"Invoice states purchase order {row['ref_no']}.")
            else:
                near[row["ref_no"]] = MatchEvidence(
                    signal="ref_fuzzy", score=max(0.0, 0.5 - 0.1 * distance),
                    detail=f"Invoice reference {ref} is {distance} character(s) "
                           f"from {row['ref_no']}.")

    scored: list[tuple[float, dict, list[MatchEvidence]]] = []
    for row in index:
        row_ref = row.get("ref_no") or ""
        confidence, evidence = _score_candidate(row, hint, near.get(row_ref))
        if evidence:
            scored.append((confidence, row, evidence))
    if not scored:
        return ContextMatch()

    scored.sort(key=lambda t: t[0], reverse=True)
    confidence, row, evidence = scored[0]

    # An ambiguous winner is not a winner. If the runner-up is just as good, the
    # signals did not actually separate them.
    if len(scored) > 1 and scored[1][0] >= confidence - 0.01:
        runner = scored[1][1].get("ref_no")
        log.info("deterministic: %s and %s tie at %.2f — reporting low confidence",
                 row.get("ref_no"), runner, confidence)
        confidence = min(confidence, 0.5)

    considered = [r.get("ref_no") for _, r, _ in scored[:5] if r.get("ref_no")]
    return _match_from(row, confidence, "deterministic", evidence,
                       reasoning="; ".join(e.detail for e in evidence),
                       considered=considered)


# --------------------------------------------------------------------------- #
# Validating whatever a model said
# --------------------------------------------------------------------------- #
def _validate(data: dict, hint: InvoiceHint, method: str) -> ContextMatch | None:
    """Turn a model's answer into a match, or reject it.

    The reference must exist in the database. Confidence is recomputed from the
    signals that verify here, never taken from the model.
    """
    ref = normalise_ref(data.get("po_ref"))
    if not ref:
        return None
    row = _store.get_po_index(ref)
    if not row:
        log.warning("%s: model returned ref %r, which is not in the corpus — rejecting",
                    method, data.get("po_ref"))
        return None

    stated = normalise_ref(hint.po_ref or hint.po_no)
    if stated == ref:
        ref_evidence = MatchEvidence(signal="ref_exact", score=1.0,
                                     detail=f"Invoice states purchase order {ref}.")
    elif stated:
        ref_evidence = MatchEvidence(
            signal="ref_fuzzy", score=0.4,
            detail=f"Invoice reference {stated} resolved to {ref}.")
    else:
        ref_evidence = None

    confidence, evidence = _score_candidate(row, hint, ref_evidence)
    if not evidence:
        log.warning("%s: ref %s exists but nothing on the invoice corroborates it",
                    method, ref)
        return None
    considered = [normalise_ref(c) for c in (data.get("candidates_considered") or [])]
    return _match_from(row, confidence, method, evidence,
                       reasoning=str(data.get("reasoning") or "").strip(),
                       considered=[c for c in considered if c])


# --------------------------------------------------------------------------- #
# Rungs 1 and 2 — the model
# --------------------------------------------------------------------------- #
def _invoice_block(hint: InvoiceHint) -> str:
    lines = [
        f"invoice number : {hint.invoice_no or '(none)'}",
        f"invoice date   : {hint.invoice_date or '(none)'}",
        f"vendor         : {hint.vendor or '(none)'}",
        f"reference read : {hint.po_ref or hint.po_no or '(none)'}",
        f"payment event  : {hint.payment_event or '(none)'}",
        f"invoice total  : S${hint.total_amount:,.2f}" if hint.total_amount
        else "invoice total  : (unknown)",
    ]
    if hint.item_names:
        lines.append("items          : " + "; ".join(hint.item_names[:12]))
    return "\n".join(lines)


def _amount_hints(hint: InvoiceHint) -> str:
    """Pre-computed amount matches, handed over as fact.

    Local models compare formatted currency unreliably, so the arithmetic is
    done here and the conclusion is given to the model rather than asked of it.
    """
    if hint.total_amount <= 0:
        return ""
    hits = []
    for row in _index():
        evidence = _amount_signal(hint.total_amount, row)
        if evidence:
            hits.append(f"- {row.get('ref_no')}: {evidence.detail}")
    if not hits:
        return "\nNo purchase order has a total or payment event equal to the invoice amount.\n"
    return "\nAmount matches already computed for you:\n" + "\n".join(hits[:6]) + "\n"


def _agent_match(hint: InvoiceHint) -> ContextMatch | None:
    agent = build_agent(
        tools=CRAWLER_TOOLS,
        system_prompt=CRAWLER_SYSTEM_PROMPT.format(index=format_index()),
    )
    payload = "=== THE INVOICE ===\n" + _invoice_block(hint) + "\n" + _amount_hints(hint)
    return _validate(invoke_agent_object(agent, payload), hint, "agent")


def _fallback_llm_match(hint: InvoiceHint) -> ContextMatch | None:
    """One plain call, for a model that cannot drive tools."""
    from app.core.llm import get_openai_client

    prompt = CRAWLER_FALLBACK_PROMPT.format(
        index=format_index(),
        invoice=_invoice_block(hint) + "\n" + _amount_hints(hint),
    )
    resp = get_openai_client().chat.completions.create(
        model=get_settings().llm_model,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    data = extract_json(resp.choices[0].message.content or "",
                        prefer="object")
    return _validate(data if isinstance(data, dict) else {}, hint, "fallback_llm")


# --------------------------------------------------------------------------- #
# The public entry point
# --------------------------------------------------------------------------- #
def crawl_context(po_ref: str | None, hint: InvoiceHint | None = None) -> ContextMatch:
    """Find the purchase order (and so the SOW) an invoice belongs to."""
    hint = hint or InvoiceHint(po_ref=po_ref or "")
    settings = get_settings()
    mode = (settings.po_crawler_mode or "agent").lower()
    ref = normalise_ref(po_ref or hint.po_ref or hint.po_no)

    # Rung 0 — the reference is right. No model call, no latency added.
    if ref and _store.get_po_by_ref(ref):
        row = _store.get_po_index(ref) or {"ref_no": ref}
        log.info("crawler: exact ref %s", ref)
        return _match_from(
            row, 1.0, "exact",
            [MatchEvidence(signal="ref_exact", score=1.0,
                           detail=f"Invoice states purchase order {ref}, which is on file.")],
            reasoning=f"The invoice's purchase order reference {ref} matches a filed order.")

    if mode == "off":
        log.info("crawler: disabled (PO_CRAWLER=off) and no exact ref — no context")
        return ContextMatch()

    t0 = time.perf_counter()
    log.info("crawler: ref %r not on file — searching by vendor/amount/items",
             ref or "(none)")

    if mode == "agent":
        # Rung 1 — the agent.
        try:
            match = _agent_match(hint)
            if match and match.confidence >= settings.po_crawler_min_confidence:
                log.info("crawler: agent matched %s (%.2f) in %.2fs — %s",
                         match.po_ref, match.confidence, time.perf_counter() - t0,
                         match.reasoning[:100])
                return match
            log.warning("crawler: agent produced no confident match; trying one plain call")
        except Exception as exc:
            log.warning("crawler: agent path failed (%s); trying one plain call", exc)

        # Rung 2 — one call, no tools.
        try:
            match = _fallback_llm_match(hint)
            if match and match.confidence >= settings.po_crawler_min_confidence:
                log.info("crawler: single-call matched %s (%.2f) in %.2fs",
                         match.po_ref, match.confidence, time.perf_counter() - t0)
                return match
            log.warning("crawler: single call produced no confident match; scoring in Python")
        except Exception as exc:
            log.warning("crawler: single call failed (%s); scoring in Python", exc)

    # Rung 3 — deterministic. Always available.
    match = deterministic_match(po_ref, hint)
    if match.found and match.confidence >= settings.po_crawler_min_confidence:
        log.info("crawler: deterministic matched %s (%.2f) in %.2fs — %s",
                 match.po_ref, match.confidence, time.perf_counter() - t0,
                 match.reasoning[:100])
        return match

    # Rung 4 — nothing. The pipeline behaves exactly as it did before the crawler.
    if match.found:
        log.info("crawler: best candidate %s scored %.2f, below the %.2f threshold — "
                 "reporting no match", match.po_ref, match.confidence,
                 settings.po_crawler_min_confidence)
    else:
        log.info("crawler: no candidate matched in %.2fs", time.perf_counter() - t0)
    return ContextMatch(candidates_considered=match.candidates_considered)
