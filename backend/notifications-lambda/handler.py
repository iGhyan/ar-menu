"""
notifications-lambda
Trigger  : SQS (events from orders-lambda via EventBridge → SQS)
Runtime  : Python 3.12
Memory   : 256 MB | Timeout: 60s

NOTE: No Cognito JWT auth needed here — this Lambda is triggered
internally by SQS, not by external HTTP requests. Security is
enforced at the SQS/IAM level. The payload comes from the verified
orders-lambda which already did JWT verification.
"""

import json
import logging
import os
import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── env vars ──────────────────────────────────────────────────────────────────
SNS_TOPIC            = os.environ["SNS_TOPIC"]
WS_ENDPOINT          = os.environ["WS_ENDPOINT"]
WS_CONNECTIONS_TABLE = os.environ.get("WS_CONNECTIONS_TABLE", "ConnectionTable-dev")
PINPOINT_APP_ID      = os.environ.get("PINPOINT_APP_ID", "")
PINPOINT_FROM_NUMBER = os.environ.get("PINPOINT_FROM_NUMBER", "")
TABLE_ORDER          = os.environ["TABLE_ORDER"]
REGION               = os.environ.get("AWS_REGION", "us-east-1")

# ── boto3 clients ─────────────────────────────────────────────────────────────
sns        = boto3.client("sns",        region_name=REGION)
ddb        = boto3.resource("dynamodb", region_name=REGION)
apigw_mgmt = boto3.client("apigatewaymanagementapi",
                           endpoint_url=WS_ENDPOINT, region_name=REGION)
pinpoint   = boto3.client("pinpoint",   region_name=REGION)

order_table       = ddb.Table(TABLE_ORDER)
connections_table = ddb.Table(WS_CONNECTIONS_TABLE)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def lambda_handler(event, context):
    """
    SQS batch handler.
    Each record body is either:
      - EventBridge envelope: { "detail": { orderId, tenantId, status, ... } }
      - Direct payload:       { orderId, tenantId, status, ... }

    Returns batchItemFailures so only failed records go to DLQ.
    No JWT auth needed — this is an internal SQS trigger.
    """
    batch_failures = []

    for record in event.get("Records", []):
        msg_id = record["messageId"]
        try:
            body    = json.loads(record["body"])
            payload = _extract_payload(body)

            # ── Validate required fields ────────────────────────────────────
            # tenantId must be a UUID — basic sanity check
            tenant_id = payload.get("tenantId", "")
            order_id  = payload.get("orderId", "")

            if not order_id:
                logger.error("Missing orderId in payload msgId=%s", msg_id)
                batch_failures.append({"itemIdentifier": msg_id})
                continue

            if not tenant_id or len(tenant_id) < 10:
                logger.error("Invalid tenantId=%s msgId=%s", tenant_id, msg_id)
                batch_failures.append({"itemIdentifier": msg_id})
                continue

            _process(payload)

        except Exception as exc:
            logger.error("Failed messageId=%s: %s", msg_id, exc, exc_info=True)
            batch_failures.append({"itemIdentifier": msg_id})

    return {"batchItemFailures": batch_failures}


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _extract_payload(body: dict) -> dict:
    """Unwrap EventBridge envelope if present, else return body as-is."""
    return body.get("detail", body)


def _process(payload: dict):
    order_id  = payload["orderId"]
    tenant_id = payload["tenantId"]
    status    = payload.get("status", "UPDATED")
    user_id   = payload.get("userId")
    phone     = payload.get("phone")
    email     = payload.get("email")
    message   = payload.get("message") or _default_message(status, order_id)

    logger.info("Processing orderId=%s tenant=%s status=%s", order_id, tenant_id, status)

    # 1. SNS — raises on failure → SQS retry → DLQ
    _publish_sns(order_id, tenant_id, status, message)

    # 2. WebSocket — ephemeral, failure skipped
    if user_id:
        _push_websocket(user_id, order_id, status, message)

    # 3. Pinpoint — best-effort, failure logged only
    if phone or email:
        _send_pinpoint(order_id, status, message, phone=phone, email=email)


# ── 1. SNS ────────────────────────────────────────────────────────────────────

