"""
test_dynamo.py — Unit tests for orders_lambda.dynamo
"""
from datetime import datetime, timezone

import pytest
from moto import mock_aws

from orders_lambda.dynamo import DuplicateOrderError, rollback_order, write_order
from orders_lambda.models import OrderRecord, OrderRequest


@pytest.fixture
def sample_request():
    return OrderRequest.model_validate(
        {
            "tenantId": "t123",
            "restaurantId": "r456",
            "tableId": "table-07",
            "currencyCode": "PKR",
            "lineItems": [
                {
                    "itemId": "item-001",
                    "name": "Burger",
                    "quantity": 1,
                    "unitPriceMinorUnits": 1000,
                    "totalPriceMinorUnits": 1000,
                }
            ],
            "totalAmountMinorUnits": 1000,
        }
    )


@pytest.fixture
def sample_record(sample_request):
    return OrderRecord.build(
        sample_request,
        order_id="ord-test-123",
        execution_arn="arn:aws:states:us-east-1:123:execution:SM:ord-test-123",
        now=datetime(2025, 4, 21, 12, 0, 0, tzinfo=timezone.utc),
    )


@mock_aws
def test_write_order_success(dynamodb, sample_record):
    resource, _ = dynamodb
    write_order(resource, "OrderTable-test", sample_record)

    table = resource.Table("OrderTable-test")
    item = table.get_item(Key={"PK": sample_record.PK, "SK": sample_record.SK})["Item"]
    assert item["orderId"] == "ord-test-123"
    assert item["status"] == "RECEIVED"


@mock_aws
def test_write_order_duplicate_raises(dynamodb, sample_record):
    resource, _ = dynamodb
    write_order(resource, "OrderTable-test", sample_record)

    with pytest.raises(DuplicateOrderError):
        write_order(resource, "OrderTable-test", sample_record)


@mock_aws
def test_rollback_deletes_item(dynamodb, sample_record):
    resource, _ = dynamodb
    write_order(resource, "OrderTable-test", sample_record)

    rollback_order(resource, "OrderTable-test", sample_record)

    table = resource.Table("OrderTable-test")
    response = table.get_item(Key={"PK": sample_record.PK, "SK": sample_record.SK})
    assert "Item" not in response


@mock_aws
def test_rollback_non_existent_item_does_not_raise(dynamodb, sample_record):
    resource, _ = dynamodb
    # No write — rollback should silently succeed
    rollback_order(resource, "OrderTable-test", sample_record)
