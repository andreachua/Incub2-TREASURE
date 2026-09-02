"""Liveness, and whether the two backing services are reachable."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import redis_client, store

router = APIRouter()


@router.get("/health")
def health() -> dict:
    db_ok = True
    try:
        store.get_receipt(-1)  # cheap round-trip
    except Exception:
        db_ok = False
    try:
        redis_ok = bool(redis_client.ping())
    except Exception:
        redis_ok = False
    return {"status": "ok", "db": db_ok, "redis": redis_ok}
