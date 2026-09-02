"""The agent that establishes each item's unit price.

It looks in the purchase order first, then the scope of work, and only falls
back to a web search when neither document carries the item — the documents are
authoritative, the web is a guess.

On any failure the items come back unpriced rather than wrongly priced; the
caller then caps the enriched total against the invoice's own total, so a missing
price never inflates the register.
"""

from __future__ import annotations

import json
import time

from app.agents.runtime import build_agent, invoke_agent_json
from app.core.logging_config import get_logger
from app.prompts.pricing import PRICE_SYSTEM_PROMPT
from app.schemas.domain import RawLineItem, coerce_line_items
from app.tools.web_price import query_item_price_tool

log = get_logger("agent.price")


def build():
    """The price agent. The documents travel in the payload, not the prompt."""
    return build_agent(tools=[query_item_price_tool], system_prompt=PRICE_SYSTEM_PROMPT)


def _price_payload(items: list[RawLineItem], po_text: str, sow_text: str) -> str:
    return (
        "=== PURCHASE ORDER ===\n" + (po_text or "(none)") +
        "\n\n=== STATEMENT OF WORK ===\n" + (sow_text or "(none)") +
        "\n\n=== ITEMS TO PRICE ===\n" +
        json.dumps([i.model_dump() for i in items], indent=2)
    )


def price_items(
    items: list[RawLineItem], po_text: str, sow_text: str
) -> list[RawLineItem]:
    if not items:
        return []
    t0 = time.perf_counter()
    try:
        agent = build()
        priced = coerce_line_items(
            invoke_agent_json(agent, _price_payload(items, po_text, sow_text))
        )
        if priced:
            log.info("priced %d item(s) via agent in %.2fs",
                     len(priced), time.perf_counter() - t0)
            return priced
        log.warning("price agent returned nothing usable; keeping items unpriced")
    except Exception as exc:
        log.warning("price agent failed (%s); keeping items unpriced", exc)
    return items
