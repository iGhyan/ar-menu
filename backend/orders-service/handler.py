"""
handler.py
==========
orders-service Lambda — thin router and dependency injector.

Routes
------
POST   /orders          → create new order
GET    /orders          → list orders (by restaurantId, last N hours)
GET    /orders/{id}     → get single order
PATCH  /orders/{id}     → update order status flags
source=stepfunctions    → SFN callback to update DynamoDB status

Env vars
--------
  TABLE_ORDER  — DynamoDB order table name
  TABLE_MENU   — DynamoDB menu table name
  STEP_ARN     — Step Functions state machine ARN
  REDIS_HOST   — Redis host for cart clearing (optional)
  REDIS_PORT   — Redis port (default: 6379)
  SKIP_MENU    — "true" to bypass menu validation (default: false)
  LOG_LEVEL    — DEBUG | INFO | WARNING | ERROR (default: INFO)
"""

from __future__ import annotations

import os
from typing import Any

import boto3

from shared.aws_clients import get_dynamodb_resource, get_dynamodb_client
from shared.response_builder import ResponseBuilder, bind_request_id
from shared.structured_logger import get_logger, bind_lambda_context

from orders_service.repositories.order_repository import OrderRepository
from orders_service.channels.cart_service import CartService
from orders_service.channels.sfn_service import StepFunctionsService
from orders_service.routes.order_routes import (
    handle_post_order,
    handle_get_order,
    handle_list_orders,
    handle_patch_order,
    handle_sfn_event,
)

# ── Logger ────────────────────────────────────────────────────────────────────
_log = get_logger("orders")

# ── Env vars ──────────────────────────────────────────────────────────────────
_TABLE_ORDER = os.environ["TABLE_ORDER"]
_TABLE_MENU  = os.environ["TABLE_MENU"]
_STEP_ARN    = os.environ["STEP_ARN"]
_SKIP_MENU   = os.environ.get("SKIP_MENU", "false").lower() == "true"

# ── Module-level singletons (warm invocation reuse) ───────────────────────────
_dynamodb        = get_dynamodb_resource()
_dynamodb_client = get_dynamodb_client()
_sfn             = boto3.client("stepfunctions")

_CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,OPTIONS",
}


# ── Entry point ───────────────────────────────────────────────────────────────

def lambda_handler(event: dict, context: Any) -> dict:
    bind_lambda_context(context)
    bind_request_id(getattr(context, "aws_request_id", "unknown"))

    # ── Step Functions internal callback ──────────────────────────────────────
    if event.get("source") == "stepfunctions":
        repo = _build_repo()
        return handle_sfn_event(event, repo)

    method = event.get("httpMethod", "")
    path   = event.get("path", "").rstrip("/")

    _log.info("handler.invoked", method=method, path=path)

    repo = _build_repo()
    cart = CartService()
    sfn  = StepFunctionsService(sfn_client=_sfn, state_machine_arn=_STEP_ARN)

    # POST /orders
    if method == "POST" and path.endswith("/orders"):
        return handle_post_order(
            event, context, repo, cart, sfn,
            ddb_client=_dynamodb_client,
            menu_table=_TABLE_MENU,
            skip_menu=_SKIP_MENU,
        )

    # GET /orders (list)
    if method == "GET" and path.endswith("/orders"):
        return handle_list_orders(event, repo)

    # GET /orders/{id}
    if method == "GET" and "/orders/" in path:
        return handle_get_order(event, repo)

    # PATCH /orders/{id}
    if method == "PATCH" and "/orders/" in path:
        return handle_patch_order(event, repo, sfn)

    # OPTIONS (CORS preflight)
    if method == "OPTIONS":
        return {"statusCode": 200, "headers": _CORS_HEADERS, "body": ""}

    return ResponseBuilder.error(
        "METHOD_NOT_ALLOWED",
        f"{method} {path} is not supported.",
        status=405,
        headers=_CORS_HEADERS,
    )


# ── Factory ───────────────────────────────────────────────────────────────────

def _build_repo() -> OrderRepository:
    return OrderRepository(
        dynamodb_resource=_dynamodb,
        dynamodb_client=_dynamodb_client,
        table_name=_TABLE_ORDER,
    )
