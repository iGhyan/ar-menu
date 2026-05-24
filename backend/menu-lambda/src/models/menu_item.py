"""
MenuItem entity model.

DynamoDB key pattern:
  PK = TENANT#{tenantId}#RESTAURANT#{restaurantId}
  SK = ITEM#{itemId}

Supports optimistic locking via the `version` field.

Image fields:
  imageKey   -- S3 key for item photo (stored in DDB)
  imageUrl   -- presigned GET URL injected at read time (not stored)
  arModelKey -- S3 key for .glb AR model (stored in DDB)
  arModelUrl -- presigned GET URL for AR model (not stored)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from .base import (
    BaseModel, ValidationError,
    _require, _validate_uuid, _validate_iso8601,
    _validate_max_len, _validate_positive,
    ALLOWED_ALLERGENS,
)


@dataclass
class MenuItem(BaseModel):
    itemId: str
    tenantId: str
    restaurantId: str
    categoryId: str
    name: str
    description: str
    priceMinorUnits: int
    isActive: bool
    version: int
    createdAt: str
    updatedAt: str
    imageKey: Optional[str] = None          # S3 key -- stored in DDB
    imageUrl: Optional[str] = None          # presigned GET URL -- not stored
    allergens: List[str] = field(default_factory=list)
    arModelKey: Optional[str] = None        # S3 key for .glb -- stored in DDB
    arModelUrl: Optional[str] = None        # presigned GET URL for AR -- not stored

    # -- DynamoDB key helpers -----------------------------------------------

    @property
    def pk(self) -> str:
        return f"TENANT#{self.tenantId}#RESTAURANT#{self.restaurantId}"

    @property
    def sk(self) -> str:
        return f"ITEM#{self.itemId}"

    # -- Validation ---------------------------------------------------------

    def validate(self) -> None:
        errors: dict[str, str] = {}

        _require(errors, "itemId", self.itemId)
        _validate_uuid(errors, "itemId", self.itemId)

        _require(errors, "tenantId", self.tenantId)
        _validate_uuid(errors, "tenantId", self.tenantId)

        _require(errors, "restaurantId", self.restaurantId)
        _validate_uuid(errors, "restaurantId", self.restaurantId)

        _require(errors, "categoryId", self.categoryId)
        _validate_uuid(errors, "categoryId", self.categoryId)

        _require(errors, "name", self.name)
        _validate_max_len(errors, "name", self.name, 200)

        _require(errors, "description", self.description)
        _validate_max_len(errors, "description", self.description, 500)

        if self.priceMinorUnits is None:
            errors["priceMinorUnits"] = "required"
        else:
            _validate_positive(errors, "priceMinorUnits", self.priceMinorUnits)

        if self.version is None or self.version < 1:
            errors["version"] = "must be a positive integer"

        _require(errors, "createdAt", self.createdAt)
        _validate_iso8601(errors, "createdAt", self.createdAt)

        _require(errors, "updatedAt", self.updatedAt)
        _validate_iso8601(errors, "updatedAt", self.updatedAt)

        invalid_allergens = [a for a in self.allergens if a not in ALLOWED_ALLERGENS]
        if invalid_allergens:
            errors["allergens"] = f"unknown allergen codes: {invalid_allergens}"

        if errors:
            raise ValidationError(errors)

    # -- Serialisation ------------------------------------------------------

    def to_dict(self, exclude_none: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "itemId": self.itemId,
            "tenantId": self.tenantId,
            "restaurantId": self.restaurantId,
            "categoryId": self.categoryId,
            "name": self.name,
            "description": self.description,
            "priceMinorUnits": self.priceMinorUnits,
            "isActive": self.isActive,
            "version": self.version,
            "createdAt": self.createdAt,
            "updatedAt": self.updatedAt,
            "allergens": self.allergens,
        }
        if self.imageKey is not None or not exclude_none:
            data["imageKey"] = self.imageKey
        if self.imageUrl is not None:
            data["imageUrl"] = self.imageUrl
        if self.arModelKey is not None or not exclude_none:
            data["arModelKey"] = self.arModelKey
        if self.arModelUrl is not None:
            data["arModelUrl"] = self.arModelUrl
        return data

    def to_dynamo_item(self) -> dict[str, Any]:
        """DDB item -- imageUrl/arModelUrl intentionally excluded (never persisted)."""
        item = self.to_dict(exclude_none=True)
        item.pop("imageUrl", None)
        item.pop("arModelUrl", None)
        item["PK"] = self.pk
        item["SK"] = self.sk
        return item

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MenuItem":
        return cls(
            itemId=data.get("itemId", ""),
            tenantId=data.get("tenantId", ""),
            restaurantId=data.get("restaurantId", ""),
            categoryId=data.get("categoryId", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            priceMinorUnits=int(data.get("priceMinorUnits", 0)),
            isActive=bool(data.get("isActive", True)),
            version=int(data.get("version", 1)),
            createdAt=data.get("createdAt", ""),
            updatedAt=data.get("updatedAt", ""),
            imageKey=data.get("imageKey"),
            imageUrl=data.get("imageUrl"),
            allergens=list(data.get("allergens") or []),
            arModelKey=data.get("arModelKey"),
            arModelUrl=data.get("arModelUrl"),
        )

    @classmethod
    def from_dynamo_item(cls, item: dict[str, Any]) -> "MenuItem":
        return cls.from_dict(item)