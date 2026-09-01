"""Runtime configuration: environment settings and the editable category list.

Path resolution is tolerant of layout: the backend may live in its own
``backend/`` directory with ``config/`` and ``.env`` either alongside it or at
the repository root. We look in the backend dir first, then the repo root.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

from .models import Category

_BACKEND_ROOT = Path(__file__).resolve().parent.parent  # .../backend
_REPO_ROOT = _BACKEND_ROOT.parent                       # repo root


def _first_existing(*candidates: Path) -> Path:
    """Return the first path that exists, else the first candidate."""
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


# Load .env from the backend dir first, then the repo root (no-op if absent).
_ENV_PATH = _first_existing(_BACKEND_ROOT / ".env", _REPO_ROOT / ".env")
load_dotenv(_ENV_PATH)

# Two systems share the same processing; only the category set differs.
VALID_SYSTEMS = ("oxn", "ehab")
DEFAULT_SYSTEM = "oxn"


def categories_path(system: str = DEFAULT_SYSTEM) -> Path:
    """Resolve the category YAML for a system (backend dir first, repo root fallback)."""
    system = (system or DEFAULT_SYSTEM).lower()
    return _first_existing(
        _BACKEND_ROOT / "config" / f"categories.{system}.yaml",
        _REPO_ROOT / "config" / f"categories.{system}.yaml",
    )


# Kept for convenience / external references (defaults to the oxn set).
CATEGORIES_PATH = categories_path(DEFAULT_SYSTEM)


class Settings(BaseModel):
    """All environment-driven configuration in one place."""

    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://192.168.100.1:5000/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "not-needed")
    llm_model: str = os.getenv("LLM_MODEL", "gemma-4-31B-it")

    postgres_host: str = os.getenv("POSTGRES_HOST", "localhost")
    postgres_port: int = int(os.getenv("POSTGRES_PORT", "5432"))
    postgres_db: str = os.getenv("POSTGRES_DB", "assets")
    postgres_user: str = os.getenv("POSTGRES_USER", "assets")
    postgres_password: str = os.getenv("POSTGRES_PASSWORD", "assets")

    embed_model: str = os.getenv(
        "EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
    )

    # Invoice enrichment: only line items priced at/above this are checked for
    # price-implausibility / generic-bundle decomposition.
    price_sanity_threshold: float = float(os.getenv("PRICE_SANITY_THRESHOLD", "1000"))

    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # --- Redis (frontend -> backend job queue) ---
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    redis_job_queue: str = os.getenv("REDIS_JOB_QUEUE", "object-categorization")
    redis_result_prefix: str = os.getenv("REDIS_RESULT_PREFIX", "assets:result:")
    redis_results_channel: str = os.getenv("REDIS_RESULTS_CHANNEL", "assets:results")
    redis_result_ttl: int = int(os.getenv("REDIS_RESULT_TTL", "3600"))

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def load_categories(system: str = DEFAULT_SYSTEM) -> list[Category]:
    """Load the editable category list for a system. Re-reads the YAML on each
    call so edits are picked up without restarting."""
    path = categories_path(system)
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("categories", []) or []
    return [Category(**item) for item in raw]


def category_names(system: str = DEFAULT_SYSTEM) -> list[str]:
    return [c.name for c in load_categories(system)]


def group_for_category(name: str, system: str = DEFAULT_SYSTEM) -> str:
    """Return the parent group (Assets/Inventories/Expenses) for a category name."""
    for c in load_categories(system):
        if c.name == name:
            return c.group
    return ""
