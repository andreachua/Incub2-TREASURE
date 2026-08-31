"""Stage 2 — categorize extracted line items with a deepagents agent.

The agent runs on the same local model and is given three tools:
  * list_categories()              -> the allowed categories (config/categories.yaml)
  * get_project_document(pid)      -> full line items for a project (key lookup)
  * search_line_items(pid, query)  -> optional pgvector shortlist

For each receipt item the agent looks up the project document, finds the most
related line item, and assigns one category from the allowed list.

If the local model does not support tool-calling (so the agent can't drive the
tools), ``categorize`` transparently falls back to a deterministic path that
fetches the document itself and asks the model for a single structured answer.
"""

from __future__ import annotations

import json

from langchain_core.tools import tool

from .config import category_names, load_categories
from .json_utils import extract_json_list
from .llm import get_openai_client
from .models import AssetRecord, RawLineItem
from .store.postgres_store import PostgresStore

# Lazy store handle (no DB connection until a method is called).
_store = PostgresStore()


# --------------------------------------------------------------------------- #
# Tools (used by the deepagents agent)
# --------------------------------------------------------------------------- #
@tool
def list_categories() -> str:
    """List the allowed asset categories with descriptions.

    Always choose the category `name` exactly as written here.
    """
    cats = load_categories()
    return "\n".join(f"- {c.name}: {c.description}" for c in cats)


@tool
def get_project_document(project_id: str) -> str:
    """Fetch the full purchase-record line items stored for a project id.

    Returns a JSON array of the project's known line items (name, description,
    quantity, price). Use it to find the item most related to a receipt line.
    """
    rows = _store.get_by_project_id(project_id)
    items = [r["content"] for r in rows]
    if not items:
        return f"No stored document found for project_id={project_id!r}."
    return json.dumps(items, indent=2)


@tool
def search_line_items(project_id: str, query: str) -> str:
    """Semantically shortlist a project's line items most similar to `query`.

    Optional helper for large documents. Returns a JSON array (possibly empty
    if semantic search is unavailable — then use get_project_document instead).
    """
    rows = _store.search_line_items(project_id, query, k=5)
    return json.dumps([r["content"] for r in rows], indent=2)


AGENT_TOOLS = [list_categories, get_project_document, search_line_items]

SYSTEM_PROMPT = """You classify purchased IT assets for an asset register.

You are given a project_id and a list of receipt line items. For EACH item:
1. Call get_project_document(project_id) to see the project's known line items
   (use search_line_items for a large document). Find the stored line item most
   related to the receipt item and use its details to enrich the description.
2. Call list_categories() and choose exactly ONE category name for the item.
3. Keep the item's quantity and price from the receipt.

When done, respond with ONLY a JSON array (no prose, no markdown fences). Each
element must have exactly these keys: "name", "description", "category",
"quantity", "price". The "category" must be one of the allowed category names.
"""


def _build_agent():
    from deepagents import create_deep_agent

    from .llm import get_chat_model

    return create_deep_agent(
        model=get_chat_model(),
        tools=AGENT_TOOLS,
        system_prompt=SYSTEM_PROMPT,
    )


def _items_payload(items: list[RawLineItem], project_id: str) -> str:
    return (
        f"project_id: {project_id}\n"
        f"receipt line items:\n"
        f"{json.dumps([i.model_dump() for i in items], indent=2)}"
    )


def _finalize(entries: list, allowed: set[str]) -> list[AssetRecord]:
    """Validate model output into AssetRecords, constraining the category."""
    records: list[AssetRecord] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            rec = AssetRecord(**entry)
        except Exception:
            continue
        if rec.category not in allowed:
            rec.category = _closest_category(rec.category, allowed)
        if rec.name:
            records.append(rec)
    return records


def _closest_category(value: str, allowed: set[str]) -> str:
    """Map an off-list category to the nearest allowed one (else Uncategorized)."""
    v = (value or "").strip().lower()
    for name in allowed:
        if name.lower() == v:
            return name
    for name in allowed:
        if v and (v in name.lower() or name.lower() in v):
            return name
    return "Uncategorized" if "Uncategorized" in allowed else next(iter(allowed))


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def categorize(items: list[RawLineItem], project_id: str) -> list[AssetRecord]:
    """Categorize items via the deepagents agent, falling back on failure."""
    if not items:
        return []
    allowed = set(category_names())

    try:
        agent = _build_agent()
        result = agent.invoke(
            {"messages": [{"role": "user", "content": _items_payload(items, project_id)}]}
        )
        text = _last_message_text(result)
        records = _finalize(extract_json_list(text), allowed)
        if records:
            return records
        print("[stage2] agent returned no usable records; using fallback.")
    except Exception as exc:
        print(f"[stage2] agent path failed ({exc}); using fallback.")

    return _categorize_fallback(items, project_id, allowed)


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
    items: list[RawLineItem], project_id: str, allowed: set[str]
) -> list[AssetRecord]:
    """Deterministic path: fetch the document, then one structured LLM call.

    Used when the local model can't drive tools. Still 'reasons' over the
    fetched document to pick the most related line item and category.
    """
    rows = _store.get_by_project_id(project_id)
    doc_items = [r["content"] for r in rows]
    cats = "\n".join(f"- {c.name}: {c.description}" for c in load_categories())

    prompt = (
        "You classify purchased IT assets for an asset register.\n\n"
        f"Allowed categories (choose the name exactly):\n{cats}\n\n"
        f"Project {project_id} known line items (for matching / enriching "
        f"descriptions):\n{json.dumps(doc_items, indent=2)}\n\n"
        "Receipt line items to classify:\n"
        f"{json.dumps([i.model_dump() for i in items], indent=2)}\n\n"
        "For each receipt item: find the most related known line item and use it "
        "to enrich the description; assign exactly one allowed category; keep the "
        "receipt quantity and price. Respond with ONLY a JSON array of objects "
        'with keys "name", "description", "category", "quantity", "price".'
    )

    from .config import get_settings

    resp = get_openai_client().chat.completions.create(
        model=get_settings().llm_model,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.choices[0].message.content or ""
    records = _finalize(extract_json_list(text), allowed)
    if records:
        return records

    # Last-resort: keep the items, mark them Uncategorized so we never drop data.
    return [
        AssetRecord(
            name=i.name,
            description=i.description,
            category="Uncategorized" if "Uncategorized" in allowed else next(iter(allowed)),
            quantity=i.quantity,
            price=i.price,
        )
        for i in items
    ]
