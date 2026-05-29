"""Address map model used by Restaurant."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import BaseModel, ValidationError, _require


@dataclass
class Address(BaseModel):
    street: str
    city: str
    country: str
    postcode: str

    def validate(self) -> None:
        errors: dict[str, str] = {}
        _require(errors, "street", self.street)
        _require(errors, "city", self.city)
        _require(errors, "country", self.country)
        _require(errors, "postcode", self.postcode)
        if errors:
            raise ValidationError(errors)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Address":
        return cls(
            street=data.get("street", ""),
            city=data.get("city", ""),
            country=data.get("country", ""),
            postcode=data.get("postcode", ""),
        )