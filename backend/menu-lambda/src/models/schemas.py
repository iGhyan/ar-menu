"""
API request / response envelope schemas.
These are thin wrappers used by handlers — not stored in DynamoDB.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .base import ValidationError, _require, _validate_uuid


# ── Presigned URL request ─────────────────────────────────────────────────

ALLOWED_ASSET_TYPES = {"logo", "category-image", "item-image", "ar-model"}
ALLOWED_CONTENT_TYPES = {
    "image/webp", "image/jpeg", "image/png",
    "model/gltf-binary",         # .glb AR models
}


@dataclass
class PresignedUrlRequest:
    tenantId: str
    restaurantId: str
    assetType: str           # logo | category-image | item-image | ar-model
    contentType: str         # MIME type
    entityId: Optional[str] = None  # categoryId or itemId when applicable

    def validate(self) -> None:
        errors: dict[str, str] = {}
        _require(errors, "tenantId", self.tenantId)
        _validate_uuid(errors, "tenantId", self.tenantId)
        _require(errors, "restaurantId", self.restaurantId)
        _validate_uuid(errors, "restaurantId", self.restaurantId)

        _require(errors, "assetType", self.assetType)
        if self.assetType and self.assetType not in ALLOWED_ASSET_TYPES:
            errors["assetType"] = f"must be one of {sorted(ALLOWED_ASSET_TYPES)}"

        _require(errors, "contentType", self.contentType)
        if self.contentType and self.contentType not in ALLOWED_CONTENT_TYPES:
            errors["contentType"] = f"must be one of {sorted(ALLOWED_CONTENT_TYPES)}"

        if errors:
            raise ValidationError(errors)

    def s3_key(self) -> str:
        """Derive the S3 key based on asset type."""
        base = f"TENANT#{self.tenantId}/restaurants/{self.restaurantId}"
        if self.assetType == "logo":
            return f"{base}/logo.webp"
        if self.assetType == "category-image":
            return f"{base}/categories/{self.entityId or 'unknown'}.webp"
        if self.assetType == "item-image":
            return f"{base}/items/{self.entityId or 'unknown'}.webp"
        if self.assetType == "ar-model":
            return f"ar-models/pending/{self.restaurantId}/{self.entityId or 'unknown'}.glb"
        return f"{base}/misc/{self.entityId}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PresignedUrlRequest":
        return cls(
            tenantId=data.get("tenantId", ""),
            restaurantId=data.get("restaurantId", ""),
            assetType=data.get("assetType", ""),
            contentType=data.get("contentType", ""),
            entityId=data.get("entityId"),
        )


# ── Pagination ────────────────────────────────────────────────────────────

@dataclass
class PaginatedResponse:
    items: list[Any]
    count: int
    lastEvaluatedKey: Optional[str] = None   # base64-encoded DDB LEK

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"items": self.items, "count": self.count}
        if self.lastEvaluatedKey:
            data["lastEvaluatedKey"] = self.lastEvaluatedKey
        return data