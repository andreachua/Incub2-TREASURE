"""Factories for talking to the local OpenAI-compatible endpoint.

Both stages share one endpoint/model so behaviour stays consistent:
- Stage 1 uses the raw ``openai`` client (vision messages).
- Stage 2 uses a LangChain ``ChatOpenAI`` (so deepagents can drive it).
"""

from __future__ import annotations

from functools import lru_cache

from openai import OpenAI

from .config import get_settings


@lru_cache(maxsize=1)
def get_openai_client() -> OpenAI:
    """Raw OpenAI-compatible client pointed at the local endpoint."""
    s = get_settings()
    return OpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key)


@lru_cache(maxsize=1)
def get_chat_model():
    """LangChain chat model on the same endpoint, for the deepagents agent."""
    # Imported lazily so Stage 1 doesn't require langchain to be installed/loaded.
    from langchain_openai import ChatOpenAI

    s = get_settings()
    return ChatOpenAI(
        base_url=s.llm_base_url,
        api_key=s.llm_api_key,
        model=s.llm_model,
        temperature=0,
    )
