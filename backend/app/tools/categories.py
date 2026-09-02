"""The category taxonomy, as prompt text and as an agent tool.

``format_categories`` renders the system's taxonomy the way both the agent and
the deterministic fallback want to see it; ``make_list_categories`` binds that
rendering to one system (oxn / ehab) as a LangChain tool.

The YAML is re-read on every call (see ``core.config.load_categories``), so
editing ``config/categories.<system>.yaml`` changes behaviour on the next run
with no code change and no restart.
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.core.config import DEFAULT_SYSTEM, load_categories


def format_categories(system: str = DEFAULT_SYSTEM) -> str:
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


def make_list_categories(system: str):
    """Build a `list_categories` tool bound to a system's category set."""

    @tool
    def list_categories() -> str:
        """List the allowed categories, grouped by Assets / Inventories / Expenses.

        Always choose the category `name` exactly as written here.
        """
        return format_categories(system)

    return list_categories
