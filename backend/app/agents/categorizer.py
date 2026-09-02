"""The agent that assigns one allowed category to each line item.

It gets a single tool — ``list_categories()``, bound to the system's taxonomy —
and must pick a category ``name`` exactly as written. The parent group is never
taken from the model; the caller re-derives it from the chosen category, so an
invented group cannot reach the register.

The caller (``pipeline.categorize``) owns the fallback for models that cannot
drive tools.
"""

from __future__ import annotations

import json

from app.agents.runtime import build_agent, invoke_agent_json
from app.prompts.categorize import SYSTEM_PROMPT
from app.schemas.domain import RawLineItem
from app.tools.categories import make_list_categories


def build(system: str):
    """The categorizer agent, bound to one system's category set."""
    return build_agent(
        tools=[make_list_categories(system)],
        system_prompt=SYSTEM_PROMPT,
    )


def items_payload(items: list[RawLineItem]) -> str:
    return (
        "receipt line items:\n"
        f"{json.dumps([i.model_dump() for i in items], indent=2)}"
    )


def classify(items: list[RawLineItem], system: str) -> list:
    """Run the agent and return the raw entries it produced.

    Validation and category constraining belong to the caller, which also owns
    the fallback path — so this raises rather than swallowing, and returns ``[]``
    only when the model genuinely said nothing parseable.
    """
    return invoke_agent_json(build(system), items_payload(items))
