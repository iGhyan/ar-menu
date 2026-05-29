"""
handlers/table_handler.py
==========================
Restaurant Table/Floor CRUD handler.

Routes:
  GET    /menus/restaurants/{restaurantId}/tables
  POST   /menus/restaurants/{restaurantId}/tables
  PUT    /menus/restaurants/{restaurantId}/tables/{tableId}
  DELETE /menus/restaurants/{restaurantId}/tables/{tableId}

DynamoDB Schema:
  PK: TENANT#{tenantId}#RESTAURANT#{restaurantId}
  SK: TABLE#{tableId}

Auth:
  GET    → public (same as menu reads)
  POST / PUT / DELETE → admin or tenant only (enforced by router)

Env vars:
  TABLE_RESTAURANT_TABLES — DynamoDB table name (default: RestaurantTables-dev)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from handlers.request import RequestContext
from utils.dynamo_helpers import decimal_to_python, build_update_expression
from utils.ids import new_id, utc_now
from utils.logger import get_logger
from utils.response import ok, created, bad_request, not_found, internal_error

log = get_logger(__name__)

_TABLE_NAME = os.environ.get("TABLE_RESTAURANT_TABLES", "RestaurantTables-dev")

# ── Module-level DDB table (reused across warm invocations) ───────────────────
_ddb   = boto3.resource("dynamodb")
_table = _ddb.Table(_TABLE_NAME)

# ── Allowed fields for PUT ────────────────────────────────────────────────────
_ALLOWED_UPDATE_FIELDS = {"tableNumber", "zone", "outlet", "capacity", "isActive"}


# ── Entry point ───────────────────────────────────────────────────────────────

def handle_table(ctx: RequestContext) -> dict:
    """
    Route table requests to the appropriate sub-handler.
    Auth is enforced upstream in the router for write methods.
    """
    restaurant_id = ctx.path_params.get("restaurantId", "")
    table_id      = ctx.path_params.get("tableId", "")
    tenant_id     = ctx.tenant_id or ""

    if not restaurant_id:
        return bad_request("restaurantId path parameter is required")
    if not tenant_id:
        return bad_request("X-Tenant-Id header is required")

    try:
        if ctx.method == "GET":
            return _list_tables(tenant_id, restaurant_id)

        if ctx.method == "POST":
            return _create_table(tenant_id, restaurant_id, ctx.body)

        if ctx.method == "PUT":
            if not table_id:
                return bad_request("tableId path parameter is required")
            return _update_table(tenant_id, restaurant_id, table_id, ctx.body)

        if ctx.method == "DELETE":
            if not table_id:
                return bad_request("tableId path parameter is required")
            return _delete_table(tenant_id, restaurant_id, table_id)

        return bad_request(f"Method {ctx.method} not allowed")

    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        log.error(
            "table.ddb.error",
            extra={"error_code": code, "restaurant_id": restaurant_id,
                   "table_id": table_id, "method": ctx.method}
        )
        return internal_error()


# ── GET /tables ───────────────────────────────────────────────────────────────

def _list_tables(tenant_id: str, restaurant_id: str) -> dict:
    try:
        res = _table.query(
            KeyConditionExpression=(
                Key("PK").eq(_pk(tenant_id, restaurant_id))
                & Key("SK").begins_with("TABLE#")
            ),
        )
        items = [decimal_to_python(i) for i in res.get("Items", [])]
        # Strip internal DDB keys from response
        cleaned = [_strip_keys(i) for i in items]

        log.info(
            "table.list.success",
            extra={"restaurant_id": restaurant_id, "count": len(cleaned)}
        )
        return ok({"tables": cleaned, "count": len(cleaned)})

    except ClientError as exc:
        log.error(
            "table.list.failed",
            extra={"restaurant_id": restaurant_id,
                   "error": exc.response["Error"]["Code"]}
        )
        return internal_error()


# ── POST /tables ──────────────────────────────────────────────────────────────

def _create_table(tenant_id: str, restaurant_id: str, body: dict) -> dict:
    table_number = str(body.get("tableNumber", "")).strip()
    zone         = str(body.get("zone", "Main Hall")).strip()
    outlet       = str(body.get("outlet", zone)).strip()

    if not table_number:
        return bad_request("tableNumber is required")

    try:
        capacity = int(body.get("capacity", 4))
    except (ValueError, TypeError):
        return bad_request("capacity must be an integer")

    table_id = new_id()
    now      = utc_now()

    item = {
        "PK":           _pk(tenant_id, restaurant_id),
        "SK":           _sk(table_id),
        "tableId":      table_id,
        "tableNumber":  table_number,
        "zone":         zone,
        "outlet":       outlet,
        "capacity":     capacity,
        "isActive":     True,
        "tenantId":     tenant_id,
        "restaurantId": restaurant_id,
        "createdAt":    now,
        "updatedAt":    now,
    }

    try:
        _table.put_item(Item=item)
        log.info(
            "table.create.success",
            extra={"table_id": table_id, "table_number": table_number,
                   "zone": zone, "restaurant_id": restaurant_id}
        )
        return created(_strip_keys(item))
    except ClientError as exc:
        log.error(
            "table.create.failed",
            extra={"restaurant_id": restaurant_id,
                   "error": exc.response["Error"]["Code"]}
        )
        return internal_error()


# ── PUT /tables/{tableId} ─────────────────────────────────────────────────────

def _update_table(
    tenant_id:     str,
    restaurant_id: str,
    table_id:      str,
    body:          dict,
) -> dict:
    updates = {
        k: v for k, v in body.items()
        if k in _ALLOWED_UPDATE_FIELDS
    }

    if not updates:
        return bad_request(
            f"No valid fields to update. Allowed: {sorted(_ALLOWED_UPDATE_FIELDS)}"
        )

    updates["updatedAt"] = utc_now()
    expr, names, values  = build_update_expression(updates)

    try:
        _table.update_item(
            Key={"PK": _pk(tenant_id, restaurant_id), "SK": _sk(table_id)},
            UpdateExpression=expr,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ConditionExpression="attribute_exists(PK)",
        )
        log.info(
            "table.update.success",
            extra={"table_id": table_id, "updated_fields": list(updates.keys())}
        )
        return ok({"tableId": table_id, "updated": list(updates.keys())})

    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "ConditionalCheckFailedException":
            return not_found(f"Table {table_id}")
        log.error(
            "table.update.failed",
            extra={"table_id": table_id, "error": code}
        )
        return internal_error()


# ── DELETE /tables/{tableId} ──────────────────────────────────────────────────

def _delete_table(tenant_id: str, restaurant_id: str, table_id: str) -> dict:
    try:
        _table.delete_item(
            Key={"PK": _pk(tenant_id, restaurant_id), "SK": _sk(table_id)},
            ConditionExpression="attribute_exists(PK)",
        )
        log.info(
            "table.delete.success",
            extra={"table_id": table_id, "restaurant_id": restaurant_id}
        )
        return ok({"tableId": table_id, "deleted": True})

    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "ConditionalCheckFailedException":
            return not_found(f"Table {table_id}")
        log.error(
            "table.delete.failed",
            extra={"table_id": table_id, "error": code}
        )
        return internal_error()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pk(tenant_id: str, restaurant_id: str) -> str:
    return f"TENANT#{tenant_id}#RESTAURANT#{restaurant_id}"


def _sk(table_id: str) -> str:
    return f"TABLE#{table_id}"


def _strip_keys(item: dict) -> dict:
    """Remove DynamoDB-internal PK/SK keys from API response."""
    return {k: v for k, v in item.items() if k not in ("PK", "SK")}
