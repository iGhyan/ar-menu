"""
tests/conftest.py
==================
Shared fixtures for menu-lambda tests.
Uses moto for DynamoDB + S3, fakeredis for cache, make_jwt for auth.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from unittest.mock import MagicMock

import pytest

# ── sys.path: src first, then layer ───────────────────────────────────────────
_ROOT  = os.path.join(os.path.dirname(__file__), "..")
_SRC   = os.path.join(_ROOT, "src")
_LAYER = os.path.join(_ROOT, "layer", "python")
for p in [_LAYER, _SRC]:
    if p not in sys.path:
        sys.path.insert(0, p)

# ── Env vars BEFORE any imports ───────────────────────────────────────────────
os.environ.update({
    "MENU_TABLE":            "MenuTable-test",
    "TENANT_TABLE":          "TenantTable-test",
    "S3_BUCKET":             "menu-assets-test",
    "REDIS_HOST":            "localhost",
    "REDIS_PORT":            "6379",
    "REDIS_TIMEOUT":         "1",
    "CACHE_TTL_SECONDS":     "300",
    "MAX_IMAGE_MB":          "5",
    "MAX_AR_MB":             "50",
    "LOG_LEVEL":             "WARNING",
    "AWS_DEFAULT_REGION":    "us-east-1",
    "AWS_ACCESS_KEY_ID":     "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "TABLE_RESTAURANT_TABLES": "RestaurantTables-test",
    "COGNITO_REGION":        "ap-south-1",
    "COGNITO_USER_POOL_ID":  "ap-south-1_SCyQ50etN",
    "COGNITO_CLIENT_ID":     "7903hkujl9qeq67toemi5qrhes",
})


@pytest.fixture(autouse=True)
def clear_aws_cache():
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
    email:     str  = "admin@example.com",
    tenant_id: str  = "tenant-abc-def-123",
    groups:    list = None,
    expired:   bool = False,
    issuer:    str  = None,
    client_id: str  = None,
) -> str:
    """Build unsigned test JWT."""
    if groups is None:
        groups = ["menulay_admin"]
    exp = int(time.time()) + (-100 if expired else 3600)
    iss = issuer or "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_SCyQ50etN"
    cid = client_id or "7903hkujl9qeq67toemi5qrhes"
    header  = _b64({"alg": "RS256", "typ": "JWT"})
    payload = _b64({
        "sub": sub, "email": email, "custom:tenant_id": tenant_id,
        "cognito:groups": groups, "exp": exp, "iss": iss,
        "client_id": cid, "token_use": "access",
    })
    return f"{header}.{payload}.fake-sig"


# ── API Gateway event builders ────────────────────────────────────────────────

def api_event(
    method:      str,
    path:        str,
    body:        dict | None = None,
    path_params: dict | None = None,
    query:       dict | None = None,
    tenant_id:   str  | None = None,
    token:       str  | None = None,
) -> dict:
    headers = {}
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return {
        "httpMethod":            method.upper(),
        "path":                  path,
        "pathParameters":        path_params or {},
        "queryStringParameters": query or {},
        "headers":               headers,
        "body":                  json.dumps(body) if body else None,
        "isBase64Encoded":       False,
    }


# ── Mock service/context fixtures ─────────────────────────────────────────────

@pytest.fixture
def mock_context():
    ctx = MagicMock()
    ctx.aws_request_id   = "test-menu-req"
    ctx.function_name    = "menu-lambda"
    ctx.function_version = "$LATEST"
    return ctx


@pytest.fixture
def admin_token():
    return make_jwt(groups=["menulay_admin"], tenant_id="tenant-abc-def-123")


@pytest.fixture
def tenant_token():
    return make_jwt(groups=["menulay_tenant"], tenant_id="tenant-abc-def-123")


@pytest.fixture
def mock_cache():
    """CacheService that always returns None (cache miss) — forces DDB path."""
    cache = MagicMock()
    cache.get.return_value = None
    cache.set.return_value = None
    cache.delete.return_value = None
    cache.get_or_load.side_effect = lambda key, loader, ttl=300: loader()
    return cache


# ── Common test data ──────────────────────────────────────────────────────────

TENANT_ID     = "tenant-abc-def-123"
RESTAURANT_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
CATEGORY_ID   = "b2c3d4e5-f6a7-8901-bcde-f12345678901"
ITEM_ID       = "c3d4e5f6-a7b8-9012-cdef-012345678901"

RESTAURANT_BODY = {
    "name":         "Test Restaurant",
    "address": {
        "street":   "123 Main St",
        "city":     "Lahore",
        "country":  "PK",
        "postcode": "54000",
    },
    "timezone":     "Asia/Karachi",
    "currencyCode": "PKR",
    "isActive":     True,
}

CATEGORY_BODY = {
    "name":         "Burgers",
    "displayOrder": 1,
    "isActive":     True,
}

ITEM_BODY = {
    "categoryId":      CATEGORY_ID,
    "name":            "Chicken Burger",
    "description":     "Crispy chicken burger",
    "priceMinorUnits": 1200,
    "isActive":        True,
    "allergens":       ["GLUTEN"],
}
