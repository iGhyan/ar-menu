"""
tests/conftest.py
Shared fixtures and env var patches for all notification lambda tests.
"""

import json
import pytest
from unittest.mock import MagicMock, patch


# ── Base env vars ─────────────────────────────────────────────────────────────

BASE_ENV = {
    "SNS_TOPIC":              "arn:aws:sns:us-east-1:123456789:orders-topic",
    "REDIS_URL":              "redis://localhost:6379",
    "WS_ENDPOINT":            "https://abc123.execute-api.us-east-1.amazonaws.com/prod",
    "WS_CONNECTIONS_TABLE":   "WsConnectionsTable",
    "PINPOINT_APP_ID":        "test-pinpoint-app-id",
    "PINPOINT_FROM_NUMBER":   "+12345678900",
    "TABLE_ORDER":            "OrderTable",
    "AWS_REGION":             "us-east-1",
}


# ── Sample payloads ───────────────────────────────────────────────────────────

@pytest.fixture
def order_payload():
    return {
        "orderId":  "ORD-123",
        "tenantId": "TENANT-A",
        "status":   "READY",
        "userId":   "USER-42",
        "phone":    "+923001234567",
        "email":    "user@example.com",
        "message":  "Your order is ready!",
    }


@pytest.fixture
def order_payload_no_contact(order_payload):
    """Payload without phone/email/userId — skips WS and Pinpoint."""
    return {**order_payload, "phone": None, "email": None, "userId": None}


@pytest.fixture
def sqs_event(order_payload):
    return {
        "Records": [
            {
                "messageId": "msg-001",
                "body": json.dumps(order_payload),
            }
        ]
    }


@pytest.fixture
def eb_event(order_payload):
    """EventBridge envelope wrapping the order payload."""
    return {
        "Records": [
            {
                "messageId": "msg-001",
                "body": json.dumps({
                    "detail-type": "ORDER_STATUS_CHANGED",
                    "source":      "orders-lambda",
                    "detail":      order_payload,
                }),
            }
        ]
    }


# ── AWS mock helpers ──────────────────────────────────────────────────────────

@pytest.fixture
def mock_sns():
    with patch("handler.sns") as m:
        m.publish.return_value = {"MessageId": "test-sns-msg-id"}
        yield m


@pytest.fixture
def mock_apigw():
    with patch("handler.apigw_mgmt") as m:
        m.post_to_connection.return_value = {}
        m.exceptions.GoneException = type("GoneException", (Exception,), {})
        yield m


@pytest.fixture
def mock_pinpoint():
    with patch("handler.pinpoint") as m:
        m.send_messages.return_value = {"MessageResponse": {}}
        yield m


@pytest.fixture
def mock_connections_table():
    with patch("handler.connections_table") as m:
        m.query.return_value = {
            "Items": [{"userId": "USER-42", "connectionId": "conn-abc"}]
        }
        yield m


@pytest.fixture
def mock_redis_unavailable():
    with patch("handler.get_redis", return_value=None):
        yield


@pytest.fixture
def mock_redis_with_connection():
    r = MagicMock()
    r.smembers.return_value = {"conn-from-redis"}
    with patch("handler.get_redis", return_value=r):
        yield r


@pytest.fixture
def mock_redis_timeout():
    r = MagicMock()
    r.smembers.side_effect = Exception("redis timeout")
    with patch("handler.get_redis", return_value=r):
        yield r


# ── Combined "all mocked" fixture ─────────────────────────────────────────────

@pytest.fixture
def all_mocks(mock_sns, mock_apigw, mock_pinpoint,
              mock_connections_table, mock_redis_unavailable):
    """Convenience: all AWS services mocked, Redis unavailable → DDB path."""
    return {
        "sns":        mock_sns,
        "apigw":      mock_apigw,
        "pinpoint":   mock_pinpoint,
        "conns_table": mock_connections_table,
    }