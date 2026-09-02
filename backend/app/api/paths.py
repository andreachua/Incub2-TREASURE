"""Filesystem locations the HTTP layer serves from.

The roots come from ``core.paths`` rather than being recomputed here from
``Path(__file__)``, so this module can move without silently pointing at the
wrong directory and 404-ing the design bundle.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.core.paths import REPO_ROOT, STATIC_DIR

__all__ = ["STATIC_DIR", "FRONTEND_DIR", "find_frontend_html"]

# The exported design bundle. In the container the repo's frontend/ is mounted
# elsewhere, so allow an override.
FRONTEND_DIR = Path(
    os.getenv(
        "MAR_FRONTEND_DIR",
        REPO_ROOT / "frontend" / "My Assets Record (MAR)_Project Assets",
    )
)


def find_frontend_html() -> Path | None:
    """The bundle's filename is fixed, but tolerate a renamed export."""
    exact = FRONTEND_DIR / "My Assets Record (MAR)_Incubation.html"
    if exact.is_file():
        return exact
    if FRONTEND_DIR.is_dir():
        for candidate in sorted(FRONTEND_DIR.glob("*.html")):
            return candidate
    return None
