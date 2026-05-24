"""
ar-assets-lambda  —  with Cognito JWT auth + RBAC
API GW REST  GET    /ar/{restaurantId}/{itemId}  → public (guest AR view)
             PUT    /ar/{restaurantId}/{itemId}  → admin or tenant only
             DELETE /ar/{restaurantId}/{itemId}  → admin or tenant only
"""

from __future__ import annotations

import json
import logging
import os
import time
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError

# ── NEW: import cognito auth ───────────────────────────────────────────────────
from cognito_auth import (
    get_user_from_event,
    is_admin_or_tenant,
    r_unauthorized,
    r_forbidden,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BUCKET_AR  = os.environ["BUCKET_AR"]
CF_DOMAIN  = os.environ["CF_DOMAIN"]
TABLE_MENU = os.environ["TABLE_MENU"]

s3  = boto3.client("s3")
ddb = boto3.resource("dynamodb").Table(TABLE_MENU)
cf  = boto3.client("cloudfront")

PRESIGN_TTL = 900


# ── helpers ───────────────────────────────────────────────────────────────────

def _resp(status: int, body: Any) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,X-Tenant-Id,Authorization",
            "Access-Control-Allow-Methods": "GET,PUT,DELETE,OPTIONS",
        },
        "body": json.dumps(body),
    }


def _cors_preflight() -> dict:
    return {
        "statusCode": 200,
        "headers": {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,X-Tenant-Id,Authorization",
            "Access-Control-Allow-Methods": "GET,PUT,DELETE,OPTIONS",
        },
        "body": "",
    }


def _path_params(event: dict) -> tuple[str | None, str | None]:
    params = event.get("pathParameters") or {}
    return params.get("restaurantId"), params.get("itemId")


def _tenant_id_from_event(event: dict) -> str | None:
    headers = event.get("headers") or {}
    return headers.get("x-tenant-id") or headers.get("X-Tenant-Id")


def _ddb_key(tenant_id: str, restaurant_id: str, item_id: str) -> dict:
    return {
        "PK": f"TENANT#{tenant_id}#RESTAURANT#{restaurant_id}",
        "SK": f"ITEM#{item_id}",
    }


def _s3_key(tenant_id: str, restaurant_id: str, item_id: str) -> str:
    return f"TENANT#{tenant_id}/restaurants/{restaurant_id}/ar-models/{item_id}.glb"


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(i) for i in obj]
    return obj


# ── route handlers ────────────────────────────────────────────────────────────

def _get(tenant_id: str, restaurant_id: str, item_id: str) -> dict:
    """Public — no auth needed. Guest views AR model."""
    ddb_key = _ddb_key(tenant_id, restaurant_id, item_id)

    try:
        resp = ddb.get_item(Key=ddb_key, ProjectionExpression="arModelKey")
    except ClientError as exc:
        logger.error("DDB get_item error: %s", exc)
        return _resp(500, {"error": "database_error"})

    item = resp.get("Item")
    if not item:
        return _resp(404, {"error": "item_not_found"})

    if "arModelKey" not in item:
        return _resp(404, {"error": "ar_model_not_configured"})

    ar_model_key = item["arModelKey"]
    try:
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET_AR, "Key": ar_model_key},
            ExpiresIn=PRESIGN_TTL,
        )
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        logger.error("S3 presign error [%s]: %s", error_code, exc)
        if error_code in ("AccessDenied", "403"):
            return _resp(403, {"error": "s3_access_denied"})
        return _resp(500, {"error": "presign_failed"})

    return _resp(200, {
        "itemId":       item_id,
        "restaurantId": restaurant_id,
        "presignedUrl": url,
        "expiresIn":    PRESIGN_TTL,
        "cfDomain":     CF_DOMAIN,
    })


