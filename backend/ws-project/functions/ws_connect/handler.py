"""
functions/ws_connect/handler.py
================================
ws-connect-lambda
Trigger  : API Gateway WebSocket $connect
Memory   : 256 MB  |  Timeout: 5s

Auth: Cognito JWT — any logged-in user can connect
      (menulay_admin, menulay_tenant, menulay_kitchen_staff)

Flow
----
1. Extract token from Authorization header or ?token= query param
2. Verify Cognito JWT (expiry, issuer, client_id, token_use)
3. Store connection record in DynamoDB (TTL = 1 hour)
4. Cache connectionId → userId in Redis hash (optional, best-effort)

Env vars
--------
  REDIS_URL   — Redis connection URL (required)
  TABLE_CONN  — DynamoDB connection table name (required)
  COGNITO_REGION       — defaults to ap-south-1
  COGNITO_USER_POOL_ID — Cognito User Pool ID
  COGNITO_CLIENT_ID    — Cognito App Client ID
"""

from __future__ import annotations

import os
import sys
import time

# ── Layer path setup (for local/SAM invocation) ───────────────────────────────
sys.path.insert(0, "/opt/python")

import boto3

from shared.aws_clients import get_dynamodb_resource
from shared.cognito_auth import CognitoAuth
from shared.exceptions import TokenMissingError, AuthError
from shared.structured_logger import get_logger, bind_lambda_context

# ── Config ────────────────────────────────────────────────────────────────────
_TABLE_CONN = os.environ["TABLE_CONN"]
_REDIS_URL  = os.environ["REDIS_URL"]

_log  = get_logger("ws-connect")
_auth = CognitoAuth()

# ── DynamoDB ──────────────────────────────────────────────────────────────────
_table = get_dynamodb_resource().Table(_TABLE_CONN)

# ── Redis (optional) ──────────────────────────────────────────────────────────
try:
    import redis as _redis_lib
    _redis_client = _redis_lib.Redis.from_url(
        _REDIS_URL, decode_responses=True, socket_timeout=2
    )
    _redis_client.ping()
    _REDIS_OK = True
    _log.info("redis.connected")
except Exception as exc:
    _log.warning("redis.unavailable", cause=str(exc))
    _redis_client = None
    _REDIS_OK     = False


# ── Entry point ───────────────────────────────────────────────────────────────

def lambda_handler(event: dict, context) -> dict:
    bind_lambda_context(context)

    request_ctx   = event.get("requestContext", {})
    connection_id = request_ctx.get("connectionId", "")

    # ── 1. Extract token ──────────────────────────────────────────────────────
    headers      = event.get("headers") or {}
    query_params = event.get("queryStringParameters") or {}

    raw_token = (
        headers.get("Authorization")
        or headers.get("authorization")
        or query_params.get("token")
        or ""
    ).strip()

    if not raw_token:
        _log.info("connect.rejected.no_token", connection_id=connection_id)
        return {"statusCode": 401, "body": "Unauthorized: missing token"}

    # ── 2. Verify JWT ─────────────────────────────────────────────────────────
    try:
        user = _auth.get_user_from_event({
            "headers": {"Authorization": raw_token}
        })
    except AuthError as exc:
        _log.warning(
            "connect.rejected.invalid_token",
            connection_id=connection_id,
            error_code=exc.error_code,
            reason=exc.message,
        )
        return {"statusCode": 401, "body": f"Unauthorized: {exc.message}"}
    except Exception as exc:  # noqa: BLE001
        _log.error("connect.jwt_error", connection_id=connection_id, exc_message=str(exc))
        return {"statusCode": 401, "body": "Unauthorized: token verification failed"}

    # ── 3. Store in DynamoDB ──────────────────────────────────────────────────
    ttl = int(time.time()) + 3600
    _table.put_item(Item={
        "connectionId": connection_id,
        "userId":       user.sub,
        "email":        user.email,
        "tenantId":     user.tenant_id,
        "groups":       user.groups,
        "connectedAt":  int(time.time()),
        "ttl":          ttl,
    })

    # ── 4. Cache in Redis (best-effort) ───────────────────────────────────────
    if _REDIS_OK and _redis_client:
        try:
            _redis_client.hset("connections", connection_id, user.sub)
        except Exception as exc:  # noqa: BLE001
            _log.warning("redis.hset.failed", connection_id=connection_id, exc_message=str(exc))

    _log.info(
        "ws.connected",
        connection_id=connection_id,
        user_id=user.sub,
        tenant_id=user.tenant_id,
        groups=user.groups,
    )
    return {"statusCode": 200, "body": "Connected"}
