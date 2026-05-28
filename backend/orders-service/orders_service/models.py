"""
orders_service/models.py
=========================
Pydantic domain models for the Orders service.

Models
------
LineItem        — single line item in an order
OrderRequest    — incoming POST /orders payload (validated)
OrderRecord     — DynamoDB record shape (what gets written/read)
OrderStatusUpdate — incoming PATCH /orders/{id} payload
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field, validator, root_validator


# ── Line Item ─────────────────────────────────────────────────────────────────

class LineItem(BaseModel):
    itemId:               str
    name:                 str
    quantity:             int = Field(..., gt=0)
    unitPriceMinorUnits:  int = Field(..., gt=0)
    totalPriceMinorUnits: int = Field(..., gt=0)

    @validator("totalPriceMinorUnits")
    def validate_total(cls, v, values):
        qty  = values.get("quantity")
        unit = values.get("unitPriceMinorUnits")
        if qty and unit and v != qty * unit:
            raise ValueError(
                f"totalPriceMinorUnits {v} != quantity({qty}) * unitPrice({unit})"
            )
        return v


# ── Order Request (POST body) ─────────────────────────────────────────────────

class OrderRequest(BaseModel):
    tenantId:             str
    restaurantId:         str
    tableId:              str
    currencyCode:         str
    lineItems:            List[LineItem] = Field(..., min_items=1)
    totalAmountMinorUnits: int = Field(..., gt=0)
    guestConnectionId:    Optional[str] = None

    @root_validator
    def validate_total_amount(cls, values):
        items   = values.get("lineItems", [])
        total   = values.get("totalAmountMinorUnits")
        computed = sum(i.totalPriceMinorUnits for i in items)
        if total and computed and total != computed:
            raise ValueError(
                f"totalAmountMinorUnits {total} != sum of line items {computed}"
            )
        return values


# ── Order Status Update (PATCH body) ─────────────────────────────────────────

class OrderStatusUpdate(BaseModel):
    tenantId:        str
    kitchenAccepted: bool = False
    foodReady:       bool = False
    delivered:       bool = False
    cancelled:       bool = False

    @property
    def derived_status(self) -> str:
        """Derive canonical status string from boolean flags."""
        if self.cancelled:       return "CANCELLED"
        if self.delivered:       return "DELIVERED"
        if self.foodReady:       return "READY"
        if self.kitchenAccepted: return "PREPARING"
        return "RECEIVED"


# ── Order Record (DynamoDB shape) ─────────────────────────────────────────────

class OrderRecord(BaseModel):
    PK:                        str
    SK:                        str
    orderId:                   str
    tenantId:                  str
    restaurantId:              str
    tableId:                   str
    status:                    str
    lineItems:                 List[dict]
    totalAmountMinorUnits:     int
    currencyCode:              str
    stepFunctionsExecutionArn: str
    guestConnectionId:         Optional[str]
    placedAt:                  str
    updatedAt:                 str
    ttl:                       int

    @classmethod
    def build(
        cls,
        request:       OrderRequest,
        order_id:      str,
        execution_arn: str,
        now:           datetime,
    ) -> "OrderRecord":
        placed_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        ttl       = int((now + timedelta(days=90)).timestamp())
        return cls(
            PK=f"TENANT#{request.tenantId}#ORDER#{order_id}",
            SK=f"STATUS#{placed_at}",
            orderId=order_id,
            tenantId=request.tenantId,
            restaurantId=request.restaurantId,
            tableId=request.tableId,
            status="RECEIVED",
            lineItems=[item.dict() for item in request.lineItems],
            totalAmountMinorUnits=request.totalAmountMinorUnits,
            currencyCode=request.currencyCode,
            stepFunctionsExecutionArn=execution_arn,
            guestConnectionId=request.guestConnectionId,
            placedAt=placed_at,
            updatedAt=placed_at,
            ttl=ttl,
        )

    def to_dynamo_item(self) -> dict:
        return {k: v for k, v in self.dict().items() if v is not None}


# ── Helpers ───────────────────────────────────────────────────────────────────

def clean_decimals(obj):
    """Recursively convert Decimal → int for JSON serialisation."""
    if isinstance(obj, list):    return [clean_decimals(i) for i in obj]
    if isinstance(obj, dict):    return {k: clean_decimals(v) for k, v in obj.items()}
    if isinstance(obj, Decimal): return int(obj)
    return obj


def to_dynamo_types(obj: dict) -> dict:
    """Recursively convert float → Decimal for DynamoDB compatibility."""
    result = {}
    for k, v in obj.items():
        if isinstance(v, float):
            result[k] = Decimal(str(v))
        elif isinstance(v, list):
            result[k] = [to_dynamo_types(i) if isinstance(i, dict) else i for i in v]
        elif isinstance(v, dict):
            result[k] = to_dynamo_types(v)
        else:
            result[k] = v
    return result
