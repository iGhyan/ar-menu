"""
tests/conftest.py
==================
Shared fixtures for WebSocket Lambda tests.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time

import pytest
from unittest.mock import MagicMock

# ── sys.path ──────────────────────────────────────────────────────────────────
_ROOT  = os.path.join(os.path.dirname(__file__), "..")
_LAYER = os.path.join(_ROOT, "layer", "python")
sys.path.insert(0, _ROOT)
sys.path.insert(0, _LAYER)

# ── Env vars BEFORE any imports ───────────────────────────────────────────────
os.environ.setdefault("REDIS_URL",           "redis://localhost:6379")
os.environ.setdefault("TABLE_CONN",          "ConnectionTable-dev")
os.environ.setdefault("TABLE_ORDER",         "OrderTable-dev")
os.environ.setdefault("STEP_ARN",            "arn:aws:states:us-east-1:123:stateMachine:test")
os.environ.setdefault("DLQ_URL",             "https://sqs.us-east-1.amazonaws.com/123/test-dlq")
os.environ.setdefault("AWS_DEFAULT_REGION",  "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID",   "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY","testing")
os.environ.setdefault("COGNITO_REGION",      "ap-south-1")
os.environ.setdefault("COGNITO_USER_POOL_ID","ap-south-1_SCyQ50etN")
os.environ.setdefault("COGNITO_CLIENT_ID",   "7903hkujl9qeq67toemi5qrhes")


@pytest.fixture(autouse=True)
def clear_client_cache():
    from shared.aws_clients import clear_cache
    clear_cache()
    yield
    clear_cache()


# ── JWT builder ───────────────────────────────────────────────────────────────

def _b64(data: dict) -> str:
    raw = json.dumps(data).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_jwt(
    sub:       str  = "user-sub-123",
    email:     str  = "user@example.com",
    tenant_id: str  = "tenant-abc",
    groups:    list = None,
    expired:   bool = False,
    issuer:    str  = None,
    client_id: str  = None,
    token_use: str  = "access",
) -> str:
    """Build a minimal unsigned JWT for testing."""
    if groups is None:
        groups = ["menulay_tenant"]

    exp = int(time.time()) + (-100 if expired else 3600)
    iss = issuer or (
        "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_SCyQ50etN"
    )
    cid = client_id or "7903hkujl9qeq67toemi5qrhes"

    header  = _b64({"alg": "RS256", "typ": "JWT"})
    payload = _b64({
        "sub":              sub,
        "email":            email,
        "custom:tenant_id": tenant_id,
        "cognito:groups":   groups,
        "exp":              exp,
        "iss":              iss,
        "client_id":        cid,
        "token_use":        token_use,
    })
    return f"{header}.{payload}.fake-sig"


# ── Event builders ────────────────────────────────────────────────────────────

def ws_event(
    route:         str         = "$connect",
    connection_id: str         = "conn-abc",
    headers:       dict | None = None,
    body:          str | None  = None,
    query_params:  dict | None = None,
) -> dict:
    return {
        "requestContext": {
            "routeKey":     route,
            "connectionId": connection_id,
        },
        "headers":               headers or {},
        "body":                  body,
        "queryStringParameters": query_params,
    }


def connect_event(token: str, connection_id: str = "conn-abc") -> dict:
    return ws_event(
        route="$connect",
        connection_id=connection_id,
        headers={"Authorization": f"Bearer {token}"},
    )


def message_event(body: dict, connection_id: str = "conn-abc") -> dict:
    return ws_event(
        route="$default",
        connection_id=connection_id,
        body=json.dumps(body),
    )


# ── Mock context ──────────────────────────────────────────────────────────────

@pytest.fixture
def mock_context():
    ctx = MagicMock()
    ctx.aws_request_id   = "test-req-ws"
    ctx.function_name    = "ws-lambda"
    ctx.function_version = "$LATEST"
    return ctx
