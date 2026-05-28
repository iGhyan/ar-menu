"""
tests/conftest.py
==================
Shared fixtures and environment bootstrap for ar-assets-lambda tests.
"""

import os
import sys
import time
import base64
import json

import pytest

# ── Env vars before any import ────────────────────────────────────────────────
os.environ.setdefault("ASSET_BUCKET_NAME",    "test-bucket")
os.environ.setdefault("CF_DOMAIN",            "d1234abcd.cloudfront.net")
os.environ.setdefault("TABLE_MENU",           "MenuTable")
os.environ.setdefault("AWS_DEFAULT_REGION",   "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID",    "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY","testing")
os.environ.setdefault("COGNITO_REGION",       "ap-south-1")
os.environ.setdefault("COGNITO_USER_POOL_ID", "ap-south-1_SCyQ50etN")
os.environ.setdefault("COGNITO_CLIENT_ID",    "7903hkujl9qeq67toemi5qrhes")

# ── sys.path setup ────────────────────────────────────────────────────────────
_ROOT  = os.path.join(os.path.dirname(__file__), "..")
_LAYER = os.path.join(_ROOT, "layer", "python")
sys.path.insert(0, _ROOT)
sys.path.insert(0, _LAYER)


@pytest.fixture(autouse=True)
def clear_client_cache():
    from shared.aws_clients import clear_cache
    clear_cache()
    yield
    clear_cache()


# ── JWT helper ────────────────────────────────────────────────────────────────

def _b64(data: dict) -> str:
    raw = json.dumps(data).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_jwt(
    sub:       str   = "user-sub-123",
    email:     str   = "user@example.com",
    tenant_id: str   = "tenant-abc",
    groups:    list  = None,
    expired:   bool  = False,
    issuer:    str   = None,
    client_id: str   = None,
    token_use: str   = "access",
) -> str:
    """Build a fake JWT (unsigned) for testing."""
    if groups is None:
        groups = ["menulay_tenant"]

    exp = int(time.time()) + (-100 if expired else 3600)
    iss = issuer or (
        f"https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_SCyQ50etN"
    )
    cid = client_id or "7903hkujl9qeq67toemi5qrhes"

    header  = _b64({"alg": "RS256", "typ": "JWT"})
    payload = _b64({
        "sub":               sub,
        "email":             email,
        "custom:tenant_id":  tenant_id,
        "cognito:groups":    groups,
        "exp":               exp,
        "iss":               iss,
        "client_id":         cid,
        "token_use":         token_use,
    })
    return f"{header}.{payload}.fake-signature"


def auth_event(
    method: str,
    restaurant_id: str = "rest-1",
    item_id:       str = "item-1",
    body:          dict | None = None,
    token:         str | None = None,
    tenant_id_header: str | None = None,
) -> dict:
    """Build an API Gateway event with Authorization header."""
    import json as _json
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if tenant_id_header:
        headers["x-tenant-id"] = tenant_id_header

    return {
        "httpMethod":      method,
        "pathParameters":  {"restaurantId": restaurant_id, "itemId": item_id},
        "headers":         headers,
        "queryStringParameters": None,
        "body":            _json.dumps(body) if body else None,
    }


@pytest.fixture
def mock_context():
    from unittest.mock import MagicMock
    ctx = MagicMock()
    ctx.aws_request_id   = "test-request-id-ar"
    ctx.function_name    = "ar-assets-test"
    ctx.function_version = "$LATEST"
    return ctx


@pytest.fixture
def admin_token():
    return make_jwt(groups=["menulay_admin"], tenant_id="tenant-abc")


@pytest.fixture
def tenant_token():
    return make_jwt(groups=["menulay_tenant"], tenant_id="tenant-abc")


@pytest.fixture
def kitchen_token():
    return make_jwt(groups=["menulay_kitchen_staff"], tenant_id="tenant-abc")
