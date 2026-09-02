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

from app.core.config import get_settings
from app.core.logging_config import get_logger

log = get_logger("store")

# The final asset-register table (Stage 3 output). "No" is a quoted identifier
# (an auto-increment running number).
MAR_DDL = """
CREATE TABLE IF NOT EXISTS mar (
    "No"                        SERIAL PRIMARY KEY,
    asset_id                    TEXT,
    asset_model                 TEXT,
    serial_no                   TEXT,
    description                 TEXT,
    quantity                    NUMERIC,
    location                    TEXT,
    status                      TEXT,
    price                       NUMERIC,
    main_category               TEXT,
    sub_category                TEXT,
    reasoning                   TEXT,
    invoice_no                  TEXT,
    po_no                       TEXT,
    do_no                       TEXT,
    do_date                     TEXT,
    gl_date                     TEXT,
    vendor                      TEXT,
    project                     TEXT,
    period_contract             TEXT,
    purchase_type               TEXT,
    taggable                    TEXT,
    asset_capitalisation_date   TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- ensure columns exist on tables created before they were added
ALTER TABLE mar ADD COLUMN IF NOT EXISTS reasoning TEXT;
ALTER TABLE mar ADD COLUMN IF NOT EXISTS invoice_no TEXT;
ALTER TABLE mar ADD COLUMN IF NOT EXISTS po_no TEXT;
ALTER TABLE mar ADD COLUMN IF NOT EXISTS do_no TEXT;
ALTER TABLE mar ADD COLUMN IF NOT EXISTS do_date TEXT;
ALTER TABLE mar ADD COLUMN IF NOT EXISTS gl_date TEXT;
ALTER TABLE mar ADD COLUMN IF NOT EXISTS vendor TEXT;
ALTER TABLE mar ADD COLUMN IF NOT EXISTS project TEXT;
ALTER TABLE mar ADD COLUMN IF NOT EXISTS period_contract TEXT;
ALTER TABLE mar ADD COLUMN IF NOT EXISTS purchase_type TEXT;
ALTER TABLE mar ADD COLUMN IF NOT EXISTS taggable TEXT;
ALTER TABLE mar ADD COLUMN IF NOT EXISTS asset_capitalisation_date TEXT;

-- Register/review fields the UI needs. tag_no, custodian and mindef_cat are
-- filled by the human reviewer; parent_no links a component row to its parent
-- asset; job_id ties a row back to the pipeline run that produced it.
ALTER TABLE mar ADD COLUMN IF NOT EXISTS tag_no TEXT;
ALTER TABLE mar ADD COLUMN IF NOT EXISTS custodian TEXT;
ALTER TABLE mar ADD COLUMN IF NOT EXISTS mindef_cat TEXT;
ALTER TABLE mar ADD COLUMN IF NOT EXISTS material_number TEXT;
ALTER TABLE mar ADD COLUMN IF NOT EXISTS receiving_plant TEXT;
ALTER TABLE mar ADD COLUMN IF NOT EXISTS receiving_sloc TEXT;
ALTER TABLE mar ADD COLUMN IF NOT EXISTS job_id TEXT;
ALTER TABLE mar ADD COLUMN IF NOT EXISTS receipt_id TEXT;
ALTER TABLE mar ADD COLUMN IF NOT EXISTS parent_no INTEGER;
-- Which purchase order this row was matched to and why. TEXT holding JSON, not
-- JSONB: insert_mar passes raw Python values straight to cur.execute, and
-- psycopg3 will not adapt a dict to jsonb without an explicit Jsonb() wrapper.
ALTER TABLE mar ADD COLUMN IF NOT EXISTS context_match TEXT;

CREATE INDEX IF NOT EXISTS idx_mar_parent_no ON mar (parent_no);
CREATE INDEX IF NOT EXISTS idx_mar_job_id ON mar (job_id);
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
    "invoice_no", "po_no", "do_no", "do_date", "gl_date", "vendor", "project",
    "period_contract", "purchase_type", "taggable", "asset_capitalisation_date",
    "tag_no", "custodian", "mindef_cat", "material_number", "receiving_plant",
    "receiving_sloc", "job_id", "receipt_id", "parent_no", "context_match",
)

# Columns a reviewer is allowed to write through the review API. Deliberately
# excludes "No", created_at and parent_no (linkage is set by the server) and
# context_match (the crawler's evidence is a record of what happened, not a
# field to edit). Note this set is derived by *subtraction*: anything added to
# MAR_COLUMNS becomes reviewer-writable unless it is also excluded here.
MAR_EDITABLE_COLUMNS = frozenset(MAR_COLUMNS) - {
    "parent_no", "job_id", "receipt_id", "context_match",
}

# Column list for reads: the running number, every data column, and created_at.
_MAR_SELECT = '"No", ' + ", ".join(MAR_COLUMNS) + ", created_at"

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

-- One searchable row per purchase order, derived from po.content and the
-- matching sow.content by app/ingest/parse.py. A separate table rather than
-- columns on `po`, so the pipeline's existing reads of `po` are untouched and
-- the index can be rebuilt independently of the documents themselves.
--
-- po_id is the key, not ref_no: po.ref_no is nullable and the schema permits
-- several purchase orders under one reference.
CREATE TABLE IF NOT EXISTS po_index (
    po_id          INTEGER PRIMARY KEY REFERENCES po(id) ON DELETE CASCADE,
    ref_no         TEXT,
    filename       TEXT,
    category       TEXT,
    vendor         TEXT,
    vendor_uen     TEXT,
    officer        TEXT,
    total_value    NUMERIC,
    item_names     TEXT,      -- ' | '-joined, kept flat so it is prompt-ready
    payment_events TEXT,      -- JSON array text: [{"no":1,"description":…,"amount":…}]
    sow_items      TEXT,      -- ' | '-joined SOW Table A-1 items
    keywords       TEXT       -- vendor || category || item_names || sow_items
);

CREATE INDEX IF NOT EXISTS idx_po_index_ref   ON po_index (ref_no);
CREATE INDEX IF NOT EXISTS idx_po_index_total ON po_index (total_value);
"""