def _put(tenant_id: str, restaurant_id: str, item_id: str, body: dict) -> dict:
    """Admin or Tenant only — update AR metadata."""
    allowed = {"arModelKey", "arScale", "arPlacement"}
    updates = {k: _sanitize(v) for k, v in body.items() if k in allowed}

    if not updates:
        return _resp(400, {"error": "no_valid_ar_fields"})

    ddb_key     = _ddb_key(tenant_id, restaurant_id, item_id)
    expr_parts  = [f"#f_{k} = :v_{k}" for k in updates]
    update_expr = "SET " + ", ".join(expr_parts)
    expr_names  = {f"#f_{k}": k for k in updates}
    expr_values = {f":v_{k}": v for k, v in updates.items()}

    try:
        ddb.update_item(
            Key=ddb_key,
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
            ConditionExpression="attribute_exists(PK)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return _resp(404, {"error": "item_not_found"})
        logger.error("DDB update_item error: %s", exc)
        return _resp(500, {"error": "database_error"})

    logger.info("AR metadata updated itemId=%s fields=%s", item_id, list(updates))
    return _resp(200, {"itemId": item_id, "updated": list(updates)})


def _delete(tenant_id: str, restaurant_id: str, item_id: str) -> dict:
    """Admin or Tenant only — remove AR metadata + CloudFront invalidation."""
    ddb_key = _ddb_key(tenant_id, restaurant_id, item_id)

    try:
        ddb.update_item(
            Key=ddb_key,
            UpdateExpression="REMOVE arModelKey, arScale, arPlacement",
            ConditionExpression="attribute_exists(PK)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return _resp(404, {"error": "item_not_found"})
        logger.error("DDB remove AR attrs error: %s", exc)
        return _resp(500, {"error": "database_error"})

    cf_path = f"/TENANT#{tenant_id}/restaurants/{restaurant_id}/ar-models/{item_id}.glb"
    try:
        _invalidate_cf(cf_path)
        logger.info("CF invalidation created for %s", cf_path)
    except Exception as exc:
        logger.warning("CF invalidation failed (non-critical): %s", exc)

    return _resp(200, {"itemId": item_id, "arMetadataRemoved": True})


def _invalidate_cf(path: str) -> None:
    dist_id = _resolve_cf_dist_id()
    cf.create_invalidation(
        DistributionId=dist_id,
        InvalidationBatch={
            "Paths": {"Quantity": 1, "Items": [path]},
            "CallerReference": str(int(time.time())),
        },
    )


def _resolve_cf_dist_id() -> str:
    if hasattr(_resolve_cf_dist_id, "_cached"):
        return _resolve_cf_dist_id._cached  # type: ignore[attr-defined]

    paginator = cf.get_paginator("list_distributions")
    for page in paginator.paginate():
        for dist in page["DistributionList"].get("Items", []):
            if dist["DomainName"] == CF_DOMAIN:
                _resolve_cf_dist_id._cached = dist["Id"]  # type: ignore[attr-defined]
                return dist["Id"]

    raise ValueError(f"No CloudFront distribution found for domain {CF_DOMAIN}")


# ── entry point ───────────────────────────────────────────────────────────────

def lambda_handler(event: dict, _context: Any) -> dict:
    method        = event.get("httpMethod", "")
    restaurant_id, item_id = _path_params(event)

    if not restaurant_id or not item_id:
        return _resp(400, {"error": "missing_path_parameters"})

    # OPTIONS — always allow (CORS preflight)
    if method == "OPTIONS":
        return _cors_preflight()

    # ── GET — PUBLIC (no auth needed) ─────────────────────────────────────────
    if method == "GET":
        # tenant_id from header (guest sends it in query or header)
        tenant_id = _tenant_id_from_event(event)
        if not tenant_id:
            # fallback: try query string
            params = event.get("queryStringParameters") or {}
            tenant_id = params.get("tenantId")
        if not tenant_id:
            return _resp(400, {"error": "missing_x_tenant_id_header"})
        return _get(tenant_id, restaurant_id, item_id)

    # ── PUT / DELETE — AUTH + RBAC (admin or tenant only) ────────────────────
    if method in ("PUT", "DELETE"):
        # Verify JWT token
        try:
            user = get_user_from_event(event)
        except ValueError as e:
            logger.warning("AR auth failed: %s", str(e))
            return r_unauthorized(str(e))
        except Exception as e:
            logger.error("AR auth error: %s", str(e))
            return r_unauthorized("Invalid or expired token")

        # RBAC: only admin or tenant
        if not is_admin_or_tenant(user):
            return r_forbidden("Only admin or tenant can modify AR assets")

        # tenant_id from JWT — secure, cannot be faked
        tenant_id = user.get("tenant_id")
        if not tenant_id:
            return _resp(400, {"error": "tenant_id not found in token"})

        logger.info("AR %s authorized user=%s tenant=%s", method, user.get("email"), tenant_id)

        if method == "PUT":
            try:
                body = json.loads(event.get("body") or "{}")
            except json.JSONDecodeError:
                return _resp(400, {"error": "invalid_json_body"})
            return _put(tenant_id, restaurant_id, item_id, body)

        if method == "DELETE":
            return _delete(tenant_id, restaurant_id, item_id)

    return _resp(405, {"error": "method_not_allowed"})