"""
functions/ws_message/handler.py
=================================
ws-message-lambda
Trigger  : API Gateway WebSocket $default
Memory   : 512 MB  |  Timeout: 10s

Flow
----
1. Parse + validate incoming WebSocket message body
2. Save order record to DynamoDB
3. If taskToken present → send SFN SendTaskSuccess
   - On SFN failure → send original message to DLQ (non-critical for client)
4. Always return 200 to client (client's job is done after sending)

Env vars
--------
  TABLE_ORDER  — DynamoDB order table name (required)
  STEP_ARN     — Step Functions state machine ARN (required)
  DLQ_URL      — SQS Dead Letter Queue URL (required)
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid

sys.path.insert(0, "/opt/python")

import boto3
from botocore.exceptions import ClientError

from shared.aws_clients import get_dynamodb_resource, get_sqs_client
from shared.exceptions import BadRequestError, InvalidJsonError
from shared.structured_logger import get_logger, bind_lambda_context

# ── Config ────────────────────────────────────────────────────────────────────
_TABLE_ORDER = os.environ["TABLE_ORDER"]
_STEP_ARN    = os.environ["STEP_ARN"]
_DLQ_URL     = os.environ["DLQ_URL"]

_log   = get_logger("ws-message")
_table = get_dynamodb_resource().Table(_TABLE_ORDER)
_sfn   = boto3.client("stepfunctions")
_sqs   = get_sqs_client()

VALID_STATUSES = frozenset({"pending", "confirmed", "processing", "cancelled", "delivered"})


# ── Entry point ───────────────────────────────────────────────────────────────

def lambda_handler(event: dict, context) -> dict:
    bind_lambda_context(context)

    connection_id = (event.get("requestContext") or {}).get("connectionId", "")

    # ── 1. Parse body ─────────────────────────────────────────────────────────
    try:
        body = json.loads(event.get("body") or "{}")
    except (json.JSONDecodeError, TypeError):
        _log.warning("message.invalid_json", connection_id=connection_id)
        return {"statusCode": 400, "body": "Invalid JSON"}

    status     = body.get("status", "").lower()
    task_token = body.get("taskToken", "")
    order_id   = body.get("orderId") or str(uuid.uuid4())

    # ── 2. Validate status ────────────────────────────────────────────────────
    if status not in VALID_STATUSES:
        _log.warning(
            "message.invalid_status",
            connection_id=connection_id,
            status=status,
        )
        return {
            "statusCode": 400,
            "body": f"Invalid status. Allowed: {sorted(VALID_STATUSES)}",
        }

    # ── 3. Save to DynamoDB ───────────────────────────────────────────────────
    _table.put_item(Item={
        "connectionId": connection_id,
        "orderId":      order_id,
        "status":       status,
        "payload":      body,
        "updatedAt":    int(time.time()),
    })
    _log.info(
        "order.saved",
        connection_id=connection_id,
        order_id=order_id,
        status=status,
    )

    # ── 4. Step Functions SendTaskSuccess ─────────────────────────────────────
    if task_token:
        _send_task_success(connection_id, order_id, status, task_token, body)
    else:
        _log.info("sfn.skipped.no_task_token", order_id=order_id)

    return {
        "statusCode": 200,
        "body": json.dumps({"orderId": order_id, "status": status}),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _send_task_success(
    connection_id: str,
    order_id:      str,
    status:        str,
    task_token:    str,
    original_body: dict,
) -> None:
    """Send SFN task success. On failure, route to DLQ (non-fatal for client)."""
    try:
        _sfn.send_task_success(
            taskToken=task_token,
            output=json.dumps({
                "connectionId": connection_id,
                "orderId":      order_id,
                "status":       status,
            }),
        )
        _log.info("sfn.task_success.sent", order_id=order_id)
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        _log.error(
            "sfn.task_success.failed",
            order_id=order_id,
            error_code=error_code,
            exc_message=str(exc),
        )
        _send_to_dlq(connection_id, original_body, reason=f"SFN error: {error_code}")


def _send_to_dlq(connection_id: str, body: dict, reason: str) -> None:
    """Route failed message to Dead Letter Queue."""
    try:
        _sqs.send_message(
            QueueUrl=_DLQ_URL,
            MessageBody=json.dumps({
                "connectionId":  connection_id,
                "originalBody":  body,
                "failureReason": reason,
                "timestamp":     int(time.time()),
            }),
            MessageGroupId=connection_id,  # FIFO ordering per connection
        )
        _log.info("dlq.message.sent", connection_id=connection_id, reason=reason)
    except ClientError as exc:
        _log.critical(
            "dlq.send.failed",
            connection_id=connection_id,
            exc_message=str(exc),
        )
