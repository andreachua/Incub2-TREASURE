"""Stage 2 — categorize extracted line items with a deepagents agent.

The agent runs on the same local model and is given one tool:
  * list_categories()  -> the allowed categories for the system (oxn / ehab)

For each receipt line item the agent assigns exactly one category from the
allowed list (the group is derived deterministically).

If the local model does not support tool-calling (so the agent can't drive the
tool), ``categorize`` transparently falls back to a deterministic path that puts
the category list and the items in a single structured prompt.
"""

from __future__ import annotations

import json
import time

from langchain_core.tools import tool

from .config import (
    DEFAULT_SYSTEM,
    category_names,
    group_for_category,
    load_categories,
)
from .json_utils import extract_json_list
from .llm import get_openai_client
from .logging_config import get_logger
from .models import AssetRecord, RawLineItem

log = get_logger("stage2")


# --------------------------------------------------------------------------- #
# Categories tool (bound to a system)
# --------------------------------------------------------------------------- #
def _format_categories(system: str = DEFAULT_SYSTEM) -> str:
    """Group categories by their parent group for prompting."""
    cats = load_categories(system)
    lines: list[str] = []
    current = None
    for c in cats:
        if c.group != current:
            current = c.group
            lines.append(f"\n[{current}]")
        code = f"({c.code}) " if c.code else ""
        lines.append(f"- {code}{c.name}: {c.description}")
    return "\n".join(lines).strip()


def _make_list_categories(system: str):
    """Build a `list_categories` tool bound to a system's category set."""

    @tool
    def list_categories() -> str:
        """List the allowed categories, grouped by Assets / Inventories / Expenses.

        Always choose the category `name` exactly as written here.
        """
        return _format_categories(system)

    return list_categories


SYSTEM_PROMPT = """You classify purchased items for a government asset register.

Each allowed category belongs to one group: Assets, Inventories, or Expenses.

You are given a list of receipt line items. For EACH item:
1. Call list_categories() to see the allowed categories.
2. Choose exactly ONE category name that best fits the item (based on its name
   and description).
3. Give a short reasoning (one sentence) explaining why that category and its
   group were chosen.
4. Keep the item's name, description, quantity and price from the receipt.

When done, respond with ONLY a JSON array (no prose, no markdown fences). Each
element must have exactly these keys: "name", "description", "category",
"reasoning", "quantity", "price". The "category" must be one of the allowed
category names (the group is derived automatically).
"""


def _build_agent(system: str):
    from deepagents import create_deep_agent

    from .llm import get_chat_model

    return create_deep_agent(
        model=get_chat_model(),
        tools=[_make_list_categories(system)],
        system_prompt=SYSTEM_PROMPT,
    )


def _items_payload(items: list[RawLineItem]) -> str:
    return (
        "receipt line items:\n"
        f"{json.dumps([i.model_dump() for i in items], indent=2)}"
    )


def _finalize(entries: list, allowed: set[str], system: str) -> list[AssetRecord]:
    """Validate model output into AssetRecords, constraining the category."""
    records: list[AssetRecord] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            rec = AssetRecord(**entry)
        except Exception:
            continue
        if allowed and rec.category not in allowed:
            rec.category = _closest_category(rec.category, allowed)
        rec.group = group_for_category(rec.category, system)  # derive deterministically
        if rec.name:
            records.append(rec)
    return records


def _closest_category(value: str, allowed: set[str]) -> str:
    """Map an off-list category to the nearest allowed one."""
    v = (value or "").strip().lower()
    for name in allowed:
        if name.lower() == v:
            return name
    for name in allowed:
        if v and (v in name.lower() or name.lower() in v):
            return name
    return _default_category(allowed)


def _default_category(allowed: set[str]) -> str:
    """Deterministic catch-all when no category matches."""
    for preferred in ("Expenses (Non-capital)", "Inventory / Consumable Materials"):
        if preferred in allowed:
            return preferred
    return sorted(allowed)[0] if allowed else ""


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def categorize(
    items: list[RawLineItem],
    system: str = DEFAULT_SYSTEM,
) -> list[AssetRecord]:
    """Categorize items via the deepagents agent, falling back on failure.

    ``system`` (oxn/ehab) selects which category set is used.
    """
    if not items:
        return []
    allowed = set(category_names(system))
    log.info("Stage 2: categorizing %d item(s), system=%s (%d categories)",
             len(items), system, len(allowed))
    if not allowed:
        log.warning("no categories defined for system=%r — items will be unclassified",
                    system)

    t0 = time.perf_counter()
    try:
        agent = _build_agent(system)
        result = agent.invoke(
            {"messages": [{"role": "user", "content": _items_payload(items)}]}
        )
        text = _last_message_text(result)
        records = _finalize(extract_json_list(text), allowed, system)
        if records:
            log.info("Stage 2: agent classified %d item(s) in %.2fs",
                     len(records), time.perf_counter() - t0)
            for r in records:
                log.debug("  %s -> %s / %s", r.name, r.group, r.category)
            return records
        log.warning("Stage 2: agent returned no usable records; using fallback")
    except Exception as exc:
        log.warning("Stage 2: agent path failed (%s); using fallback", exc)

    records = _categorize_fallback(items, allowed, system)
    log.info("Stage 2: fallback classified %d item(s) in %.2fs",
             len(records), time.perf_counter() - t0)
    return records


def _last_message_text(result: dict) -> str:
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for msg in reversed(messages):
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):  # some models return content blocks
            parts = [b.get("text", "") for b in content if isinstance(b, dict)]
            joined = "".join(parts)
            if joined.strip():
                return joined
    return ""


def _categorize_fallback(
    items: list[RawLineItem], allowed: set[str], system: str
) -> list[AssetRecord]:
    """Deterministic path: one structured LLM call with the category list.

    Used when the local model can't drive tools.
    """
    cats = _format_categories(system)

    prompt = (
        "You classify purchased items for a government asset register.\n"
        "Each category belongs to a group (Assets / Inventories / Expenses).\n\n"
        f"Allowed categories (choose the name exactly):\n{cats}\n\n"
        "Receipt line items to classify:\n"
        f"{json.dumps([i.model_dump() for i in items], indent=2)}\n\n"
        "For each receipt item: assign exactly one allowed category based on its "
        "name and description; give a short reasoning (one sentence) for the "
        "choice; keep the receipt name, description, quantity and price. Respond "
        "with ONLY a JSON array of objects with keys "
        '"name", "description", "category", "reasoning", "quantity", "price".'
    )

    from .config import get_settings

    resp = get_openai_client().chat.completions.create(
        model=get_settings().llm_model,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.choices[0].message.content or ""
    records = _finalize(extract_json_list(text), allowed, system)
    if records:
        return records

    # Last-resort: keep the items with a default category so we never drop data.
    default = _default_category(allowed)
    return [
        AssetRecord(
            name=i.name,
            description=i.description,
            category=default,
            group=group_for_category(default, system),
            reasoning="default category (model produced no usable classification)",
            quantity=i.quantity,
            price=i.price,
        )
        for i in items
    ]
