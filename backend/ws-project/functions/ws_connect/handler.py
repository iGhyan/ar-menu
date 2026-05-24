"""
ws-connect-lambda
Trigger  : API Gateway WebSocket $connect
Memory   : 256 MB  |  Timeout : 5s
Env Vars : REDIS_URL, TABLE_CONN

Auth: Cognito JWT (RS256) — replaces old HS256 JWT_SECRET approach
Any logged-in user can connect (admin, tenant, kitchen_staff)
"""

import os
import time
import json
import base64
import logging
import urllib.request
from typing import Optional, Dict, Any

import boto3
import redis

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REDIS_URL  = os.environ["REDIS_URL"]
TABLE_CONN = os.environ["TABLE_CONN"]

# ── Cognito config ─────────────────────────────────────────────────────────────
REGION       = 'ap-south-1'
USER_POOL_ID = 'ap-south-1_SCyQ50etN'
CLIENT_ID    = '7903hkujl9qeq67toemi5qrhes'

JWKS_URL = (
    f'https://cognito-idp.{REGION}.amazonaws.com/'
    f'{USER_POOL_ID}/.well-known/jwks.json'
)

# ── AWS clients ────────────────────────────────────────────────────────────────
dynamodb = boto3.resource("dynamodb")
table    = dynamodb.Table(TABLE_CONN)

# ── Redis optional ─────────────────────────────────────────────────────────────
try:
    redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=2)
    redis_client.ping()
    REDIS_AVAILABLE = True
    logger.info("Redis connected")
except Exception as e:
    logger.warning("Redis not available, skipping (non-critical): %s", e)
    redis_client = None
    REDIS_AVAILABLE = False

# ── JWKS cache ─────────────────────────────────────────────────────────────────
_jwks_cache: Optional[Dict] = None
_jwks_cache_time: float = 0
JWKS_CACHE_TTL = 3600


def _get_jwks() -> Dict:
    global _jwks_cache, _jwks_cache_time
    now = time.time()
    if _jwks_cache and (now - _jwks_cache_time) < JWKS_CACHE_TTL:
        return _jwks_cache
    with urllib.request.urlopen(JWKS_URL, timeout=5) as res:
        _jwks_cache = json.loads(res.read())
        _jwks_cache_time = now
        logger.info("JWKS refreshed")
    return _jwks_cache


def _decode_payload(token: str) -> Dict:
    """Base64 decode JWT payload — for claims only."""
    parts = token.split('.')
    if len(parts) != 3:
        raise ValueError('Invalid JWT format')
    payload = parts[1]
    payload += '=' * (4 - len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def _verify_cognito_token(token: str) -> Dict[str, Any]:
    """
    Verify Cognito JWT token.
    Returns claims dict if valid, raises ValueError if not.
    """
    if token.startswith('Bearer '):
        token = token[7:]

    claims = _decode_payload(token)

    # Check expiry
    if claims.get('exp', 0) < time.time():
        raise ValueError('Token has expired')

    # Check issuer
    expected_iss = f'https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}'
    if claims.get('iss') != expected_iss:
        raise ValueError('Invalid token issuer')

    # Check client
    token_client = claims.get('client_id') or claims.get('aud')
    if token_client != CLIENT_ID:
        raise ValueError('Invalid token client')

    # Check token_use
    if claims.get('token_use') not in ('id', 'access'):
        raise ValueError('Invalid token_use')

    return claims


def lambda_handler(event: dict, context) -> dict:
    request_ctx   = event.get("requestContext", {})
    connection_id = request_ctx.get("connectionId", "")
    headers       = event.get("headers") or {}

    # ── 1. Token extract ──────────────────────────────────────────────────────
    # WebSocket connect mein token query string ya header se aata hai
    query_params = event.get("queryStringParameters") or {}
    auth_header  = (
        headers.get("Authorization") or
        headers.get("authorization") or
        query_params.get("token") or  # ws://url?token=xxx
        ""
    )
    token = auth_header.replace("Bearer ", "").strip()

    if not token:
        logger.info("No token — rejecting $connect for %s", connection_id)
        return {"statusCode": 401, "body": "Unauthorized: missing token"}

    # ── 2. Cognito JWT verify ─────────────────────────────────────────────────
    try:
        claims = _verify_cognito_token(token)
    except ValueError as e:
        logger.warning("Invalid JWT — rejecting $connect for %s: %s", connection_id, str(e))
        return {"statusCode": 401, "body": f"Unauthorized: {str(e)}"}
    except Exception as e:
        logger.error("JWT verify error for %s: %s", connection_id, str(e))
        return {"statusCode": 401, "body": "Unauthorized: token verification failed"}

    user_id   = claims.get("sub", "unknown")
    email     = claims.get("email", "")
    tenant_id = claims.get("custom:tenant_id", "")
    groups    = claims.get("cognito:groups", [])

    logger.info(
        "JWT valid — connectionId=%s userId=%s email=%s groups=%s",
        connection_id, user_id, email, groups
    )

    # ── 3. DynamoDB — store connection ────────────────────────────────────────
    ttl = int(time.time()) + 3600
    table.put_item(Item={
        "connectionId": connection_id,
        "userId":       user_id,
        "email":        email,
        "tenantId":     tenant_id,
        "groups":       groups,
        "connectedAt":  int(time.time()),
        "ttl":          ttl,
    })

    # ── 4. Redis HSET — optional ──────────────────────────────────────────────
    if REDIS_AVAILABLE and redis_client:
        try:
            redis_client.hset("connections", connection_id, user_id)
        except Exception as e:
            logger.warning("Redis HSET failed (non-critical): %s", e)

    logger.info("Connected: connectionId=%s userId=%s tenantId=%s",
                connection_id, user_id, tenant_id)
    return {"statusCode": 200, "body": "Connected"}