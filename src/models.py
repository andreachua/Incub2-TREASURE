"""Shared Pydantic data models for the pipeline."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


def _coerce_number(value: object) -> float:
    """Best-effort conversion of a model-produced value to a float.

    Local VLMs frequently emit prices/quantities as strings like "$1,299.00"
    or "2 pcs". Strip everything that isn't part of a number rather than fail.
    """
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return 0.0
    text = str(value).strip()
    cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ".-")
    if cleaned in ("", "-", ".", "-."):
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


class RawLineItem(BaseModel):
    """A single line item as extracted from a receipt in Stage 1."""

    name: str
    description: str = ""
    quantity: float = 1.0
    price: float = 0.0

    @field_validator("quantity", "price", mode="before")
    @classmethod
    def _numbers(cls, v: object) -> float:
        return _coerce_number(v)

    @field_validator("name", "description", mode="before")
    @classmethod
    def _strings(cls, v: object) -> str:
        return "" if v is None else str(v).strip()


class AssetRecord(BaseModel):
    """A categorized asset record — the Stage 3 output shape."""

    name: str
    description: str = ""
    category: str
    quantity: float = 1.0
    price: float = 0.0

    @field_validator("quantity", "price", mode="before")
    @classmethod
    def _numbers(cls, v: object) -> float:
        return _coerce_number(v)

    @field_validator("name", "description", "category", mode="before")
    @classmethod
    def _strings(cls, v: object) -> str:
        return "" if v is None else str(v).strip()


class Category(BaseModel):
    """A single allowed category loaded from config/categories.yaml."""

    name: str
    description: str = ""
