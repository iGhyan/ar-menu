"""
conftest.py — Shared pytest fixtures.

AWS mocked with moto. Redis mocked with fakeredis.
All fixtures are function-scoped (fresh state per test).
"""
from __future__ import annotations

import json
import os

import boto3
import fakeredis
import pytest
from moto import mock_aws

# ── Env vars must be set before importing handler ──────────────────────────────
os.environ.update(
    {
        "TABLE_ORDER": "OrderTable-test",
        "TABLE_MENU": "MenuTable-test",
        "STEP_ARN": "arn:aws:states:us-east-1:123456789012:stateMachine:OrderSM",
        "EVENT_BUS": "orders-event-bus",
        "DLQ_URL": "https://sqs.us-east-1.amazonaws.com/123456789012/orders-dlq",
        "REDIS_HOST": "localhost",
        "REDIS_PORT": "6379",
        "LOG_LEVEL": "WARNING",
    }
)


@pytest.fixture
def aws_credentials(monkeypatch):
    """Fake AWS credentials for moto."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")


@pytest.fixture
def dynamodb(aws_credentials):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")

        # OrderTable
        client.create_table(
            TableName="OrderTable-test",
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "restaurantId", "AttributeType": "S"},
                {"AttributeName": "placedAt", "AttributeType": "S"},
                {"AttributeName": "tableId", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
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
                        {"AttributeName": "placedAt", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "GSI-2-table-orders",
                    "KeySchema": [
                        {"AttributeName": "tableId", "KeyType": "HASH"},
                        {"AttributeName": "placedAt", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "KEYS_ONLY"},
                },
                {
                    "IndexName": "GSI-3-status-orders",
                    "KeySchema": [
                        {"AttributeName": "status", "KeyType": "HASH"},
                        {"AttributeName": "placedAt", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "KEYS_ONLY"},
                },
            ],
        )

        # MenuTable
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

        yield boto3.resource("dynamodb", region_name="us-east-1"), client


@pytest.fixture
def seed_menu(dynamodb):
    """Insert a valid menu item into MenuTable."""
    _, client = dynamodb
    client.put_item(
        TableName="MenuTable-test",
        Item={
            "PK": {"S": "RESTAURANT#r456"},
            "SK": {"S": "ITEM#item-001"},
            "priceMinorUnits": {"N": "1200"},
            "available": {"BOOL": True},
            "name": {"S": "Chicken Burger"},
        },
    )


@pytest.fixture
def fake_redis(monkeypatch):
    """Replace the Redis client with fakeredis."""
    import orders_lambda.cart as cart_module

    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    cart_module._redis_client = client
    yield client
    cart_module._redis_client = None


@pytest.fixture
def valid_payload():
    return {
        "tenantId": "t123",
        "restaurantId": "r456",
        "tableId": "table-07",
        "currencyCode": "PKR",
        "lineItems": [
            {
                "itemId": "item-001",
                "name": "Chicken Burger",
                "quantity": 3,
                "unitPriceMinorUnits": 1200,
                "totalPriceMinorUnits": 3600,
            }
        ],
        "totalAmountMinorUnits": 3600,
    }


@pytest.fixture
def lambda_context():
    class Context:
        aws_request_id = "test-request-id"
        function_name = "orders-lambda"
        memory_limit_in_mb = 256

    return Context()
