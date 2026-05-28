"""
tests/conftest.py
==================
Shared pytest fixtures. AWS mocked with moto, Redis with fakeredis.
"""

from __future__ import annotations

import os
import sys

import boto3
import pytest
from moto import mock_aws

# ── sys.path ──────────────────────────────────────────────────────────────────
_ROOT  = os.path.join(os.path.dirname(__file__), "..")
_LAYER = os.path.join(_ROOT, "layer", "python")
sys.path.insert(0, _ROOT)
sys.path.insert(0, _LAYER)

# ── Env vars BEFORE any imports ───────────────────────────────────────────────
os.environ.update({
    "TABLE_ORDER":          "OrderTable-test",
    "TABLE_MENU":           "MenuTable-test",
    "STEP_ARN":             "arn:aws:states:us-east-1:123456789012:stateMachine:OrderSM",
    "REDIS_HOST":           "localhost",
    "REDIS_PORT":           "6379",
    "SKIP_MENU":            "false",
    "LOG_LEVEL":            "WARNING",
    "AWS_DEFAULT_REGION":   "us-east-1",
    "AWS_ACCESS_KEY_ID":    "testing",
    "AWS_SECRET_ACCESS_KEY":"testing",
    "AWS_SECURITY_TOKEN":   "testing",
    "AWS_SESSION_TOKEN":    "testing",
})


@pytest.fixture(autouse=True)
def clear_client_cache():
    from shared.aws_clients import clear_cache
    clear_cache()
    yield
    clear_cache()


# ── DynamoDB (moto) ───────────────────────────────────────────────────────────

@pytest.fixture
def dynamodb():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")

        client.create_table(
            TableName="OrderTable-test",
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "PK",           "AttributeType": "S"},
                {"AttributeName": "SK",           "AttributeType": "S"},
                {"AttributeName": "restaurantId", "AttributeType": "S"},
                {"AttributeName": "placedAt",     "AttributeType": "S"},
                {"AttributeName": "tableId",      "AttributeType": "S"},
                {"AttributeName": "status",       "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI-1-restaurant-orders",
                    "KeySchema": [
                        {"AttributeName": "restaurantId", "KeyType": "HASH"},
                        {"AttributeName": "placedAt",     "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "GSI-2-table-orders",
                    "KeySchema": [
                        {"AttributeName": "tableId",  "KeyType": "HASH"},
                        {"AttributeName": "placedAt", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "KEYS_ONLY"},
                },
                {
                    "IndexName": "GSI-3-status-orders",
                    "KeySchema": [
                        {"AttributeName": "status",   "KeyType": "HASH"},
                        {"AttributeName": "placedAt", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "KEYS_ONLY"},
                },
            ],
        )

        client.create_table(
            TableName="MenuTable-test",
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
        )

        resource = boto3.resource("dynamodb", region_name="us-east-1")
        yield resource, client


@pytest.fixture
def seed_menu(dynamodb):
    """Insert a valid menu item into MenuTable."""
    _, client = dynamodb
    client.put_item(
        TableName="MenuTable-test",
        Item={
            "PK":              {"S": "RESTAURANT#r456"},
            "SK":              {"S": "ITEM#item-001"},
            "priceMinorUnits": {"N": "1200"},
            "available":       {"BOOL": True},
            "name":            {"S": "Chicken Burger"},
        },
    )


@pytest.fixture
def fake_redis(monkeypatch):
    """Replace Redis client with fakeredis."""
    import fakeredis
    from orders_service.channels import cart_service as cart_module

    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)

    monkeypatch.setattr(cart_module, "_redis_client", client)
    yield client
    monkeypatch.setattr(cart_module, "_redis_client", None)


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def valid_payload():
    return {
        "tenantId":             "t123",
        "restaurantId":         "r456",
        "tableId":              "table-07",
        "currencyCode":         "PKR",
        "lineItems": [{
            "itemId":               "item-001",
            "name":                 "Chicken Burger",
            "quantity":             3,
            "unitPriceMinorUnits":  1200,
            "totalPriceMinorUnits": 3600,
        }],
        "totalAmountMinorUnits": 3600,
    }


@pytest.fixture
def lambda_context():
    class Ctx:
        aws_request_id    = "test-request-id"
        function_name     = "orders-lambda"
        function_version  = "$LATEST"
        memory_limit_in_mb = 256
    return Ctx()


@pytest.fixture
def order_repo(dynamodb):
    from orders_service.repositories.order_repository import OrderRepository
    resource, client = dynamodb
    return OrderRepository(
        dynamodb_resource=resource,
        dynamodb_client=client,
        table_name="OrderTable-test",
    )
