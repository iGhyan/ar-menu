"""
orders_service/routes/order_routes.py
======================================
Route handler functions — pure business logic, no AWS boilerplate.

Each function receives injected services so they're fully unit-testable.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from botocore.exceptions import ClientError
from pydantic import ValidationError

from orders_service.models import OrderRecord, OrderRequest, OrderStatusUpdate, clean_decimals
from orders_service.menu_validator import MenuValidationError, validate_menu_items
from orders_service.repositories.order_repository import DuplicateOrderError, OrderRepository
from orders_service.channels.cart_service import CartService
from orders_service.channels.sfn_service import StepFunctionsService
from shared.exceptions import ResourceNotFoundError, BadRequestError, InvalidJsonError
from shared.response_builder import ResponseBuilder
from shared.structured_logger import get_logger

_log = get_logger("orders.routes")

_CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,OPTIONS",
}


# ── POST /orders ──────────────────────────────────────────────────────────────

def handle_post_order(
    event:       dict,
    context:     Any,
    repo:        OrderRepository,
    cart:        CartService,
    sfn:         StepFunctionsService,
    ddb_client:  Any,
    menu_table:  str,
    skip_menu:   bool = False,
) -> dict:
    """Create a new order: validate → write DDB → clear cart → start SFN."""
    order_id = str(uuid.uuid4())

    # Parse + validate request body
    try:
        body    = json.loads(event.get("body") or "{}")
        request = OrderRequest(**body)
    except (json.JSONDecodeError, TypeError):
        return ResponseBuilder.error(
            "INVALID_JSON", "Request body is not valid JSON.", status=400, headers=_CORS_HEADERS
        )
    except ValidationError as exc:
        return ResponseBuilder.error(
            "VALIDATION_ERROR", str(exc), status=400, headers=_CORS_HEADERS
        )

    # Menu validation
    if not skip_menu:
        try:
            validate_menu_items(ddb_client, menu_table, request.restaurantId, request.lineItems)
        except MenuValidationError as exc:
            return ResponseBuilder.error(
                exc.error_code, exc.message, status=422, headers=_CORS_HEADERS
            )
        except ClientError:
            return ResponseBuilder.error(
                "MENU_SERVICE_UNAVAILABLE", "Menu service unavailable.", status=503,
                headers=_CORS_HEADERS,
            )

    # Build + write record
    now    = datetime.now(timezone.utc)
    record = OrderRecord.build(request, order_id, execution_arn="PENDING", now=now)

    try:
        repo.write_order(record)
    except DuplicateOrderError:
        return ResponseBuilder.error(
            "DUPLICATE_ORDER", "Order already exists.", status=409, headers=_CORS_HEADERS
        )
    except ClientError:
        return ResponseBuilder.error(
            "ORDER_STORAGE_UNAVAILABLE", "Order storage unavailable.", status=503,
            headers=_CORS_HEADERS,
        )

    # Clear cart (best-effort)
    cart.clear_cart(request.tenantId, request.tableId)

    # Start Step Functions
    try:
        execution_arn = sfn.start_new_order(order_id, request)
    except ClientError as exc:
        repo.rollback_order(record)
        return ResponseBuilder.error(
            "STEP_FUNCTIONS_UNAVAILABLE",
            f"Step Functions unavailable: {exc}",
            status=503,
            headers=_CORS_HEADERS,
        )

    _log.info(
        "order.placed",
        order_id=order_id,
        tenant_id=request.tenantId,
        restaurant_id=request.restaurantId,
    )

    return ResponseBuilder.success(
        data={
            "orderId":                    order_id,
            "status":                     "RECEIVED",
            "stepFunctionsExecutionArn":  execution_arn,
        },
        status=201,
        headers=_CORS_HEADERS,
    )


# ── GET /orders/{id} ──────────────────────────────────────────────────────────

def handle_get_order(event: dict, repo: OrderRepository) -> dict:
    """Fetch a single order by orderId + tenantId."""
    order_id  = (event.get("pathParameters") or {}).get("id")
    params    = event.get("queryStringParameters") or {}
    tenant_id = params.get("tenantId")

    if not order_id or not tenant_id:
        return ResponseBuilder.error(
            "MISSING_PARAMETERS",
            "orderId in path and tenantId query param are required.",
            status=400,
            headers=_CORS_HEADERS,
        )

    order = repo.get_order(order_id, tenant_id)
    if not order:
        return ResponseBuilder.error(
            "ORDER_NOT_FOUND", f"Order {order_id!r} not found.", status=404,
            headers=_CORS_HEADERS,
        )

    return ResponseBuilder.success(
        data={"order": clean_decimals(dict(order)), "sfnStatus": None},
        headers=_CORS_HEADERS,
    )


# ── GET /orders ───────────────────────────────────────────────────────────────

def handle_list_orders(event: dict, repo: OrderRepository) -> dict:
    """List recent orders for a restaurant."""
    params        = event.get("queryStringParameters") or {}
    tenant_id     = params.get("tenantId")
    restaurant_id = params.get("restaurantId")

    if not tenant_id or not restaurant_id:
        return ResponseBuilder.error(
            "MISSING_PARAMETERS",
            "tenantId and restaurantId query params are required.",
            status=400,
            headers=_CORS_HEADERS,
        )

    hours = int(params.get("hours", 4))

    try:
        orders = repo.list_orders(restaurant_id, tenant_id, hours=hours)
    except ClientError:
        return ResponseBuilder.error(
            "ORDER_FETCH_FAILED", "Failed to fetch orders.", status=503,
            headers=_CORS_HEADERS,
        )

    return ResponseBuilder.success(
        data={"orders": clean_decimals(orders), "count": len(orders)},
        headers=_CORS_HEADERS,
    )


# ── PATCH /orders/{id} ────────────────────────────────────────────────────────

def handle_patch_order(
    event: dict,
    repo:  OrderRepository,
    sfn:   StepFunctionsService,
) -> dict:
    """Update order status flags → update DDB immediately → trigger SFN for notifications."""
    order_id = (event.get("pathParameters") or {}).get("id")
    if not order_id:
        return ResponseBuilder.error(
            "MISSING_PARAMETERS", "orderId required in path.", status=400,
            headers=_CORS_HEADERS,
        )

    # Parse body
    try:
        body   = json.loads(event.get("body") or "{}")
        update = OrderStatusUpdate(**body)
    except (json.JSONDecodeError, TypeError):
        return ResponseBuilder.error(
            "INVALID_JSON", "Request body is not valid JSON.", status=400,
            headers=_CORS_HEADERS,
        )
    except ValidationError as exc:
        return ResponseBuilder.error(
            "VALIDATION_ERROR", str(exc), status=400, headers=_CORS_HEADERS
        )

    # Fetch existing order
    order = repo.get_order(order_id, update.tenantId)
    if not order:
        return ResponseBuilder.error(
            "ORDER_NOT_FOUND", f"Order {order_id!r} not found.", status=404,
            headers=_CORS_HEADERS,
        )

    new_status = update.derived_status

    # Step 1: Update DynamoDB immediately (critical)
    try:
        repo.update_status(order_id, update.tenantId, new_status)
        _log.info("order.status.immediate_update", order_id=order_id, status=new_status)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "order.status.immediate_update.failed",
            order_id=order_id,
            exc_message=str(exc),
        )

    # Step 2: Start SFN for notifications (non-fatal)
    exec_name     = f"{order_id}-{int(datetime.now(timezone.utc).timestamp())}"
    execution_arn = sfn.start_status_update(order_id, order, update, exec_name)

    data = {
        "orderId": order_id,
        "status":  new_status,
        "flags": {
            "kitchenAccepted": update.kitchenAccepted,
            "foodReady":       update.foodReady,
            "delivered":       update.delivered,
            "cancelled":       update.cancelled,
        },
    }
    if execution_arn:
        data["executionArn"] = execution_arn

    return ResponseBuilder.success(data=data, headers=_CORS_HEADERS)


# ── Step Functions callback ───────────────────────────────────────────────────

def handle_sfn_event(event: dict, repo: OrderRepository) -> dict:
    """Called by Step Functions state machine to update order status."""
    repo.update_status(event["orderId"], event["tenantId"], event["status"])
    return event
