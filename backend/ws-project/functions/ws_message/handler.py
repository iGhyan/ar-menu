"""
ws-message-lambda
Trigger  : API Gateway WebSocket $default
Memory   : 512 MB  |  Timeout : 10s
Env Vars : TABLE_ORDER, STEP_ARN, DLQ_URL
"""

import os
import json
import uuid
import time
import logging
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Cold-start initialisation ──
TABLE_ORDER = os.environ["TABLE_ORDER"]
STEP_ARN    = os.environ["STEP_ARN"]
DLQ_URL     = os.environ["DLQ_URL"]

VALID_STATUSES = {"pending", "confirmed", "processing", "cancelled", "delivered"}

dynamodb = boto3.resource("dynamodb")
table    = dynamodb.Table(TABLE_ORDER)
sfn      = boto3.client("stepfunctions")
sqs      = boto3.client("sqs")


def _send_to_dlq(connection_id: str, body: dict, reason: str) -> None:
    """Failed message ko Dead Letter Queue mein bhejo."""
    try:
        sqs.send_message(
            QueueUrl=DLQ_URL,
            MessageBody=json.dumps({
                "connectionId": connection_id,
                "originalBody": body,
                "failureReason": reason,
                "timestamp":    int(time.time()),
            }),
            MessageGroupId=connection_id,   # FIFO queue ho to ordering maintain ho
        )
        logger.info("Sent to DLQ: connectionId=%s reason=%s", connection_id, reason)
    except ClientError as e:
        # DLQ bhi fail ho gaya — yahan sirf log kar sakte hain
        logger.critical("DLQ send FAILED for %s: %s", connection_id, e)


def lambda_handler(event: dict, context) -> dict:
    request_ctx   = event.get("requestContext", {})
    connection_id = request_ctx.get("connectionId", "")

    # ── 1. Body parse karo ────────────────────────────────
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        logger.warning("Invalid JSON body from %s", connection_id)
        return {"statusCode": 400, "body": "Invalid JSON"}

    status     = body.get("status", "").lower()
    task_token = body.get("taskToken", "")
    order_id   = body.get("orderId") or str(uuid.uuid4())

    # ── 2. Status validate karo ───────────────────────────
    if status not in VALID_STATUSES:
        logger.warning("Invalid status '%s' from %s", status, connection_id)
        return {
            "statusCode": 400,
            "body": f"Invalid status. Allowed: {sorted(VALID_STATUSES)}",
        }

    # ── 3. DynamoDB mein order save karo ─────────────────
    table.put_item(Item={
        "connectionId": connection_id,
        "orderId":      order_id,
        "status":       status,
        "payload":      body,
        "updatedAt":    int(time.time()),
    })
    logger.info("Order saved: connectionId=%s orderId=%s status=%s",
                connection_id, order_id, status)

    # ── 4. Step Functions SendTaskSuccess ─────────────────
    if task_token:
        try:
            sfn.send_task_success(
                taskToken=task_token,
                output=json.dumps({
                    "connectionId": connection_id,
                    "orderId":      order_id,
                    "status":       status,
                }),
            )
            logger.info("SFN SendTaskSuccess: orderId=%s", order_id)
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            logger.error("SFN SendTaskSuccess FAILED (orderId=%s): %s %s",
                         order_id, error_code, e)
            # SFN fail → DLQ
            _send_to_dlq(connection_id, body, reason=f"SFN error: {error_code}")
            # 200 return karo — client ka kaam ho gaya, orchestration side ka issue hai
    else:
        logger.info("No taskToken in body — skipping SFN call (orderId=%s)", order_id)

    return {"statusCode": 200, "body": json.dumps({"orderId": order_id, "status": status})}
