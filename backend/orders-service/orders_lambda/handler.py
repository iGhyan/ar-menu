from __future__ import annotations
import json, logging, os, uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key
from pydantic import ValidationError

from cart import clear_cart
from dynamo import DuplicateOrderError, write_order, rollback_order
from models import OrderRecord, OrderRequest
from validator import ValidationError as MenuValidationError, validate_menu_items

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

_ddb   = boto3.resource("dynamodb")
_ddb_c = boto3.client("dynamodb")
_sfn   = boto3.client("stepfunctions")

TABLE_ORDER = os.environ["TABLE_ORDER"]
TABLE_MENU  = os.environ["TABLE_MENU"]
STEP_ARN    = os.environ["STEP_ARN"]
SKIP_MENU   = os.environ.get("SKIP_MENU", "false").lower() == "true"


def _r(status: int, body) -> dict:
    return {
        "statusCode": status,
        "headers":    {"Content-Type": "application/json",
                       "Access-Control-Allow-Origin": "*"},
        "body":       json.dumps(body),
    }

def _clean(obj):
    if isinstance(obj, list):    return [_clean(i) for i in obj]
    if isinstance(obj, dict):    return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, Decimal): return int(obj)
    return obj

def _get_order(order_id: str, tenant_id: str) -> dict | None:
    pk  = f"TENANT#{tenant_id}#ORDER#{order_id}"
    res = _ddb.Table(TABLE_ORDER).query(
        KeyConditionExpression=Key("PK").eq(pk), Limit=1)
    items = res.get("Items", [])
    return items[0] if items else None

def _update_status(order_id: str, tenant_id: str, status: str) -> None:
    order = _get_order(order_id, tenant_id)
    if not order:
        raise ValueError(f"Order not found: {order_id}")
    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _ddb.Table(TABLE_ORDER).update_item(
        Key={"PK": order["PK"], "SK": order["SK"]},
        UpdateExpression="SET #st = :s, updatedAt = :u",
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={":s": status, ":u": updated_at},
    )


def handle_sfn_event(event: dict) -> dict:
    _update_status(event["orderId"], event["tenantId"], event["status"])
    return event


# ── POST /orders — PUBLIC ─────────────────────────────────────────────────────
def post_order(event: dict, context) -> dict:
    order_id = str(uuid.uuid4())
    try:
        body    = json.loads(event.get("body") or "{}")
        request = OrderRequest(**body)
    except (json.JSONDecodeError, ValidationError) as e:
        return _r(400, {"error": "Invalid request", "detail": str(e)})

    if not SKIP_MENU:
        try:
            validate_menu_items(_ddb_c, TABLE_MENU, request.restaurantId, request.lineItems)
        except MenuValidationError as e:
            return _r(422, {"error": "Menu validation failed", "detail": str(e)})
        except ClientError:
            return _r(503, {"error": "Menu service unavailable"})

    now    = datetime.now(timezone.utc)
    record = OrderRecord.build(request, order_id, execution_arn="PENDING", now=now)

    try:
        write_order(_ddb, TABLE_ORDER, record)
    except DuplicateOrderError:
        return _r(409, {"error": "Duplicate order"})
    except ClientError:
        return _r(503, {"error": "Order storage unavailable"})

    clear_cart(request.tenantId, request.tableId)

    sfn_input = {
        "orderId":               order_id,
        "tenantId":              request.tenantId,
        "restaurantId":          request.restaurantId,
        "tableId":               request.tableId,
        "totalAmountMinorUnits": request.totalAmountMinorUnits,
        "currencyCode":          request.currencyCode,
        "lineItems":             [i.dict() for i in request.lineItems],
        "guestConnectionId":     request.guestConnectionId,
        "kitchenAccepted":       False,
        "foodReady":             False,
        "delivered":             False,
        "cancelled":             False,
    }

    try:
        res = _sfn.start_execution(
            stateMachineArn=STEP_ARN,
            name=order_id,
            input=json.dumps(sfn_input),
        )
    except ClientError as e:
        rollback_order(_ddb, TABLE_ORDER, record)
        return _r(503, {"error": "Step Functions unavailable", "detail": str(e)})

    return _r(201, {
        "orderId":      order_id,
        "status":       "RECEIVED",
        "executionArn": res["executionArn"],
    })


