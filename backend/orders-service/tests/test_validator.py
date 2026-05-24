"""
test_validator.py — Unit tests for orders_lambda.validator
"""
import pytest
from moto import mock_aws

from orders_lambda.models import LineItem
from orders_lambda.validator import ValidationError, validate_menu_items


@pytest.fixture
def line_items():
    return [
        LineItem(
            itemId="item-001",
            name="Chicken Burger",
            quantity=3,
            unitPriceMinorUnits=1200,
            totalPriceMinorUnits=3600,
        )
    ]


@mock_aws
def test_valid_item_passes(dynamodb, seed_menu, line_items):
    _, client = dynamodb
    # Should not raise
    validate_menu_items(client, "MenuTable-test", "r456", line_items)


@mock_aws
def test_missing_item_raises(dynamodb, line_items):
    _, client = dynamodb
    # No menu seeded → item not found
    with pytest.raises(ValidationError, match="not found"):
        validate_menu_items(client, "MenuTable-test", "r456", line_items)


@mock_aws
def test_price_mismatch_raises(dynamodb, line_items):
    _, client = dynamodb
    # Seed with different price
    client.put_item(
        TableName="MenuTable-test",
        Item={
            "PK": {"S": "RESTAURANT#r456"},
            "SK": {"S": "ITEM#item-001"},
            "priceMinorUnits": {"N": "999"},
            "available": {"BOOL": True},
        },
    )
    with pytest.raises(ValidationError, match="price mismatch"):
        validate_menu_items(client, "MenuTable-test", "r456", line_items)


@mock_aws
def test_unavailable_item_raises(dynamodb, line_items):
    _, client = dynamodb
    client.put_item(
        TableName="MenuTable-test",
        Item={
            "PK": {"S": "RESTAURANT#r456"},
            "SK": {"S": "ITEM#item-001"},
            "priceMinorUnits": {"N": "1200"},
            "available": {"BOOL": False},
        },
    )
    with pytest.raises(ValidationError, match="unavailable"):
        validate_menu_items(client, "MenuTable-test", "r456", line_items)


@mock_aws
def test_multiple_errors_reported_together(dynamodb):
    _, client = dynamodb
    items = [
        LineItem(itemId="x", name="X", quantity=1, unitPriceMinorUnits=100, totalPriceMinorUnits=100),
        LineItem(itemId="y", name="Y", quantity=1, unitPriceMinorUnits=200, totalPriceMinorUnits=200),
    ]
    with pytest.raises(ValidationError) as exc_info:
        validate_menu_items(client, "MenuTable-test", "r456", items)
    error_msg = str(exc_info.value)
    assert "x" in error_msg
    assert "y" in error_msg
