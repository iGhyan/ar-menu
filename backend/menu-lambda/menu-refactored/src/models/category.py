"""
MenuCategory entity model.

DynamoDB key pattern:
  PK = TENANT#{tenantId}#RESTAURANT#{restaurantId}
  SK = CATEGORY#{categoryId}

Image fields:
  imageKey -- S3 object key (stored in DDB)
  imageUrl -- presigned GET URL injected at read time (not stored)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .base import (
    BaseModel, ValidationError,
    _require, _validate_uuid, _validate_positive,
)


@dataclass
class MenuCategory(BaseModel):
    categoryId: str
    tenantId: str
    restaurantId: str
    name: str
    displayOrder: int
    isActive: bool
    imageKey: Optional[str] = None   # S3 key -- stored in DDB
    imageUrl: Optional[str] = None   # presigned GET URL -- not stored

    # -- DynamoDB key helpers -----------------------------------------------

    @property
    def pk(self) -> str:
        return f"TENANT#{self.tenantId}#RESTAURANT#{self.restaurantId}"

    @property
    def sk(self) -> str:
        return f"CATEGORY#{self.categoryId}"

    # -- Validation ---------------------------------------------------------

    def validate(self) -> None:
        errors: dict[str, str] = {}

        _require(errors, "categoryId", self.categoryId)
        _validate_uuid(errors, "categoryId", self.categoryId)

        _require(errors, "tenantId", self.tenantId)
        _validate_uuid(errors, "tenantId", self.tenantId)

        _require(errors, "restaurantId", self.restaurantId)
        _validate_uuid(errors, "restaurantId", self.restaurantId)

        _require(errors, "name", self.name)

        if self.displayOrder is None:
            errors["displayOrder"] = "required"
        else:
            _validate_positive(errors, "displayOrder", self.displayOrder)

        if errors:
            raise ValidationError(errors)

    # -- Serialisation ------------------------------------------------------

    def to_dict(self, exclude_none: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "categoryId": self.categoryId,
            "tenantId": self.tenantId,
            "restaurantId": self.restaurantId,
            "name": self.name,
            "displayOrder": self.displayOrder,
            "isActive": self.isActive,
        }
        if self.imageKey is not None or not exclude_none:
            data["imageKey"] = self.imageKey
        if self.imageUrl is not None:
            data["imageUrl"] = self.imageUrl
        return data

    def to_dynamo_item(self) -> dict[str, Any]:
        """DDB item -- imageUrl intentionally excluded (never persisted)."""
        item = self.to_dict(exclude_none=True)
        item.pop("imageUrl", None)
        item["PK"] = self.pk
        item["SK"] = self.sk
        return item

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MenuCategory":
        return cls(
            categoryId=data.get("categoryId", ""),
            tenantId=data.get("tenantId", ""),
            restaurantId=data.get("restaurantId", ""),
            name=data.get("name", ""),
            displayOrder=int(data.get("displayOrder", 0)),
            isActive=bool(data.get("isActive", True)),
            imageKey=data.get("imageKey"),
            imageUrl=data.get("imageUrl"),
        )

    @classmethod
    def from_dynamo_item(cls, item: dict[str, Any]) -> "MenuCategory":
        return cls.from_dict(item)