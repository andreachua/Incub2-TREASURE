"""Response/request shapes for the register + review API the frontend calls.

These are the UI-facing models. They deliberately differ from ``models.py``:
``AssetRecord`` is the pipeline's shape (raw floats, pipeline field names),
whereas these carry display-ready strings and the provenance metadata the
review screen renders.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# Where a field's current value came from, mirroring the UI's legend:
#   ai      — extracted from the purchase documents by the pipeline
#   system  — a deterministic default (see src/derive.py)
#   manual  — nothing upstream can supply it; a human must
Provenance = Literal["ai", "manual", "system"]


class ReviewField(BaseModel):
    """One form field: its current value and where that value came from."""

    value: str = ""
    prov: Provenance = "ai"


class Citation(BaseModel):
    """A quoted span from a source document supporting a classification."""

    source_label: str
    quote: str


class Explanation(BaseModel):
    """Why the categorizer chose the category it did.

    ``citations`` is empty when the pipeline recorded a rationale but no
    quotable source spans — the UI shows that honestly rather than inventing
    quotes.
    """

    title: str = ""
    rationale: str = ""
    citations: list[Citation] = []
    runner_up: str = ""
    matched_rules: int = 0
    source_count: int = 0


# What a rail entry points at. "invoice" is the uploaded file itself; "po" and
# "sow" are the procurement documents matched by the invoice's PO reference.
SourceKind = Literal["invoice", "po", "sow", "do"]


class SourceDoc(BaseModel):
    """An entry in the review screen's "Source documents" rail.

    ``url`` is the API path that serves the document; it is empty when the
    reference was extracted but the document itself is not held (e.g. a
    delivery order, or a PO ref with no matching row).
    """

    title: str
    meta: str = ""
    kind: SourceKind = "invoice"
    url: str = ""


class MatchEvidenceView(BaseModel):
    """One reason the crawler tied this invoice to a purchase order."""

    signal: str = ""
    detail: str = ""
    score: float = 0.0


class ContextMatchView(BaseModel):
    """How the invoice's purchase order and scope of work were found.

    Shown as its own card in the review rail rather than folded into the
    classification explanation: that popover hangs off the MINDEF Category field
    and explains *why this category*, which is a different question from *which
    contract this invoice belongs to*.
    """

    po_ref: str = ""
    sow_ref: str = ""
    vendor: str = ""
    confidence: float = 0.0
    method: str = ""
    reasoning: str = ""
    evidence: list[MatchEvidenceView] = []


class ReviewPayload(BaseModel):
    """Everything the review screen needs for one asset record."""

    job_id: str
    record_index: int = 0
    record_count: int = 1
    fields: dict[str, ReviewField] = {}
    explanation: Explanation | None = None
    source_documents: list[SourceDoc] = []
    counts: dict[str, int] = {}  # e.g. {"ai": 20, "system": 1, "manual": 2}
    # Optional, so a client written before the crawler existed is unaffected.
    context_match: ContextMatchView | None = None


class AssetRow(BaseModel):
    """One row of the asset register table (components nest under a parent)."""

    no: int
    name: str = ""
    tag_no: str = ""
    category: str = ""
    serial_no: str = ""
    custodian: str = ""
    unit_price: str = ""  # pre-formatted, e.g. "12,480.00"
    status: str = ""
    is_new: bool = False
    components: list[AssetRow] = []


class AssetsPage(BaseModel):
    total: int = 0
    updated_at: str = ""
    assets: list[AssetRow] = []


class SummaryTile(BaseModel):
    key: str
    label: str
    caption: str = ""
    count: int = 0


class Summary(BaseModel):
    total: int = 0
    pending: int = 0
    updated_at: str = ""
    tiles: list[SummaryTile] = []


class CategoryOption(BaseModel):
    name: str
    group: str = ""
    code: str = ""


class JobStatus(BaseModel):
    """Poll target for an upload. Always 200 so the client can poll quietly."""

    job_id: str
    status: Literal["queued", "ready", "error"] = "queued"
    # Which pipeline stage the job is at while `status` is "queued" — one of
    # app.pipeline.orchestrator.STAGES, or "" when the consumer has not picked
    # the job up yet. The upload screen renders it as a checklist.
    stage: str = ""
    count: int = 0
    error: str = ""


class CompleteReviewRequest(BaseModel):
    """The edited field set posted by "Complete review"."""

    fields: dict[str, str] = {}
    # Which of the job's records was on screen. An invoice decomposes into
    # several line items and the reviewer can page through them, so without
    # this the completion always lands on the first row — overwriting record 0
    # with record N's values. Defaults to 0, which is what a single-record job
    # and any pre-existing client both mean.
    record_index: int = 0


class CompleteReviewResponse(BaseModel):
    asset: AssetRow
    message: str = ""


class CreateAssetRequest(BaseModel):
    """A new asset, either typed in by hand or confirmed off an uploaded invoice."""

    fields: dict[str, str] = {}
    record_index: int = 0  # see CompleteReviewRequest; ignored without a job_id
    # Set when the form was pre-filled by a pipeline run: the other line items
    # from that invoice become this asset's components, exactly as a completed
    # review does.
    job_id: str = ""
    system: str = "oxn"


AssetRow.model_rebuild()
