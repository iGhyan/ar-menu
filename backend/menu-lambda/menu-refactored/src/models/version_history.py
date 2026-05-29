"""
VersionHistory entity model.

Stores a snapshot of a MenuItem every time it is updated, giving a
full audit trail of price / description / availability changes.

DynamoDB key pattern (stored in MenuTable, same PK as the item):
  PK = TENANT#{tenantId}#RESTAURANT#{restaurantId}
  SK = ITEM#{itemId}#VERSION#{version:05d}

The zero-padded version number in the SK keeps history items in
chronological order when queried with begins_with("ITEM#{itemId}#VERSION#").

Fields
------
itemId      Reference to the MenuItem
tenantId    Owning tenant
restaurantId Owning restaurant
version     The version number being recorded (1-based)
snapshot    Complete dict of the MenuItem state at this version
changedBy   Identity of the actor who made the change (optional)
changedAt   ISO 8601 UTC timestamp of the change
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .base import (
    BaseModel, ValidationError,
    _require, _validate_uuid, _validate_iso8601,
)


@dataclass
class VersionHistory(BaseModel):
    itemId: str
    tenantId: str
    restaurantId: str
    version: int
    snapshot: dict[str, Any]
    changedAt: str
    changedBy: Optional[str] = None

    # ── DynamoDB key helpers ───────────────────────────────────────────────

    @property
    def pk(self) -> str:
        return f"TENANT#{self.tenantId}#RESTAURANT#{self.restaurantId}"

    @property
    def sk(self) -> str:
        # Zero-padded so lexicographic sort == numeric sort up to version 99999
        return f"ITEM#{self.itemId}#VERSION#{self.version:05d}"

    # ── Validation ────────────────────────────────────────────────────────

    def validate(self) -> None:
        errors: dict[str, str] = {}

        _require(errors, "itemId", self.itemId)
        _validate_uuid(errors, "itemId", self.itemId)

        _require(errors, "tenantId", self.tenantId)
        _validate_uuid(errors, "tenantId", self.tenantId)

        _require(errors, "restaurantId", self.restaurantId)
        _validate_uuid(errors, "restaurantId", self.restaurantId)

        if self.version is None or self.version < 1:
            errors["version"] = "must be a positive integer"

        if not self.snapshot:
            errors["snapshot"] = "required"

        _require(errors, "changedAt", self.changedAt)
        _validate_iso8601(errors, "changedAt", self.changedAt)

        if errors:
            raise ValidationError(errors)

    # ── Serialisation ─────────────────────────────────────────────────────

    def to_dict(self, exclude_none: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "itemId":       self.itemId,
            "tenantId":     self.tenantId,
            "restaurantId": self.restaurantId,
            "version":      self.version,
            "snapshot":     self.snapshot,
            "changedAt":    self.changedAt,
        }
        if self.changedBy is not None or not exclude_none:
            data["changedBy"] = self.changedBy
        return data

    def to_dynamo_item(self) -> dict[str, Any]:
        item = self.to_dict(exclude_none=True)
        item["PK"] = self.pk
        item["SK"] = self.sk
        return item

    # ── Factory ───────────────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VersionHistory":
        return cls(
            itemId=data.get("itemId", ""),
            tenantId=data.get("tenantId", ""),
            restaurantId=data.get("restaurantId", ""),
            version=int(data.get("version", 1)),
            snapshot=data.get("snapshot") or {},
            changedAt=data.get("changedAt", ""),
            changedBy=data.get("changedBy"),
        )

    @classmethod
    def from_dynamo_item(cls, item: dict[str, Any]) -> "VersionHistory":
        return cls.from_dict(item)

    # ── Class-level factory ────────────────────────────────────────────────

    @classmethod
    def from_menu_item(
        cls,
        item_dict: dict[str, Any],
        changed_by: Optional[str] = None,
    ) -> "VersionHistory":
        """
        Convenience factory — build a VersionHistory from a MenuItem.to_dict().
        Strips read-time URL fields so only persisted fields are snapshotted.
        """
        snapshot = {
            k: v for k, v in item_dict.items()
            if k not in ("imageUrl", "arModelUrl")
        }
        return cls(
            itemId=item_dict.get("itemId", ""),
            tenantId=item_dict.get("tenantId", ""),
            restaurantId=item_dict.get("restaurantId", ""),
            version=int(item_dict.get("version", 1)),
            snapshot=snapshot,
            changedAt=item_dict.get("updatedAt", ""),
            changedBy=changed_by,
        )