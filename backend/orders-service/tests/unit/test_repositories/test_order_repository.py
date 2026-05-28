"""tests/unit/test_repositories/test_order_repository.py"""

import pytest
from datetime import datetime, timezone
from moto import mock_aws
from unittest.mock import MagicMock

from orders_service.models import OrderRequest, OrderRecord
from orders_service.repositories.order_repository import OrderRepository, DuplicateOrderError
from shared.exceptions import ResourceNotFoundError


def _make_record(order_id="ord-1", tenant_id="t1") -> OrderRecord:
    req = OrderRequest(
        tenantId=tenant_id, restaurantId="r1", tableId="tb1",
        currencyCode="PKR",
        lineItems=[{"itemId": "i1", "name": "B", "quantity": 1,
                    "unitPriceMinorUnits": 100, "totalPriceMinorUnits": 100}],
        totalAmountMinorUnits=100,
    )
    return OrderRecord.build(req, order_id, "arn:exec", datetime.now(timezone.utc))


@mock_aws
class TestOrderRepository:
    def test_write_and_get_order(self, order_repo):
        record = _make_record()
        order_repo.write_order(record)
        result = order_repo.get_order("ord-1", "t1")
        assert result is not None
        assert result["orderId"] == "ord-1"

    def test_get_nonexistent_order_returns_none(self, order_repo):
        result = order_repo.get_order("no-such-id", "t1")
        assert result is None

    def test_duplicate_write_raises(self, order_repo):
        record = _make_record()
        order_repo.write_order(record)
        with pytest.raises(DuplicateOrderError):
            order_repo.write_order(record)

    def test_rollback_deletes_order(self, order_repo):
        record = _make_record()
        order_repo.write_order(record)
        order_repo.rollback_order(record)
        assert order_repo.get_order("ord-1", "t1") is None

    def test_update_status(self, order_repo):
        record = _make_record()
        order_repo.write_order(record)
        order_repo.update_status("ord-1", "t1", "PREPARING")
        result = order_repo.get_order("ord-1", "t1")
        assert result["status"] == "PREPARING"

    def test_update_status_not_found_raises(self, order_repo):
        with pytest.raises(ResourceNotFoundError):
            order_repo.update_status("ghost-order", "t1", "READY")

    def test_list_orders_returns_recent(self, order_repo):
        record = _make_record(order_id="ord-list", tenant_id="t1")
        order_repo.write_order(record)
        orders = order_repo.list_orders("r1", "t1", hours=4)
        assert any(o["orderId"] == "ord-list" for o in orders)

    def test_list_orders_filters_by_tenant(self, order_repo):
        record = _make_record(order_id="ord-t2", tenant_id="t2")
        order_repo.write_order(record)
        orders = order_repo.list_orders("r1", "t1", hours=4)
        assert all(o.get("tenantId") == "t1" for o in orders)
