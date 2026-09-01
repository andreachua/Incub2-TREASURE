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
from ..logging_config import get_logger

log = get_logger("store")

# The final asset-register table (Stage 3 output). "No" is a quoted identifier
# (an auto-increment running number).
MAR_DDL = """
CREATE TABLE IF NOT EXISTS mar (
    "No"          SERIAL PRIMARY KEY,
    asset_id      TEXT,
    asset_model   TEXT,
    serial_no     TEXT,
    description   TEXT,
    quantity      NUMERIC,
    location      TEXT,
    status        TEXT,
    price         NUMERIC,
    main_category TEXT,
    sub_category  TEXT,
    reasoning     TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- ensure the column exists on tables created before `reasoning` was added
ALTER TABLE mar ADD COLUMN IF NOT EXISTS reasoning TEXT;
"""

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

CREATE TABLE IF NOT EXISTS receipts (
    id           SERIAL PRIMARY KEY,
    system       TEXT NOT NULL,
    filename     TEXT,
    content_type TEXT,
    data         BYTEA NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
""" + MAR_DDL

MAR_COLUMNS = (
    "asset_id", "asset_model", "serial_no", "description", "quantity",
    "location", "status", "price", "main_category", "sub_category", "reasoning",
)

# Statement of Work + Purchase Order documents. sow.ref_no is the PK; po.ref_no
# is a FK to it (many POs -> 1 SOW).
PO_SOW_DDL = """
CREATE TABLE IF NOT EXISTS sow (
    ref_no   TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    category TEXT,
    content  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS po (
    id       SERIAL PRIMARY KEY,
    ref_no   TEXT REFERENCES sow(ref_no),
    filename TEXT NOT NULL UNIQUE,
    category TEXT,
    content  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_po_ref_no ON po (ref_no);
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
        log.info("ensuring schema (project_documents, receipts, mar) on %s",
                 self.dsn.rsplit("@", 1)[-1])
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

    # -- receipts (uploaded files) ------------------------------------------

    def save_receipt(
        self,
        system: str,
        filename: str | None,
        content_type: str | None,
        data: bytes,
    ) -> int:
        """Store an uploaded receipt/invoice and return its new id."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO receipts (system, filename, content_type, data)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (system, filename, content_type, data),
            )
            row = cur.fetchone()
            conn.commit()
            rid = int(row["id"])
            log.info("saved receipt id=%d system=%s filename=%s (%d bytes)",
                     rid, system, filename, len(data))
            return rid

    def get_receipt(self, receipt_id: int) -> dict[str, Any] | None:
        """Fetch a stored receipt by id. ``data`` is returned as ``bytes``."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, system, filename, content_type, data
                FROM receipts
                WHERE id = %s
                """,
                (receipt_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            if isinstance(row.get("data"), memoryview):
                row["data"] = bytes(row["data"])
            return row

    # -- mar (final asset register / Stage 3 output) ------------------------

    def insert_mar(self, rows: list[dict[str, Any]]) -> list[int]:
        """Insert asset rows into the ``mar`` table, creating it if needed.

        Each ``row`` maps the MAR_COLUMNS keys (missing keys become NULL).
        Returns the generated "No" values in order.
        """
        if not rows:
            return []
        placeholders = ", ".join(["%s"] * len(MAR_COLUMNS))
        columns = ", ".join(MAR_COLUMNS)
        sql = f'INSERT INTO mar ({columns}) VALUES ({placeholders}) RETURNING "No"'
        ids: list[int] = []
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(MAR_DDL)  # ensure the table exists
            for row in rows:
                cur.execute(sql, tuple(row.get(col) for col in MAR_COLUMNS))
                ids.append(int(cur.fetchone()["No"]))
            conn.commit()
        log.debug("inserted %d row(s) into mar (No=%s)", len(ids), ids)
        return ids

    # -- po / sow documents -------------------------------------------------

    def init_po_sow_schema(self) -> None:
        log.info("ensuring po/sow schema on %s", self.dsn.rsplit("@", 1)[-1])
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(PO_SOW_DDL)
            conn.commit()

    def upsert_sow(self, ref_no: str, filename: str, category: str | None, content: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sow (ref_no, filename, category, content)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (ref_no) DO UPDATE
                    SET filename = EXCLUDED.filename,
                        category = EXCLUDED.category,
                        content  = EXCLUDED.content
                """,
                (ref_no, filename, category, content),
            )
            conn.commit()

    def sow_ref_exists(self, ref_no: str) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM sow WHERE ref_no = %s", (ref_no,))
            return cur.fetchone() is not None

    def get_po_by_ref(self, ref_no: str) -> list[dict[str, Any]]:
        """Return PO rows matching a ref_no (may be several — many PO -> 1 SOW)."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, ref_no, filename, category, content FROM po "
                "WHERE ref_no = %s ORDER BY id",
                (ref_no,),
            )
            return list(cur.fetchall())

    def get_sow_by_ref(self, ref_no: str) -> dict[str, Any] | None:
        """Return the SOW row for a ref_no, or None."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT ref_no, filename, category, content FROM sow WHERE ref_no = %s",
                (ref_no,),
            )
            return cur.fetchone()

    def upsert_po(self, ref_no: str | None, filename: str, category: str | None, content: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO po (ref_no, filename, category, content)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (filename) DO UPDATE
                    SET ref_no   = EXCLUDED.ref_no,
                        category = EXCLUDED.category,
                        content  = EXCLUDED.content
                """,
                (ref_no, filename, category, content),
            )
            conn.commit()
