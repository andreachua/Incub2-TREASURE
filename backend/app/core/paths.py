"""The two filesystem roots the rest of the application resolves against.

These live in one module on purpose. They used to be recomputed inline as
``Path(__file__).resolve().parent.parent`` in each module that needed them,
which silently depends on how deeply that module is nested: move the file one
directory down and the root points at the wrong place, ``config/`` and ``.env``
stop resolving, ``load_categories()`` returns an empty list, and every item gets
classified as "" — with no exception raised anywhere.

Anchoring them here means the depth is stated once, next to the assertion that
checks it.
"""

from __future__ import annotations

from pathlib import Path

# .../backend/app/core/paths.py -> parents[0]=core, [1]=app, [2]=backend
BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent

CONFIG_DIR = BACKEND_DIR / "config"
STATIC_DIR = BACKEND_DIR / "static"

# If this module is ever moved, this fails loudly at import instead of silently
# degrading the category set at runtime.
assert (BACKEND_DIR / "pyproject.toml").is_file(), (
    f"BACKEND_DIR resolved to {BACKEND_DIR}, which has no pyproject.toml — "
    "app/core/paths.py has moved and parents[2] is now wrong"
)


def first_existing(*candidates: Path) -> Path:
    """Return the first path that exists, else the first candidate.

    Callers use this to look in the backend directory before the repository
    root, so the backend works both standalone and inside this repo.
    """
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]
