"""
Restaurant entity model.

DynamoDB key pattern:
  PK = TENANT#{tenantId}#RESTAURANT#{restaurantId}
  SK = METADATA

Image fields:
  logoKey  -- S3 object key (stored in DynamoDB, never returned raw to clients)
  logoUrl  -- presigned GET URL injected at read time by the service layer (not stored)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .base import (
    BaseModel, ValidationError,
    _require, _validate_uuid, _validate_iso8601,
    _validate_max_len, _IANA_TZ_RE, _ISO4217_RE,
)
from .address import Address


@dataclass
class Restaurant(BaseModel):
    restaurantId: str
    tenantId: str
    name: str
    address: Address
    timezone: str
    currencyCode: str
    isActive: bool
    createdAt: str
    updatedAt: str
    logoKey: Optional[str] = None   # S3 key -- stored in DDB, used to generate URL
    logoUrl: Optional[str] = None   # presigned GET URL -- injected at read, not stored

    # -- DynamoDB key helpers -----------------------------------------------

    @property
    def pk(self) -> str:
        return f"TENANT#{self.tenantId}#RESTAURANT#{self.restaurantId}"

    @property
    def sk(self) -> str:
        return "METADATA"

    # -- Validation ---------------------------------------------------------

    def validate(self) -> None:
        errors: dict[str, str] = {}

        _require(errors, "restaurantId", self.restaurantId)
        _validate_uuid(errors, "restaurantId", self.restaurantId)

        _require(errors, "tenantId", self.tenantId)
        _validate_uuid(errors, "tenantId", self.tenantId)

        _require(errors, "name", self.name)
        _validate_max_len(errors, "name", self.name, 200)

        _require(errors, "timezone", self.timezone)
        if self.timezone and not _IANA_TZ_RE.match(self.timezone):
            errors["timezone"] = "must be a valid IANA timezone (e.g. Asia/Karachi)"

        _require(errors, "currencyCode", self.currencyCode)
        if self.currencyCode and not _ISO4217_RE.match(self.currencyCode):
            errors["currencyCode"] = "must be a 3-letter ISO 4217 code"

        _require(errors, "createdAt", self.createdAt)
        _validate_iso8601(errors, "createdAt", self.createdAt)

        _require(errors, "updatedAt", self.updatedAt)
        _validate_iso8601(errors, "updatedAt", self.updatedAt)

        if self.address:
            try:
                self.address.validate()
            except ValidationError as exc:
                for k, v in exc.errors.items():
                    errors[f"address.{k}"] = v
        else:
            errors["address"] = "required"

        if errors:
            raise ValidationError(errors)

    # -- Serialisation ------------------------------------------------------

    def to_dict(self, exclude_none: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "restaurantId": self.restaurantId,
            "tenantId": self.tenantId,
            "name": self.name,
            "address": self.address.to_dict(),
            "timezone": self.timezone,
            "currencyCode": self.currencyCode,
            "isActive": self.isActive,
            "createdAt": self.createdAt,
            "updatedAt": self.updatedAt,
        }
        if self.logoKey is not None or not exclude_none:
            data["logoKey"] = self.logoKey
        if self.logoUrl is not None:
            data["logoUrl"] = self.logoUrl
        return data

    def to_dynamo_item(self) -> dict[str, Any]:
        """DDB item -- logoUrl intentionally excluded (never persisted)."""
        item = self.to_dict(exclude_none=True)
        item.pop("logoUrl", None)
        item["PK"] = self.pk
        item["SK"] = self.sk
        return item

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Restaurant":
        addr_raw = data.get("address") or {}
        address = (
            Address.from_dict(addr_raw)
            if isinstance(addr_raw, dict)
            else Address("", "", "", "")
        )
        return cls(
            restaurantId=data.get("restaurantId", ""),
            tenantId=data.get("tenantId", ""),
            name=data.get("name", ""),
            address=address,
            timezone=data.get("timezone", ""),
            currencyCode=data.get("currencyCode", ""),
            isActive=bool(data.get("isActive", True)),
            createdAt=data.get("createdAt", ""),
            updatedAt=data.get("updatedAt", ""),
            logoKey=data.get("logoKey"),
            logoUrl=data.get("logoUrl"),
        )

    @classmethod
    def from_dynamo_item(cls, item: dict[str, Any]) -> "Restaurant":
        return cls.from_dict(item)