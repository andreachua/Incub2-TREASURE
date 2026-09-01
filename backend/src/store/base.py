"""Swappable document-store interface.

The pipeline only depends on this abstraction, so the backing store (Postgres +
pgvector today) can be swapped for TinyDB/Mongo without touching Stages 1-3.
"""

from __future__ import annotations

from typing import Any, Protocol


class DocumentStore(Protocol):
    def get_by_project_id(self, project_id: str) -> list[dict[str, Any]]:
        """Return the full stored documents for a project (key lookup).

        Each dict has at least: ``project_id``, ``title``, ``content``.
        ``content`` is the full purchase-record/catalog payload the agent
        reasons over to find the most related line item.
        """
        ...

    def search_line_items(
        self, project_id: str, query: str, k: int = 5
    ) -> list[dict[str, Any]]:
        """Optional semantic shortlist of candidate line items within a project.

        Returns an empty list if vector search is unavailable (e.g. embeddings
        were never populated); callers must tolerate that and fall back to the
        full document from ``get_by_project_id``.
        """
        ...
