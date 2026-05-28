"""tests/unit/test_models.py"""

import pytest
from datetime import datetime, timezone
from orders_service.models import (
    LineItem, OrderRequest, OrderRecord, OrderStatusUpdate,
    clean_decimals, to_dynamo_types,
)
from decimal import Decimal


class TestLineItem:
    def test_valid_line_item(self):
        item = LineItem(
            itemId="i1", name="Burger",
            quantity=2, unitPriceMinorUnits=1000, totalPriceMinorUnits=2000
        )
        assert item.totalPriceMinorUnits == 2000

    def test_total_mismatch_raises(self):
        with pytest.raises(Exception):
            LineItem(
                itemId="i1", name="Burger",
                quantity=2, unitPriceMinorUnits=1000, totalPriceMinorUnits=9999
            )

    def test_zero_quantity_raises(self):
        with pytest.raises(Exception):
            LineItem(itemId="i1", name="x", quantity=0,
                     unitPriceMinorUnits=100, totalPriceMinorUnits=0)


class TestOrderRequest:
    def _make(self, **overrides):
        base = {
            "tenantId": "t1", "restaurantId": "r1", "tableId": "tb1",
            "currencyCode": "PKR",
            "lineItems": [{
                "itemId": "i1", "name": "Burger",
                "quantity": 2, "unitPriceMinorUnits": 1000, "totalPriceMinorUnits": 2000
            }],
            "totalAmountMinorUnits": 2000,
        }
        return {**base, **overrides}

    def test_valid_request(self):
        req = OrderRequest(**self._make())
        assert req.tenantId == "t1"
        assert len(req.lineItems) == 1

    def test_total_mismatch_raises(self):
        with pytest.raises(Exception):
            OrderRequest(**self._make(totalAmountMinorUnits=9999))

    def test_empty_line_items_raises(self):
        with pytest.raises(Exception):
            OrderRequest(**self._make(lineItems=[]))

    def test_guest_connection_id_optional(self):
        req = OrderRequest(**self._make())
        assert req.guestConnectionId is None

        req2 = OrderRequest(**self._make(guestConnectionId="conn-abc"))
        assert req2.guestConnectionId == "conn-abc"


class TestOrderStatusUpdate:
    def test_cancelled_status(self):
        u = OrderStatusUpdate(tenantId="t1", cancelled=True)
        assert u.derived_status == "CANCELLED"

    def test_delivered_status(self):
        u = OrderStatusUpdate(tenantId="t1", delivered=True)
        assert u.derived_status == "DELIVERED"

    def test_ready_status(self):
        u = OrderStatusUpdate(tenantId="t1", foodReady=True)
        assert u.derived_status == "READY"

    def test_preparing_status(self):
        u = OrderStatusUpdate(tenantId="t1", kitchenAccepted=True)
        assert u.derived_status == "PREPARING"

    def test_default_received_status(self):
        u = OrderStatusUpdate(tenantId="t1")
        assert u.derived_status == "RECEIVED"

    def test_cancelled_takes_priority_over_delivered(self):
        u = OrderStatusUpdate(tenantId="t1", cancelled=True, delivered=True)
        assert u.derived_status == "CANCELLED"


class TestOrderRecord:
    def test_build_creates_correct_pk(self):
        req = OrderRequest(
            tenantId="t1", restaurantId="r1", tableId="tb1",
            currencyCode="PKR",
            lineItems=[{"itemId": "i1", "name": "B", "quantity": 1,
                        "unitPriceMinorUnits": 100, "totalPriceMinorUnits": 100}],
            totalAmountMinorUnits=100,
        )
        now    = datetime.now(timezone.utc)
        record = OrderRecord.build(req, "order-123", "arn:exec", now)

        assert record.PK == "TENANT#t1#ORDER#order-123"
        assert record.SK.startswith("STATUS#")
        assert record.status == "RECEIVED"
        assert record.orderId == "order-123"
        assert record.ttl > 0

    def test_to_dynamo_item_excludes_none(self):
        req = OrderRequest(
            tenantId="t1", restaurantId="r1", tableId="tb1",
            currencyCode="PKR",
            lineItems=[{"itemId": "i1", "name": "B", "quantity": 1,
                        "unitPriceMinorUnits": 100, "totalPriceMinorUnits": 100}],
            totalAmountMinorUnits=100,
        )
        now    = datetime.now(timezone.utc)
        record = OrderRecord.build(req, "order-123", "arn:exec", now)
        item   = record.to_dynamo_item()
        assert "guestConnectionId" not in item  # None values excluded


class TestHelpers:
    def test_clean_decimals_converts_decimal(self):
        assert clean_decimals(Decimal("42")) == 42

    def test_clean_decimals_recursive_dict(self):
        result = clean_decimals({"a": Decimal("1"), "b": [Decimal("2")]})
        assert result == {"a": 1, "b": [2]}

    def test_to_dynamo_types_converts_float(self):
        result = to_dynamo_types({"price": 1.5})
        assert isinstance(result["price"], Decimal)

    def test_to_dynamo_types_nested(self):
        result = to_dynamo_types({"item": {"price": 2.5}})
        assert isinstance(result["item"]["price"], Decimal)
