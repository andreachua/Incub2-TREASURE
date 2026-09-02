"""Mapping between storage/pipeline shapes and the UI shapes in api_models.

Three translations live here:
  mar row        -> AssetRow / review field values   (reading the register)
  pipeline record-> review field values              (reading a fresh job)
  review fields  -> mar row                          (writing a completed review)
"""

from __future__ import annotations

from typing import Any

from .ui import AssetRow, AssetsPage
from app.core.config import (
    DEFAULT_SYSTEM,
    category_names,
    get_settings,
    group_for_category,
)
from .domain import _coerce_number

# Status values the register uses. A row starts life extracted-but-unreviewed
# and becomes "Registered" when a human completes the review.
STATUS_REGISTERED = "Registered"
STATUS_PENDING = "Pending review"
STATUS_COMPONENT = "Linked"

# Shown in the Tag no. column before the asset has physically been tagged.
TAG_PENDING = "Pending"


def default_custodian() -> str:
    """The custodian a new record is assigned to until a reviewer confirms it."""
    return get_settings().default_custodian


def format_price(value: Any) -> str:
    """Render a numeric price the way the register displays it: 12,480.00."""
    if value is None or value == "":
        return ""
    return f"{_coerce_number(value):,.2f}"


def format_quantity(value: Any) -> str:
    """Quantities are usually whole; don't show 1.0 where 1 is meant."""
    if value is None or value == "":
        return ""
    n = _coerce_number(value)
    return str(int(n)) if n == int(n) else str(n)


def category_label(mindef_cat: str, sub_category: str) -> str:
    """"CAT B · Communications Equipment", or just the category if uncategorised."""
    cat = (mindef_cat or "").strip()
    sub = (sub_category or "").strip()
    if cat and sub:
        return f"CAT {cat} · {sub}"
    if cat:
        return f"CAT {cat}"
    return sub


# --- reading the register ------------------------------------------------- #

def row_to_asset(row: dict[str, Any], components: list[dict[str, Any]] | None = None) -> AssetRow:
    is_component = row.get("parent_no") is not None
    return AssetRow(
        no=int(row["No"]),
        name=row.get("asset_model") or "",
        tag_no=row.get("tag_no") or ("" if is_component else TAG_PENDING),
        category="Component" if is_component else category_label(
            row.get("mindef_cat") or "", row.get("sub_category") or ""
        ),
        serial_no=row.get("serial_no") or "",
        custodian=row.get("custodian") or "",
        unit_price=format_price(row.get("price")),
        status=row.get("status") or (STATUS_COMPONENT if is_component else STATUS_PENDING),
        components=[row_to_asset(c) for c in (components or [])],
    )


def rows_to_page(
    rows: list[dict[str, Any]],
    children: dict[int, list[dict[str, Any]]],
    total: int,
    updated_at: str = "",
) -> AssetsPage:
    return AssetsPage(
        total=total,
        updated_at=updated_at,
        assets=[row_to_asset(r, children.get(int(r["No"]), [])) for r in rows],
    )


# --- field values for the review form ------------------------------------- #

def mar_row_to_values(row: dict[str, Any]) -> dict[str, str]:
    """A stored register row, as review-form field values."""
    return {
        "name": row.get("asset_model") or "",
        "category": row.get("sub_category") or "",
        "description": row.get("description") or "",
        "price": format_price(row.get("price")),
        "quantity": format_quantity(row.get("quantity")),
        **{
            k: (row.get(k) or "")
            for k in (
                "serial_no", "invoice_no", "po_no", "do_no", "do_date", "gl_date",
                "vendor", "project", "period_contract", "purchase_type", "taggable",
                "asset_capitalisation_date", "tag_no", "custodian", "mindef_cat",
                "material_number", "receiving_plant", "receiving_sloc",
            )
        },
    }


def record_to_values(record: dict[str, Any]) -> dict[str, str]:
    """A fresh pipeline record (stage3_writer._KEYS), as review-form values.

    Fields no stage produces (tag number, ERP material/plant/SLOC, the CAT
    B/C/Dev call) come back empty so the form marks them for manual entry.
    """
    values = {
        "price": format_price(record.get("price")),
        "quantity": format_quantity(record.get("quantity")),
        "custodian": default_custodian(),
        "mindef_cat": "",
        "tag_no": "",
        "material_number": "",
        "receiving_plant": "",
        "receiving_sloc": "",
    }
    for key in (
        "name", "description", "category", "invoice_no", "po_no", "do_no",
        "do_date", "gl_date", "vendor", "project", "period_contract",
        "purchase_type", "serial_no", "taggable", "asset_capitalisation_date",
    ):
        values[key] = str(record.get(key) or "")
    return values


# --- writing a completed review ------------------------------------------- #

def fields_to_mar_row(
    fields: dict[str, str], system: str = DEFAULT_SYSTEM
) -> dict[str, Any]:
    """Reviewed form values -> mar columns. ``main_category`` is re-derived."""
    category = (fields.get("category") or "").strip()
    return {
        "asset_model": fields.get("name") or None,
        "sub_category": category or None,
        "main_category": group_for_category(category, system) if category else None,
        "description": fields.get("description") or None,
        "price": _coerce_number(fields.get("price")),
        "quantity": _coerce_number(fields.get("quantity")) or 1.0,
        "status": STATUS_REGISTERED,
        "tag_no": (fields.get("tag_no") or "").strip() or TAG_PENDING,
        **{
            k: (fields.get(k) or "").strip() or None
            for k in (
                "serial_no", "invoice_no", "po_no", "do_no", "do_date", "gl_date",
                "vendor", "project", "period_contract", "purchase_type", "taggable",
                "asset_capitalisation_date", "custodian", "mindef_cat",
                "material_number", "receiving_plant", "receiving_sloc",
            )
        },
    }


# --- validation ------------------------------------------------------------ #

# The message the review form's error banner already shows, kept verbatim so
# the front end's copy does not change.
MINDEF_CAT_REQUIRED = "CAT B or C or Dev? must be selected"


def validate_fields(
    fields: dict[str, str], system: str = DEFAULT_SYSTEM
) -> list[str]:
    """Every rule a manually-entered asset must satisfy, as human messages.

    Applied to `POST /api/assets` only. `POST /api/review/{job_id}/complete`
    keeps its original single rule: tightening that endpoint from "category
    required" to "seven fields required" would change how an existing workflow
    behaves, which is not what this feature is for.
    """
    errors: list[str] = []

    def value(key: str) -> str:
        return (fields.get(key) or "").strip()

    if not value("mindef_cat"):
        errors.append(MINDEF_CAT_REQUIRED)
    if not value("name"):
        errors.append("Brand/Model is required")

    category = value("category")
    if not category:
        errors.append("MINDEF Category is required")
    elif category not in category_names(system):
        errors.append(f"{category!r} is not a category in the {system} taxonomy")

    if value("price") and _coerce_number(fields.get("price")) < 0:
        errors.append("Unit Price cannot be negative")
    if value("quantity") and _coerce_number(fields.get("quantity")) <= 0:
        errors.append("Quantity must be at least 1")

    purchase_type = value("purchase_type")
    if purchase_type and purchase_type not in (
        "One-time purchase", "Period contract call-off", "Framework demand"
    ):
        errors.append(f"{purchase_type!r} is not a valid purchase type")

    taggable = value("taggable")
    if taggable and taggable not in ("Yes", "No"):
        errors.append("Taggable must be Yes or No")

    return errors
