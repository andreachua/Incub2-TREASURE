"""Runtime configuration: environment settings and the editable category list."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

from .models import Category

# Load .env once at import time (no-op if the file is absent).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

CATEGORIES_PATH = _PROJECT_ROOT / "config" / "categories.yaml"


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

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def load_categories(path: Path | None = None) -> list[Category]:
    """Load the editable category list. Re-reads the YAML on each call so edits
    are picked up without restarting."""
    path = path or CATEGORIES_PATH
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("categories", [])
    return [Category(**item) for item in raw]


def category_names(path: Path | None = None) -> list[str]:
    return [c.name for c in load_categories(path)]
