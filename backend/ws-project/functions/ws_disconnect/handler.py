"""
functions/ws_disconnect/handler.py
====================================
ws-disconnect-lambda
Trigger  : API Gateway WebSocket $disconnect
Memory   : 256 MB  |  Timeout: 5s

Flow
----
1. Remove connectionId from Redis hash (best-effort, non-critical)
2. Remove connection record from DynamoDB (best-effort)
3. Always return 200 — disconnect must always succeed

Env vars
--------
  REDIS_URL   — Redis connection URL (required)
  TABLE_CONN  — DynamoDB connection table name (required)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "/opt/python")

from shared.aws_clients import get_dynamodb_resource
from shared.structured_logger import get_logger, bind_lambda_context

_TABLE_CONN = os.environ["REDIS_URL"]   # kept for Redis
_REDIS_URL  = os.environ["REDIS_URL"]
_TABLE_CONN = os.environ.get("TABLE_CONN", "")

_log   = get_logger("ws-disconnect")

# ── Redis (module-level — reused on warm starts) ──────────────────────────────
try:
    import redis as _redis_lib
    _redis_client = _redis_lib.Redis.from_url(
        _REDIS_URL, decode_responses=True, socket_timeout=2
    )
    _redis_client.ping()
    _REDIS_OK = True
except Exception as exc:
    _log.warning("redis.unavailable", cause=str(exc))
    _redis_client = None
    _REDIS_OK     = False

# ── DynamoDB (optional — only if TABLE_CONN configured) ───────────────────────
_table = get_dynamodb_resource().Table(_TABLE_CONN) if _TABLE_CONN else None


# ── Entry point ───────────────────────────────────────────────────────────────

def lambda_handler(event: dict, context) -> dict:
    bind_lambda_context(context)

    connection_id = (event.get("requestContext") or {}).get("connectionId", "")

    # ── 1. Remove from Redis (non-critical) ───────────────────────────────────
    if _REDIS_OK and _redis_client:
        try:
            deleted = _redis_client.hdel("connections", connection_id)
            if deleted:
                _log.info("redis.connection.removed", connection_id=connection_id)
            else:
                _log.info("redis.connection.already_gone", connection_id=connection_id)
        except Exception as exc:  # noqa: BLE001
            _log.error(
                "redis.hdel.failed",
                connection_id=connection_id,
                exc_message=str(exc),
            )

    # ── 2. Remove from DynamoDB (non-critical) ────────────────────────────────
    if _table and connection_id:
        try:
            _table.delete_item(Key={"connectionId": connection_id})
            _log.info("ddb.connection.removed", connection_id=connection_id)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "ddb.connection.remove.failed",
                connection_id=connection_id,
                exc_message=str(exc),
            )

    _log.info("ws.disconnected", connection_id=connection_id)
    return {"statusCode": 200, "body": "Disconnected"}
