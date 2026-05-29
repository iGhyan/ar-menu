"""
shared/tenant_validator.py
===========================
Tenant isolation logic — validates and extracts tenant context from S3 keys.

Expected S3 key format
-----------------------
    uploads/TENANT#<uuid>/restaurants/<uuid>/ar-models/<filename>.glb

This module is in the shared layer so any Lambda that handles tenant-scoped
S3 objects can reuse the same validation rules without duplication.

Usage
-----
    from shared.tenant_validator import extract_tenant_context, validate_tenant_key
    from shared.exceptions import TenantKeyError

    # Raises TenantKeyError if invalid:
    ctx = extract_tenant_context("uploads/TENANT#abc.../restaurants/.../ar-models/x.glb")
    print(ctx.tenant_id, ctx.restaurant_id, ctx.filename)

    # Boolean check (no exception):
    ok, reason = validate_tenant_key(key)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from shared.exceptions import TenantKeyError

# ── Patterns ──────────────────────────────────────────────────────────────────

_UUID_RE = re.compile(
    r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$",
    re.IGNORECASE,
)

_TENANT_KEY_RE = re.compile(
    r"^uploads/"
    r"TENANT#(?P<tenant_id>[a-f0-9\-]{36})/"
    r"restaurants/(?P<restaurant_id>[a-f0-9\-]{36})/"
    r"ar-models/(?P<filename>.+\.glb)$",
    re.IGNORECASE,
)


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TenantContext:
    """Parsed tenant information extracted from an S3 key."""

    tenant_id:     str
    restaurant_id: str
    filename:      str
    original_key:  str

    @property
    def approved_key(self) -> str:
        return self.original_key.replace("uploads/", "approved/", 1)

    @property
    def rejected_key(self) -> str:
        return self.original_key.replace("uploads/", "rejected/", 1)

    def destination_key(self, status: str) -> str:
        """Return the destination key for *status* ('approved' | 'rejected')."""
        if status not in ("approved", "rejected"):
            raise ValueError(f"Unknown status '{status}' — expected 'approved' or 'rejected'.")
        return self.original_key.replace("uploads/", f"{status}/", 1)


# ── Public API ────────────────────────────────────────────────────────────────

def extract_tenant_context(key: str) -> TenantContext:
    """
    Parse *key* and return a `TenantContext`.

    Raises
    ------
    TenantKeyError
        If the key does not match the expected pattern or contains invalid UUIDs.
    """
    _assert_uploads_prefix(key)

    match = _TENANT_KEY_RE.match(key)
    if not match:
        raise TenantKeyError(
            key=key,
            reason=(
                "Expected uploads/TENANT#<uuid>/restaurants/<uuid>/ar-models/<file>.glb, "
                f"got: {key}"
            ),
        )

    tenant_id     = match.group("tenant_id").lower()
    restaurant_id = match.group("restaurant_id").lower()
    filename      = match.group("filename")

    # UUID pattern validation — regex already restricts to hex chars but
    # we validate full UUID format here for a precise error message.
    if not _UUID_RE.match(tenant_id):
        raise TenantKeyError(
            key=key,
            reason=f"Invalid tenantId format — not a valid UUID: '{tenant_id}'.",
        )
    if not _UUID_RE.match(restaurant_id):
        raise TenantKeyError(
            key=key,
            reason=f"Invalid restaurantId format — not a valid UUID: '{restaurant_id}'.",
        )

    return TenantContext(
        tenant_id=tenant_id,
        restaurant_id=restaurant_id,
        filename=filename,
        original_key=key,
    )


def validate_tenant_key(key: str) -> tuple[bool, str]:
    """
    Validate *key* without raising.

    Returns
    -------
    (True, "")              — key is valid
    (False, reason_string)  — key is invalid, reason describes the problem
    """
    try:
        extract_tenant_context(key)
        return True, ""
    except TenantKeyError as exc:
        return False, exc.message


# ── Internal helpers ──────────────────────────────────────────────────────────

def _assert_uploads_prefix(key: str) -> None:
    if not key.lower().startswith("uploads/"):
        raise TenantKeyError(key=key, reason="Key must start with 'uploads/'.")


def _assert_uuid(key: str, field_name: str, value: str) -> None:
    if not _UUID_RE.match(value):
        raise TenantKeyError(
            key=key,
            reason=f"Invalid {field_name} format — not a valid UUID: '{value}'.",
        )
