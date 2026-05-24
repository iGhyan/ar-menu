"""
Tenant entity model.

DynamoDB table: TenantTable  (separate from MenuTable)
Primary key:    tenantId  (HASH only — no sort key)

A Tenant owns one or more Restaurants. The TenantTable stores
billing plan, contact details, and feature flags so every Lambda
call can authorise the incoming tenantId without a MenuTable scan.

Fields
------
tenantId        UUID — partition key
name            Display name of the tenant organisation
email           Primary contact email
plan            Billing plan: FREE | STARTER | PRO | ENTERPRISE
isActive        Whether the tenant account is enabled
maxRestaurants  How many restaurants this plan allows (0 = unlimited)
createdAt       ISO 8601 UTC
updatedAt       ISO 8601 UTC
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from .base import (
    BaseModel, ValidationError,
    _require, _validate_uuid, _validate_iso8601,
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ALLOWED_PLANS = {"FREE", "STARTER", "PRO", "ENTERPRISE"}


@dataclass
class Tenant(BaseModel):
    tenantId: str
    name: str
    email: str
    plan: str
    isActive: bool
    maxRestaurants: int
    createdAt: str
    updatedAt: str
    contactPhone: Optional[str] = None

    # ── DynamoDB key ──────────────────────────────────────────────────────

    @property
    def pk(self) -> str:
        return self.tenantId

    # ── Validation ────────────────────────────────────────────────────────

    def validate(self) -> None:
        errors: dict[str, str] = {}

        _require(errors, "tenantId", self.tenantId)
        _validate_uuid(errors, "tenantId", self.tenantId)

        _require(errors, "name", self.name)
        if self.name and len(self.name) > 200:
            errors["name"] = "max 200 characters"

        _require(errors, "email", self.email)
        if self.email and not _EMAIL_RE.match(self.email):
            errors["email"] = "must be a valid email address"

        _require(errors, "plan", self.plan)
        if self.plan and self.plan not in ALLOWED_PLANS:
            errors["plan"] = f"must be one of {sorted(ALLOWED_PLANS)}"

        if self.maxRestaurants is None or self.maxRestaurants < 0:
            errors["maxRestaurants"] = "must be a non-negative integer"

        _require(errors, "createdAt", self.createdAt)
        _validate_iso8601(errors, "createdAt", self.createdAt)

        _require(errors, "updatedAt", self.updatedAt)
        _validate_iso8601(errors, "updatedAt", self.updatedAt)

        if errors:
            raise ValidationError(errors)

    # ── Serialisation ─────────────────────────────────────────────────────

    def to_dict(self, exclude_none: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "tenantId":        self.tenantId,
            "name":            self.name,
            "email":           self.email,
            "plan":            self.plan,
            "isActive":        self.isActive,
            "maxRestaurants":  self.maxRestaurants,
            "createdAt":       self.createdAt,
            "updatedAt":       self.updatedAt,
        }
        if self.contactPhone is not None or not exclude_none:
            data["contactPhone"] = self.contactPhone
        return data

    def to_dynamo_item(self) -> dict[str, Any]:
        item = self.to_dict(exclude_none=True)
        item["tenantId"] = self.pk        # explicit PK field
        return item

    # ── Factory ───────────────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Tenant":
        return cls(
            tenantId=data.get("tenantId", ""),
            name=data.get("name", ""),
            email=data.get("email", ""),
            plan=data.get("plan", "FREE"),
            isActive=bool(data.get("isActive", True)),
            maxRestaurants=int(data.get("maxRestaurants", 1)),
            createdAt=data.get("createdAt", ""),
            updatedAt=data.get("updatedAt", ""),
            contactPhone=data.get("contactPhone"),
        )

    @classmethod
    def from_dynamo_item(cls, item: dict[str, Any]) -> "Tenant":
        return cls.from_dict(item)