# Fuzzy matching support. Run separately and tolerantly: CREATE EXTENSION needs
# privileges this role may not have everywhere, and a deployment without it
# should degrade to the Python fallbacks rather than fail to start.
#
# Trigram rather than tsvector on purpose. What distinguishes these documents is
# proper nouns and digit strings — "ApexForge", "NightHawk Vision", "1000672008"
# — which to_tsvector stems and splits badly. And the failure being fixed is a
# *misread* reference: "1000672OO8" shares no lexeme with "1000672008" but has
# high trigram similarity. pg_trgm also returns a similarity score that feeds
# the match confidence directly.
#
# Honest note: at 25 rows the planner will seq-scan and never touch these GIN
# indexes. The functional win is the extensions (similarity, word_similarity,
# levenshtein); the indexes cost nothing and matter only if the corpus grows.
PO_SOW_FUZZY_DDL = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;

CREATE INDEX IF NOT EXISTS idx_po_content_trgm  ON po  USING gin (content gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_sow_content_trgm ON sow USING gin (content gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_po_index_keywords_trgm
    ON po_index USING gin (keywords gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_po_index_vendor_trgm
    ON po_index USING gin (vendor gin_trgm_ops);
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
            from app.core.embeddings import embed_text
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

    # -- mar reads (asset register UI) --------------------------------------

    def list_assets(
        self, search: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        """Top-level asset rows (components excluded) plus the total match count.

        ``search`` matches case-insensitively against tag number, serial number,
        model and vendor — the four things the UI's search box advertises.
        """
        where = ["parent_no IS NULL"]
        params: list[Any] = []
        term = (search or "").strip()
        if term:
            where.append(
                "(tag_no ILIKE %s OR serial_no ILIKE %s"
                " OR asset_model ILIKE %s OR vendor ILIKE %s)"
            )
            params.extend([f"%{term}%"] * 4)
        clause = " AND ".join(where)

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(MAR_DDL)
            cur.execute(f"SELECT count(*) AS n FROM mar WHERE {clause}", tuple(params))
            total = int(cur.fetchone()["n"])
            cur.execute(
                f'SELECT {_MAR_SELECT} FROM mar WHERE {clause}'
                ' ORDER BY "No" DESC LIMIT %s OFFSET %s',
                (*params, limit, offset),
            )
            return list(cur.fetchall()), total

    def get_children(self, parent_nos: list[int]) -> dict[int, list[dict[str, Any]]]:
        """Component rows for the given parents, grouped by ``parent_no``."""
        if not parent_nos:
            return {}
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f'SELECT {_MAR_SELECT} FROM mar'
                ' WHERE parent_no = ANY(%s) ORDER BY "No"',
                (list(parent_nos),),
            )
            grouped: dict[int, list[dict[str, Any]]] = {}
            for row in cur.fetchall():
                grouped.setdefault(int(row["parent_no"]), []).append(row)
            return grouped

    def get_asset(self, no: int) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f'SELECT {_MAR_SELECT} FROM mar WHERE "No" = %s', (no,))
            return cur.fetchone()

    def update_asset(self, no: int, fields: dict[str, Any]) -> dict[str, Any] | None:
        """Update whitelisted columns on one row; returns the updated row."""
        allowed = {k: v for k, v in fields.items() if k in MAR_EDITABLE_COLUMNS}
        if not allowed:
            return self.get_asset(no)
        assignments = ", ".join(f"{col} = %s" for col in allowed)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(MAR_DDL)
            cur.execute(
                f'UPDATE mar SET {assignments} WHERE "No" = %s'
                f' RETURNING {_MAR_SELECT}',
                (*allowed.values(), no),
            )
            row = cur.fetchone()
            conn.commit()
            return row

    def get_by_job(self, job_id: str) -> list[dict[str, Any]]:
        """Every row written by one pipeline run, parents first."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(MAR_DDL)
            cur.execute(
                f'SELECT {_MAR_SELECT} FROM mar WHERE job_id = %s'
                ' ORDER BY parent_no NULLS FIRST, "No"',
                (job_id,),
            )
            return list(cur.fetchall())

    def latest_job_id(self, pending_only: bool = True) -> str | None:
        """The most recent pipeline run still awaiting review (or any run)."""
        clause = "" if not pending_only else " AND coalesce(status, '') <> 'Registered'"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(MAR_DDL)
            cur.execute(
                "SELECT job_id FROM mar"
                f" WHERE job_id IS NOT NULL AND parent_no IS NULL{clause}"
                ' ORDER BY "No" DESC LIMIT 1'
            )
            row = cur.fetchone()
            return row["job_id"] if row else None

    def link_components(self, parent_no: int, job_id: str) -> int:
        """Attach the rest of a job's rows to ``parent_no`` as components.

        One invoice yields several line items; when a reviewer registers one of
        them as the asset, the siblings become its linked components.
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE mar SET parent_no = %s, status = 'Linked'"
                ' WHERE job_id = %s AND "No" <> %s AND parent_no IS NULL',
                (parent_no, job_id, parent_no),
            )
            n = cur.rowcount
            conn.commit()
            return n

    def register_counts(self) -> dict[str, Any]:
        """Totals for the home screen tiles and the register's header line."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(MAR_DDL)
            cur.execute("SELECT count(*) AS n FROM mar WHERE parent_no IS NULL")
            total = int(cur.fetchone()["n"])
            cur.execute(
                "SELECT coalesce(mindef_cat, '') AS k, count(*) AS n FROM mar"
                " WHERE parent_no IS NULL GROUP BY 1"
            )
            by_cat = {r["k"]: int(r["n"]) for r in cur.fetchall()}
            cur.execute(
                "SELECT coalesce(main_category, '') AS k, count(*) AS n FROM mar"
                " WHERE parent_no IS NULL GROUP BY 1"
            )
            by_group = {r["k"]: int(r["n"]) for r in cur.fetchall()}
            cur.execute(
                "SELECT count(*) AS n FROM mar"
                " WHERE parent_no IS NULL AND coalesce(status, '') <> 'Registered'"
            )
            pending = int(cur.fetchone()["n"])
            cur.execute("SELECT max(created_at) AS t FROM mar")
            updated = cur.fetchone()["t"]
        return {
            "total": total,
            "pending": pending,
            "by_mindef_cat": by_cat,
            "by_main_category": by_group,
            "updated_at": updated.isoformat() if updated else "",
        }

    # -- po / sow documents -------------------------------------------------

    def init_po_sow_schema(self) -> None:
        log.info("ensuring po/sow schema on %s", self.dsn.rsplit("@", 1)[-1])
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(PO_SOW_DDL)
            conn.commit()
        # Separate transaction: if the role cannot CREATE EXTENSION we still want
        # the tables above, and the crawler falls back to matching in Python.
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(PO_SOW_FUZZY_DDL)
                conn.commit()
            self._fuzzy = True
        except psycopg.Error as exc:
            self._fuzzy = False
            log.warning("fuzzy search unavailable (%s); falling back to Python matching",
                        str(exc).strip().splitlines()[0])

    def has_fuzzy_search(self) -> bool:
        """Whether pg_trgm/fuzzystrmatch are installed (probed, then cached)."""
        cached = getattr(self, "_fuzzy", None)
        if cached is not None:
            return cached
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT levenshtein('a', 'b'), similarity('a', 'a')")
                cur.fetchone()
            self._fuzzy = True
        except psycopg.Error:
            self._fuzzy = False
        return self._fuzzy

    # -- the searchable purchase-order index --------------------------------

    def po_index_count(self) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM po_index")
            row = cur.fetchone()
            return int(row["n"]) if row else 0

    def upsert_po_index(self, row: dict[str, Any]) -> None:
        columns = ("po_id", "ref_no", "filename", "category", "vendor", "vendor_uen",
                   "officer", "total_value", "item_names", "payment_events",
                   "sow_items", "keywords")
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != "po_id")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO po_index ({', '.join(columns)}) "
                f"VALUES ({', '.join(['%s'] * len(columns))}) "
                f"ON CONFLICT (po_id) DO UPDATE SET {updates}",
                tuple(row.get(c) for c in columns),
            )
            conn.commit()

    def list_po_index(self) -> list[dict[str, Any]]:
        """Every indexed purchase order. 25 rows today — small enough to hand a model."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM po_index ORDER BY ref_no, po_id")
            return list(cur.fetchall())

    def get_po_index(self, ref_no: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM po_index WHERE ref_no = %s ORDER BY po_id LIMIT 1",
                        (ref_no,))
            return cur.fetchone()

    def list_po_source_rows(self) -> list[dict[str, Any]]:
        """The raw PO rows the index is built from."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT id, ref_no, filename, category, content FROM po ORDER BY id")
            return list(cur.fetchall())

    def find_po_refs_like(self, ref: str, max_distance: int = 2) -> list[dict[str, Any]]:
        """Reference numbers close to ``ref``: exact, then prefix/suffix, then edit distance.

        This is the query that recovers from a misread digit. Falls back to
        ranking all rows in Python when fuzzystrmatch is unavailable — at 25
        rows that is perfectly adequate.
        """
        if not ref:
            return []
        if self.has_fuzzy_search():
            try:
                with self._connect() as conn, conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT ref_no, filename, category,
                               levenshtein(ref_no, %(ref)s) AS distance,
                               (ref_no = %(ref)s)           AS exact
                        FROM   po
                        WHERE  ref_no IS NOT NULL
                          AND (ref_no = %(ref)s
                               OR ref_no LIKE %(ref)s || '%%'
                               OR %(ref)s LIKE ref_no || '%%'
                               OR levenshtein(ref_no, %(ref)s) <= %(dist)s)
                        ORDER BY exact DESC, distance ASC, ref_no
                        LIMIT 10
                        """,
                        {"ref": ref, "dist": max_distance},
                    )
                    return list(cur.fetchall())
            except psycopg.Error as exc:
                log.warning("fuzzy ref lookup failed (%s); ranking in Python", exc)
        return self._find_po_refs_like_python(ref, max_distance)

    def _find_po_refs_like_python(self, ref: str, max_distance: int) -> list[dict[str, Any]]:
        import difflib

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT ref_no, filename, category FROM po WHERE ref_no IS NOT NULL")
            rows = list(cur.fetchall())
        scored = []
        for row in rows:
            candidate = row["ref_no"]
            ratio = difflib.SequenceMatcher(None, candidate, ref).ratio()
            # Approximate an edit distance from the similarity ratio.
            distance = round((1 - ratio) * max(len(candidate), len(ref)))
            if candidate == ref or candidate.startswith(ref) or ref.startswith(candidate) \
                    or distance <= max_distance:
                scored.append({**row, "distance": distance, "exact": candidate == ref})
        scored.sort(key=lambda r: (not r["exact"], r["distance"], r["ref_no"]))
        return scored[:10]

    def _search_documents(self, table: str, query: str, limit: int) -> list[dict[str, Any]]:
        """Free-text search over one document table.

        Uses ``word_similarity(query, content)`` — not ``similarity()``, which
        compares whole trigram sets and returns near-zero for a short query
        against an 8 KB document.
        """
        if not query:
            return []
        if self.has_fuzzy_search():
            try:
                with self._connect() as conn, conn.cursor() as cur:
                    cur.execute(
                        f"""
                        SELECT ref_no, filename, category,
                               word_similarity(%(q)s, content) AS score
                        FROM   {table}
                        WHERE  ref_no IS NOT NULL
                          AND (content ILIKE '%%' || %(q)s || '%%'
                               OR word_similarity(%(q)s, content) > 0.35)
                        ORDER BY score DESC, ref_no
                        LIMIT %(limit)s
                        """,
                        {"q": query, "limit": limit},
                    )
                    return list(cur.fetchall())
            except psycopg.Error as exc:
                log.warning("%s search failed (%s); using ILIKE", table, exc)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT ref_no, filename, category, 0.0 AS score FROM {table} "
                f"WHERE ref_no IS NOT NULL AND content ILIKE '%%' || %s || '%%' "
                f"ORDER BY ref_no LIMIT %s",
                (query, limit),
            )
            return list(cur.fetchall())

    def search_po_content(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        return self._search_documents("po", query, limit)

    def search_sow_content(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        return self._search_documents("sow", query, limit)

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
