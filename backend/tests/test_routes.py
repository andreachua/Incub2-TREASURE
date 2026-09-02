"""The HTTP surface is frozen: the reorganisation must not add, drop or reorder a route.

The front end talks to this API over HTTP only, so the route table is the whole
contract between the two halves of the repo. Three things are checked:

1. the exact set of paths and methods,
2. that ``/api/review/latest`` is declared *before* ``/api/review/{job_id}``, and
3. that no route at all sits behind an earlier pattern that would capture it.

(2) and (3) matter because FastAPI matches in declaration order — reverse those
two and `latest` is captured as a job id, so the front end's default screen 404s
with no error anywhere to explain it.

Run:  uv run python -m pytest tests/ -q
  or: uv run python tests/test_routes.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402

# Captured from the pre-reorganisation server. Keep sorted.
EXPECTED: set[tuple[str, tuple[str, ...]]] = {
    ("/api/assets", ("GET",)),
    ("/api/categories", ("GET",)),
    ("/api/documents/po/{ref_no}", ("GET",)),
    ("/api/documents/receipt/{receipt_id}", ("GET",)),
    ("/api/documents/receipt/{receipt_id}/info", ("GET",)),
    ("/api/documents/sow/{ref_no}", ("GET",)),
    ("/api/jobs/{job_id}/status", ("GET",)),
    ("/api/review/latest", ("GET",)),
    ("/api/review/{job_id}", ("GET",)),
    ("/api/review/{job_id}/complete", ("POST",)),
    ("/api/summary", ("GET",)),
    ("/app", ("GET",)),
    ("/docs", ("GET", "HEAD")),
    ("/docs/oauth2-redirect", ("GET", "HEAD")),
    ("/health", ("GET",)),
    ("/invoice", ("POST",)),
    ("/openapi.json", ("GET", "HEAD")),
    ("/receipts", ("POST",)),
    ("/redoc", ("GET", "HEAD")),
    ("/results/{job_id}", ("GET",)),
}

# Static mounts, present only when their directory exists. /static ships in the
# image; /app-assets needs the repo's frontend/ mounted, which the API container
# does and a bare test container does not. Conditional, so not part of the
# frozen set — but if one is mounted it must be mounted at the right path.
CONDITIONAL: set[tuple[str, tuple[str, ...]]] = {
    ("/static", ()),
    ("/app-assets", ()),
}

# Routes added after the freeze. Listed separately so the diff stays honest
# about what is new rather than quietly widening EXPECTED.
ADDED: set[tuple[str, tuple[str, ...]]] = {
    # Add Asset: a blank form to fill in, and somewhere to send it.
    ("/api/review/blank", ("GET",)),
    ("/api/assets", ("POST",)),
}


def _walk(routes):
    """Flatten included routers.

    FastAPI 0.141 keeps an ``include_router`` call as a single ``_IncludedRouter``
    entry in ``app.routes`` rather than splicing its routes in, so iterating
    ``app.routes`` directly sees seven opaque objects instead of the real paths.
    """
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            yield from _walk(original.routes)
        else:
            yield route


def _paths() -> list[str]:
    return [r.path for r in _walk(app.routes)]


def _actual() -> set[tuple[str, tuple[str, ...]]]:
    return {
        (r.path, tuple(sorted(getattr(r, "methods", None) or ())))
        for r in _walk(app.routes)
    }


def test_route_table_is_unchanged() -> None:
    actual = _actual()
    expected = EXPECTED | ADDED
    assert not (expected - actual), f"routes lost: {sorted(expected - actual)}"
    unexpected = actual - expected - CONDITIONAL
    assert not unexpected, f"routes added: {sorted(unexpected)}"


def test_review_latest_is_declared_before_job_id() -> None:
    paths = _paths()
    assert paths.index("/api/review/latest") < paths.index("/api/review/{job_id}"), (
        "/api/review/latest must be declared first, or FastAPI matches it as a job id"
    )


def test_no_route_is_shadowed_by_an_earlier_one() -> None:
    """No path may be captured by a parameterised path declared before it.

    This is the general form of the /api/review/latest rule, so grouping routes
    into routers cannot reintroduce that class of bug somewhere else.
    """
    paths = _paths()
    patterns = [
        (p, re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", re.escape(p).replace(r"\{", "{").replace(r"\}", "}")) + "$"))
        for p in paths
    ]
    shadowed = [
        (later, earlier)
        for i, (later, _) in enumerate(patterns)
        for earlier, pattern in patterns[:i]
        if earlier != later and pattern.match(later)
    ]
    assert not shadowed, f"routes unreachable behind an earlier pattern: {shadowed}"


if __name__ == "__main__":
    test_route_table_is_unchanged()
    test_review_latest_is_declared_before_job_id()
    test_no_route_is_shadowed_by_an_earlier_one()
    print(f"OK — {len(_actual())} routes, declaration order correct, none shadowed")
