"""
Base model classes and validation primitives.
All models are plain dataclasses — no external framework.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# ── Validation Errors ─────────────────────────────────────────────────────

class ValidationError(Exception):
    """Raised when model validation fails."""

    def __init__(self, errors: dict[str, str]) -> None:
        self.errors = errors
        super().__init__(str(errors))


# ── Validator helpers ─────────────────────────────────────────────────────

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_ISO8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$"
)
_IANA_TZ_RE = re.compile(r"^[A-Za-z]+/[A-Za-z_]+$")
_ISO4217_RE = re.compile(r"^[A-Z]{3}$")


def _require(errors: dict, field_name: str, value: Any) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        errors[field_name] = "required"
        return False
    return True


def _validate_uuid(errors: dict, field_name: str, value: str) -> None:
    if value and not _UUID_RE.match(value):
        errors[field_name] = f"must be a valid UUID, got: {value!r}"


def _validate_iso8601(errors: dict, field_name: str, value: str) -> None:
    if value and not _ISO8601_RE.match(value):
        errors[field_name] = "must be ISO 8601 UTC (e.g. 2025-04-20T10:00:00Z)"


def _validate_max_len(errors: dict, field_name: str, value: str, max_len: int) -> None:
    if value and len(value) > max_len:
        errors[field_name] = f"max {max_len} characters, got {len(value)}"


def _validate_positive(errors: dict, field_name: str, value: Any) -> None:
    if value is not None and (not isinstance(value, (int, float)) or value < 0):
        errors[field_name] = "must be a non-negative number"


ALLOWED_AR_STATUSES = {"NONE", "PENDING_VALIDATION", "APPROVED", "REJECTED"}
ALLOWED_ALLERGENS = {
    "GLUTEN", "DAIRY", "EGGS", "FISH", "SHELLFISH",
    "TREE_NUTS", "PEANUTS", "WHEAT", "SOYBEANS", "SESAME",
}


# ── Base dataclass ────────────────────────────────────────────────────────

@dataclass
class BaseModel:
    """Shared serialisation helpers."""

    def to_dict(self, exclude_none: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if exclude_none:
            data = {k: v for k, v in data.items() if v is not None}
        return data

    def validate(self) -> None:
        """Override in subclass; raise ValidationError on failure."""
        raise NotImplementedError