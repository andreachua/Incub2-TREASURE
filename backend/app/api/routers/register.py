"""The register itself: home-screen counts, the category options, and the rows."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import log, store
from app.core.config import DEFAULT_SYSTEM, VALID_SYSTEMS, load_categories
from app.schemas import mapping as register
from app.schemas.ui import (
    AssetsPage,
    CategoryOption,
    CompleteReviewRequest,
    CompleteReviewResponse,
    CreateAssetRequest,
    Summary,
    SummaryTile,
)

router = APIRouter()


# --------------------------------------------------------------------------- #
# Register + review API — what the frontend bridge calls.
# --------------------------------------------------------------------------- #

# Captions for the home screen's category tiles. The counts are live; these
# describe what each category *means* and are policy text, not data.
_TILES = (
    ("A", "CAT A", "Capitalised · value ≥ $100,000"),
    ("B", "CAT B", "Tagged equipment · under threshold"),
    ("C", "CAT C", "Consumable and controlled stores"),
)


@router.get("/api/summary", response_model=Summary)
def get_summary() -> Summary:
    """Home screen: live per-category counts and the pending-review count."""
    counts = store.register_counts()
    by_cat = counts["by_mindef_cat"]
    return Summary(
        total=counts["total"],
        pending=counts["pending"],
        updated_at=counts["updated_at"],
        tiles=[
            SummaryTile(key=key, label=label, caption=caption,
                        count=by_cat.get(key, 0))
            for key, label, caption in _TILES
        ],
    )


@router.get("/api/categories", response_model=list[CategoryOption])
def get_categories(system: str = DEFAULT_SYSTEM) -> list[CategoryOption]:
    """The MINDEF Category options, straight from config/categories.<system>.yaml."""
    system = (system or DEFAULT_SYSTEM).strip().lower()
    if system not in VALID_SYSTEMS:
        raise HTTPException(422, f"system must be one of {list(VALID_SYSTEMS)}")
    return [
        CategoryOption(name=c.name, group=c.group, code=c.code)
        for c in load_categories(system)
    ]


@router.get("/api/assets", response_model=AssetsPage)
def get_assets(
    search: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> AssetsPage:
    """The asset register: top-level rows with their components nested."""
    rows, total = store.list_assets(search=search, limit=limit, offset=offset)
    children = store.get_children([int(r["No"]) for r in rows])
    updated = store.register_counts()["updated_at"]
    return register.rows_to_page(rows, children, total, updated)


@router.post("/api/assets", response_model=CompleteReviewResponse, status_code=201)
def create_asset(body: CreateAssetRequest) -> CompleteReviewResponse:
    """Add an asset to the register.

    Two ways in, one row out. With a ``job_id`` the form was pre-filled by a
    pipeline run, so this delegates to the review-completion path rather than
    forking it — that is what links the invoice's other line items as components
    and updates the extracted row instead of inserting a duplicate. Without one,
    the asset was typed in by hand and is inserted directly.

    A manual row carries no job_id, so it never shows up as a pending review on
    the home screen. That falls out of the existing query and is the behaviour
    we want.
    """
    fields = body.fields or {}
    errors = register.validate_fields(fields, body.system)
    if errors:
        raise HTTPException(422, "; ".join(errors))

    if body.job_id:
        from app.api.routers.review import complete_review

        return complete_review(
            body.job_id,
            CompleteReviewRequest(fields=fields, record_index=body.record_index),
        )

    row = register.fields_to_mar_row(fields, body.system)
    no = store.insert_mar([row])[0]
    created = store.get_asset(no)
    if created is None:
        raise HTTPException(500, "could not persist the new asset")
    asset = register.row_to_asset(created, [])
    asset.is_new = True
    log.info("asset added manually: mar.No=%d (%s)", no, asset.name)
    return CompleteReviewResponse(
        asset=asset,
        message=f"{asset.name} added to {asset.category}. Tag number pending.",
    )
