"""The machinery every deep agent in this application shares.

Before this module, each of the three agents built its own agent, pulled the
text out of its own result, and parsed its own JSON — including two byte-for-byte
copies of ``last_message_text``. That is all here now, once.

What is deliberately **not** here: the try/except envelope and the log strings.
Each agent logs a different, human-meaningful message ("detect: agent failed
(%s); keeping items" versus "Stage 2: agent path failed (%s); using fallback")
and each falls back differently — one keeps its items untouched, another
re-classifies from scratch. Centralising that would change log output, which is
the one observable thing a behaviour-preserving refactor can still get wrong. So
callers keep their own four-line envelope and their own fallback; this module
removes only the mechanical duplication.

Everything here runs against the local OpenAI-compatible endpoint configured in
``.env`` (``LLM_BASE_URL`` / ``LLM_MODEL``) via ``core.llm``. There is no other
model provider.
"""

from __future__ import annotations

from typing import Any

from app.core.json_utils import extract_json, extract_json_list


def build_agent(*, tools: list, system_prompt: str, model: Any = None) -> Any:
    """A deepagents agent on the local chat model.

    ``deepagents`` and ``core.llm.get_chat_model`` are imported inside the
    function on purpose: it keeps langchain and langgraph out of the import
    graph of processes that never run an agent (the API server, Stage 1).
    """
    from deepagents import create_deep_agent

    from app.core.llm import get_chat_model

    return create_deep_agent(
        model=model or get_chat_model(),
        tools=tools,
        system_prompt=system_prompt,
    )


def last_message_text(result: Any) -> str:
    """The text of the agent's final message.

    Tolerates the three shapes local models come back in: a message object with
    ``.content``, a plain dict, and a list of content blocks.
    """
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


def invoke_agent(agent: Any, payload: str) -> str:
    """Run one turn and return the final message text."""
    return last_message_text(
        agent.invoke({"messages": [{"role": "user", "content": payload}]})
    )


def invoke_agent_json(agent: Any, payload: str) -> list:
    """Run one turn and parse a JSON array out of the answer.

    Returns ``[]`` when the model produced nothing parseable, so callers can
    treat "no usable output" and "agent said nothing" the same way.
    """
    try:
        return extract_json_list(invoke_agent(agent, payload))
    except ValueError:
        return []


def invoke_agent_object(agent: Any, payload: str) -> dict:
    """As ``invoke_agent_json``, for agents that answer with a single object."""
    try:
        data = extract_json(invoke_agent(agent, payload), prefer="object")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