def _publish_sns(order_id: str, tenant_id: str, status: str, message: str):
    try:
        resp = sns.publish(
            TopicArn=SNS_TOPIC,
            Message=json.dumps({
                "orderId":  order_id,
                "tenantId": tenant_id,
                "status":   status,
                "message":  message,
            }),
            Subject=f"Order {status}",
            MessageAttributes={
                "tenantId": {"DataType": "String", "StringValue": tenant_id},
                "status":   {"DataType": "String", "StringValue": status},
            },
        )
        logger.info("SNS published MessageId=%s", resp["MessageId"])
    except ClientError as exc:
        logger.error("SNS publish failed: %s", exc)
        raise


# ── 2. WebSocket ──────────────────────────────────────────────────────────────

def _push_websocket(user_id: str, order_id: str, status: str, message: str):
    connection_ids = _get_connection_ids(user_id)
    if not connection_ids:
        logger.info("No active WS connections for userId=%s", user_id)
        return

    ws_data = json.dumps({
        "type":    "ORDER_UPDATE",
        "orderId": order_id,
        "status":  status,
        "message": message,
    }).encode()

    for conn_id in connection_ids:
        try:
            apigw_mgmt.post_to_connection(ConnectionId=conn_id, Data=ws_data)
            logger.info("WS pushed connId=%s", conn_id)
        except apigw_mgmt.exceptions.GoneException:
            logger.info("Stale WS connection %s — removing", conn_id)
            _remove_connection(conn_id)
        except Exception as exc:
            logger.warning("WS push failed connId=%s: %s — skipping", conn_id, exc)


def _get_connection_ids(user_id: str) -> list:
    try:
        resp = connections_table.scan(
            FilterExpression=Attr("userId").eq(user_id)
        )
        return [item["connectionId"] for item in resp.get("Items", [])]
    except Exception as exc:
        logger.warning("DDB connection scan failed: %s", exc)
        return []


def _remove_connection(conn_id: str):
    try:
        connections_table.delete_item(Key={"connectionId": conn_id})
    except Exception as exc:
        logger.warning("Failed to remove stale connection %s: %s", conn_id, exc)


# ── 3. Pinpoint ───────────────────────────────────────────────────────────────

def _send_pinpoint(order_id: str, status: str, message: str,
                   phone: str = None, email: str = None):
    if not PINPOINT_APP_ID:
        logger.info("PINPOINT_APP_ID not set — skipping Pinpoint")
        return

    addresses = {}
    if phone:
        addresses[phone] = {"ChannelType": "SMS"}
    if email:
        addresses[email] = {"ChannelType": "EMAIL"}

    if not addresses:
        return

    msg_config = {"DefaultMessage": {"Body": message}}

    if phone and PINPOINT_FROM_NUMBER:
        msg_config["SMSMessage"] = {
            "Body":              message,
            "MessageType":       "TRANSACTIONAL",
            "OriginationNumber": PINPOINT_FROM_NUMBER,
        }
    if email:
        msg_config["EmailMessage"] = {
            "SimpleEmail": {
                "Subject": {"Data": f"Your order is {status}"},
                "HtmlPart": {"Data": f"<p>{message}</p>"},
                "TextPart": {"Data": message},
            }
        }

    try:
        resp = pinpoint.send_messages(
            ApplicationId=PINPOINT_APP_ID,
            MessageRequest={
                "Addresses":            addresses,
                "MessageConfiguration": msg_config,
            },
        )
        logger.info("Pinpoint response: %s", json.dumps(resp.get("MessageResponse", {})))
    except ClientError as exc:
        logger.error("Pinpoint send failed: %s — continuing", exc)


# ── Utility ───────────────────────────────────────────────────────────────────

def _default_message(status: str, order_id: str) -> str:
    templates = {
        "PLACED":    f"Your order #{order_id} has been placed successfully.",
        "CONFIRMED": f"Order #{order_id} confirmed — we're preparing it now.",
        "READY":     f"Order #{order_id} is ready for pickup!",
        "DELIVERED": f"Order #{order_id} has been delivered. Enjoy!",
        "CANCELLED": f"Order #{order_id} has been cancelled.",
    }
    return templates.get(status.upper(), f"Order #{order_id} status: {status}.")