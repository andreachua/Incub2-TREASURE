"""PostgreSQL + pgvector implementation of DocumentStore.

Primary path: key lookup by ``project_id`` (no embeddings required).
Optional path: ``search_line_items`` ranks line items by vector similarity,
used only if the ``embedding`` column has been populated.
"""

from __future__ import annotations

import json
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..config import get_settings

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS project_documents (
    id          SERIAL PRIMARY KEY,
    project_id  TEXT NOT NULL,
    title       TEXT,
    content     JSONB NOT NULL,
    embedding   vector(384)
);

CREATE INDEX IF NOT EXISTS idx_project_documents_project_id
    ON project_documents (project_id);
"""


def _vec_literal(vector: list[float] | None) -> str | None:
    """Render a float list as a pgvector text literal, e.g. '[0.1,0.2]'."""
    if vector is None:
        return None
    return "[" + ",".join(f"{x:.6f}" for x in vector) + "]"


class PostgresStore:
    """Concrete DocumentStore backed by Postgres/pgvector."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or get_settings().postgres_dsn

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def init_schema(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            conn.commit()

    def get_by_project_id(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT project_id, title, content
                FROM project_documents
                WHERE project_id = %s
                ORDER BY id
                """,
                (project_id,),
            )
            return list(cur.fetchall())

    def search_line_items(
        self, project_id: str, query: str, k: int = 5
    ) -> list[dict[str, Any]]:
        # Embed the query with the same model used to seed embeddings.
        try:
            from ..embeddings import embed_text
        except Exception:
            return []

        try:
            vector = embed_text(query)
        except Exception:
            return []

        with self._connect() as conn, conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    SELECT project_id, title, content,
                           embedding <=> %s::vector AS distance
                    FROM project_documents
                    WHERE project_id = %s AND embedding IS NOT NULL
                    ORDER BY distance
                    LIMIT %s
                    """,
                    (_vec_literal(vector), project_id, k),
                )
                return list(cur.fetchall())
            except Exception:
                # No embeddings populated / pgvector op unavailable — caller
                # falls back to the full document.
                return []

    # -- helpers used by the seed script ------------------------------------

    def upsert_document(
        self,
        project_id: str,
        title: str,
        content: dict[str, Any] | list[Any],
        embedding: list[float] | None = None,
    ) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO project_documents (project_id, title, content, embedding)
                VALUES (%s, %s, %s, %s)
                """,
                (project_id, title, json.dumps(content), _vec_literal(embedding)),
            )
            conn.commit()

    def clear(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE project_documents RESTART IDENTITY")
            conn.commit()
