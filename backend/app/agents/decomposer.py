"""The agent that breaks a generic, implausibly-priced invoice line into its parts.

Some invoice lines are one generic item ("equipment package", "advance payment")
carrying a very large price — really a bundle of several products. This agent
decides which lines are bundles by comparing the invoice price against the price
it can establish from the purchase order, then the SOW, then a web lookup, and
expands the ones that are.

Only lines at or above ``price_sanity_threshold`` are examined; the rest pass
through untouched, so the common case costs nothing.
"""

from __future__ import annotations

import json
import time

from app.agents.runtime import build_agent, invoke_agent_json
from app.core.logging_config import get_logger
from app.prompts.decompose import DECOMPOSE_SYSTEM_PROMPT
from app.schemas.domain import RawLineItem, coerce_line_items
from app.tools.web_price import query_item_price_tool

log = get_logger("agent.decompose")


def build(po_text: str, sow_text: str):
    """The decompose agent, with the two documents baked into its instructions."""
    return build_agent(
        tools=[query_item_price_tool],
        system_prompt=DECOMPOSE_SYSTEM_PROMPT.format(
            po=po_text or "(none)", sow=sow_text or "(none)"
        ),
    )


def detect_and_decompose(
    items: list[RawLineItem], po_text: str, sow_text: str
) -> list[RawLineItem]:
    """Flag generic/implausibly-priced lines and break them into individual items.

    Detection compares each line's invoice price against the expected price found
    via PO -> SOW -> query_item_price. Only lines priced at/above
    ``price_sanity_threshold`` are checked; the rest pass through untouched.
    """
    if not items:
        return []
    from app.core.config import get_settings

    threshold = get_settings().price_sanity_threshold
    candidates = [i for i in items if i.price >= threshold]
    passthrough = [i for i in items if i.price < threshold]
    if not candidates:
        log.info("detect: no line >= %.0f; skipping decomposition", threshold)
        return items

    t0 = time.perf_counter()
    log.info("detect: checking %d line(s) >= %.0f for generic-bundle decomposition",
             len(candidates), threshold)
    try:
        agent = build(po_text, sow_text)
        payload = "=== ITEMS TO CHECK ===\n" + json.dumps(
            [i.model_dump() for i in candidates], indent=2
        )
        decomposed = coerce_line_items(invoke_agent_json(agent, payload))
        if decomposed:
            out = decomposed + passthrough
            log.info("detect: %d checked line(s) -> %d item(s) (+%d untouched) in %.2fs",
                     len(candidates), len(decomposed), len(passthrough),
                     time.perf_counter() - t0)
            return out
        log.warning("detect: agent returned nothing usable; keeping items")
    except Exception as exc:
        log.warning("detect: agent failed (%s); keeping items", exc)
    return items
