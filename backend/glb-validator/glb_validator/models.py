"""
glb_validator/models.py
========================
Data models for GLB validation results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ValidationResult:
    """
    Outcome of validating a single GLB file.

    Attributes
    ----------
    status          : "approved" | "rejected" | "skipped"
    original_key    : The S3 key that was processed
    destination_key : Where the file was moved (approved/ or rejected/)
    reason          : Human-readable rejection reason (empty on approval)
    error_code      : Machine-readable code (empty on approval)
    tenant_id       : Extracted tenant UUID (empty if key was invalid)
    restaurant_id   : Extracted restaurant UUID
    filename        : Just the filename portion of the key
    metadata        : Any extra k/v to surface in the response
    """

    status:          Literal["approved", "rejected", "skipped"]
    original_key:    str
    destination_key: str = ""
    reason:          str = ""
    error_code:      str = ""
    tenant_id:       str = ""
    restaurant_id:   str = ""
    filename:        str = ""
    metadata:        dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d: dict = {
            "result":       self.status,
            "key":          self.original_key,
        }
        if self.destination_key:
            d["dest"]     = self.destination_key
        if self.tenant_id:
            d["tenantId"] = self.tenant_id
        if self.restaurant_id:
            d["restaurantId"] = self.restaurant_id
        if self.reason:
            d["reason"]   = self.reason
        if self.error_code:
            d["errorCode"] = self.error_code
        if self.metadata:
            d["metadata"] = self.metadata
        return d
