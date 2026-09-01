"""Central logging setup for the pipeline.

Call ``configure_logging()`` once at each entry point (API, consumer, CLI); use
``get_logger(name)`` everywhere else. Level is controlled by ``LOG_LEVEL`` (env),
default ``INFO``.
"""

from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False


def configure_logging(level: str | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    lvl = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    # Quiet noisy third-party per-request loggers (one line per HTTP call).
    for noisy in ("httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger (configures logging on first use)."""
    configure_logging()
    return logging.getLogger(name)