# ── PATCH /orders/{id} — PUBLIC ───────────────────────────────────────────────
def patch_order(event: dict) -> dict:
    order_id = (event.get("pathParameters") or {}).get("id")
    if not order_id:
        return _r(400, {"error": "orderId required in path"})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _r(400, {"error": "Invalid JSON"})

    tenant_id = body.get("tenantId")
    if not tenant_id:
        return _r(400, {"error": "tenantId required in body"})

    order = _get_order(order_id, tenant_id)
    if not order:
        return _r(404, {"error": f"Order {order_id} not found"})

    sfn_input = {
        "orderId":               order_id,
        "tenantId":              tenant_id,
        "restaurantId":          order.get("restaurantId", ""),
        "tableId":               order.get("tableId", ""),
        "totalAmountMinorUnits": int(order.get("totalAmountMinorUnits", 0)),
        "currencyCode":          order.get("currencyCode", "PKR"),
        "lineItems":             _clean(order.get("lineItems", [])),
        "guestConnectionId":     order.get("guestConnectionId"),
        "kitchenAccepted":       body.get("kitchenAccepted", False),
        "foodReady":             body.get("foodReady", False),
        "delivered":             body.get("delivered", False),
        "cancelled":             body.get("cancelled", False),
    }

    exec_name = f"{order_id}-{int(datetime.now(timezone.utc).timestamp())}"

    try:
        res = _sfn.start_execution(
            stateMachineArn=STEP_ARN,
            name=exec_name,
            input=json.dumps(sfn_input),
        )
        return _r(200, {
            "orderId":      order_id,
            "executionArn": res["executionArn"],
            "flags":        {k: sfn_input[k] for k in
                            ["kitchenAccepted","foodReady","delivered","cancelled"]},
        })
    except ClientError as e:
        return _r(503, {"error": str(e)})


# ── GET /orders/{id} ──────────────────────────────────────────────────────────
def get_order_by_id(event: dict) -> dict:
    order_id  = (event.get("pathParameters") or {}).get("id")
    params    = event.get("queryStringParameters") or {}
    tenant_id = params.get("tenantId")

    if not order_id or not tenant_id:
        return _r(400, {"error": "orderId in path and tenantId query param required"})

    order = _get_order(order_id, tenant_id)
    if not order:
        return _r(404, {"error": f"Order {order_id} not found"})

    return _r(200, {"order": _clean(dict(order)), "sfnStatus": None})


# ── GET /orders ───────────────────────────────────────────────────────────────
def get_orders(event: dict) -> dict:
    params        = event.get("queryStringParameters") or {}
    tenant_id     = params.get("tenantId")
    restaurant_id = params.get("restaurantId")

    if not tenant_id or not restaurant_id:
        return _r(400, {"error": "tenantId and restaurantId required"})

    hours     = int(params.get("hours", 4))
    from_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        res = _ddb.Table(TABLE_ORDER).query(
            IndexName="GSI-1-restaurant-orders",
            KeyConditionExpression=Key("restaurantId").eq(restaurant_id) & Key("placedAt").gte(from_time),
        )
    except ClientError:
        return _r(503, {"error": "Failed to fetch orders"})

    orders = [o for o in res.get("Items", []) if o.get("tenantId") == tenant_id]
    return _r(200, {"orders": _clean(orders), "count": len(orders)})


# ── Router ────────────────────────────────────────────────────────────────────
def lambda_handler(event: dict, context) -> dict:

    if event.get("source") == "stepfunctions":
        return handle_sfn_event(event)

    method = event.get("httpMethod", "")
    path   = event.get("path", "").rstrip("/")

    logger.info("REQUEST method=%s path=%s", method, path)

    if method == "GET" and path.endswith("/orders"):
        return get_orders(event)

    if method == "GET" and "/orders/" in path:
        return get_order_by_id(event)

    if method == "POST" and path.endswith("/orders"):
        return post_order(event, context)

    if method == "PATCH" and "/orders/" in path:
        return patch_order(event)

    return _r(405, {"error": f"{method} {path} not allowed"})