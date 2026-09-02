"""The process-wide singletons the routers share.

These are module globals built at import, exactly as they were when this was one
file. That is deliberate and not an oversight: converting them to FastAPI
``Depends`` would change when connections are opened and closed, and this
reorganisation is meant to move code, not behaviour. ``PostgresStore.__init__``
only reads settings — it does not connect — so import-time construction is cheap.
"""

from __future__ import annotations

import redis

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.store.postgres_store import PostgresStore

log = get_logger("api")
settings = get_settings()
store = PostgresStore()
redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
