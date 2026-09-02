"""HTTP client for the asset-pipeline FastAPI backend.

Streamlit renders server-side, so these calls go out from the container over
the compose network (``API_BASE=http://api:8000``) — the browser never talks to
the API directly and there is no CORS involved.

The endpoint list mirrors what the design bundle's bridge used, plus the three
document routes added for the source-document viewer.
"""

from __future__ import annotations

import os
from typing import Any

import requests
import streamlit as st

API_BASE = os.getenv("API_BASE", "http://localhost:8000").rstrip("/")
TIMEOUT = float(os.getenv("API_TIMEOUT", "30"))

# Short TTL: every widget interaction reruns the script, and the register does
# not change between two keystrokes. Writes call clear_caches().
_TTL = 5


class ApiError(RuntimeError):
    """A non-2xx response, carrying the backend's `detail` where it sent one."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def _request(method: str, path: str, **kwargs: Any) -> Any:
    url = f"{API_BASE}{path}"
    try:
        res = requests.request(method, url, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise ApiError(f"cannot reach the API at {API_BASE}: {exc}") from exc
    if not res.ok:
        detail = res.text
        try:
            detail = res.json().get("detail", detail)
        except ValueError:
            pass
        raise ApiError(str(detail) or res.reason, res.status_code)
    if not res.content:
        return None
    if res.headers.get("content-type", "").startswith("application/json"):
        return res.json()
    return res.content


def _get(path: str, **params: Any) -> Any:
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    return _request("GET", path, params=clean, headers={"Accept": "application/json"})


# --------------------------------------------------------------------------- #
# Register + review
# --------------------------------------------------------------------------- #

@st.cache_data(ttl=_TTL, show_spinner=False)
def get_summary() -> dict:
    return _get("/api/summary")


@st.cache_data(ttl=_TTL, show_spinner=False)
def get_categories(system: str = "oxn") -> list[dict]:
    return _get("/api/categories", system=system)


@st.cache_data(ttl=_TTL, show_spinner=False)
def get_assets(search: str = "", limit: int = 200) -> dict:
    return _get("/api/assets", search=search, limit=limit)


@st.cache_data(ttl=_TTL, show_spinner=False)
def get_review(job_id: str | None = None, index: int = 0) -> dict | None:
    """The review payload for a job, or the latest pending one.

    Returns None when nothing has been extracted yet — a 404 here means "no
    record is waiting", not a failure.
    """
    path = f"/api/review/{job_id}" if job_id else "/api/review/latest"
    try:
        return _get(path, index=index)
    except ApiError as exc:
        if exc.status == 404:
            return None
        raise


def get_job_status(job_id: str) -> dict:
    """Never cached — this is the upload poll target."""
    return _get(f"/api/jobs/{job_id}/status")


def complete_review(job_id: str, fields: dict[str, str],
                    record_index: int = 0) -> dict:
    """Register the reviewed record.

    ``record_index`` says *which* of the job's line items was on screen —
    without it the backend can only assume the first, and completing record 3
    overwrites record 1.
    """
    return _request(
        "POST",
        f"/api/review/{job_id}/complete",
        json={"fields": fields, "record_index": record_index},
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_blank_review() -> dict:
    """An empty registration form — every field marked for manual entry."""
    return _get("/api/review/blank")


def create_asset(fields: dict[str, str], job_id: str = "",
                 system: str = "oxn", record_index: int = 0) -> dict:
    """Add an asset. With a job_id the backend treats it as a completed review."""
    return _request(
        "POST",
        "/api/assets",
        json={"fields": fields, "job_id": job_id, "system": system,
              "record_index": record_index},
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )


def upload_invoice(filename: str, data: bytes, content_type: str,
                   system: str = "oxn") -> dict:
    return _request(
        "POST",
        "/invoice",
        files={"file": (filename, data, content_type or "application/octet-stream")},
        data={"system": system},
    )


# --------------------------------------------------------------------------- #
# Source documents
# --------------------------------------------------------------------------- #

@st.cache_data(ttl=300, show_spinner=False)
def get_document_info(url: str) -> dict:
    """Type, name and page count for a stored document, before fetching it."""
    return _get(f"{url}/info")


@st.cache_data(ttl=300, show_spinner=False)
def get_document_bytes(url: str) -> tuple[bytes, str]:
    """Fetch a binary document; returns (bytes, content_type)."""
    res = requests.get(f"{API_BASE}{url}", timeout=TIMEOUT)
    if not res.ok:
        raise ApiError(f"{res.status_code} fetching {url}", res.status_code)
    return res.content, res.headers.get("content-type", "application/octet-stream")


@st.cache_data(ttl=300, show_spinner=False)
def get_document_text(url: str) -> list[dict]:
    """Fetch a PO/SOW document; returns its `documents` list."""
    payload = _get(url)
    return payload.get("documents", []) if isinstance(payload, dict) else []


def clear_caches() -> None:
    """Drop cached reads after a write so the next rerun shows the new state."""
    get_summary.clear()
    get_categories.clear()
    get_assets.clear()
    get_review.clear()
    get_blank_review.clear()
