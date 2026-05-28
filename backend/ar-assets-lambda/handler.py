"""
handler.py
==========
ar-assets-lambda  —  thin orchestration handler.

Routes
------
GET    /ar/{restaurantId}/{itemId}  → public  (guest AR view — no auth)
PUT    /ar/{restaurantId}/{itemId}  → admin or tenant only
DELETE /ar/{restaurantId}/{itemId}  → admin or tenant only
OPTIONS /ar/{restaurantId}/{itemId} → CORS preflight

Env vars
--------
  ASSET_BUCKET_NAME  — S3 bucket for AR models
  CF_DOMAIN          — CloudFront distribution domain
  TABLE_MENU         — DynamoDB table name
  COGNITO_REGION         — (optional, default: ap-south-1)
  COGNITO_USER_POOL_ID   — (optional, override pool)
  COGNITO_CLIENT_ID      — (optional, override client)
  LOG_LEVEL              — DEBUG | INFO | WARNING | ERROR (default: INFO)
"""

from __future__ import annotations

import os
from typing import Any

from shared.aws_clients import get_s3_client, get_dynamodb_resource
from shared.cognito_auth import CognitoAuth
from shared.exceptions import (
    AppBaseException,
    AuthError,
    BadRequestError,
    ForbiddenError,
    MissingParameterError,
    ResourceNotFoundError,
    StorageError,
)
from shared.response_builder import ResponseBuilder, bind_request_id
from shared.structured_logger import get_logger, bind_lambda_context

from ar_assets.service import ArAssetsService

# ── Module-level singletons (reused across warm invocations) ──────────────────
_log  = get_logger("ar-assets")
_auth = CognitoAuth()

# ── Env vars ──────────────────────────────────────────────────────────────────
_BUCKET_NAME = os.environ["ASSET_BUCKET_NAME"]
_CF_DOMAIN   = os.environ["CF_DOMAIN"]
_TABLE_MENU  = os.environ["TABLE_MENU"]

# ── RBAC: which Cognito groups can mutate AR assets ───────────────────────────
_MUTATE_ROLES = ["menulay_admin", "menulay_tenant"]

# ── CORS headers (applied to all responses) ───────────────────────────────────
_CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Content-Type,X-Tenant-Id,Authorization",
    "Access-Control-Allow-Methods": "GET,PUT,DELETE,OPTIONS",
}


# ── Entry point ───────────────────────────────────────────────────────────────

def lambda_handler(event: dict, context: Any) -> dict:
    """Route API Gateway requests to the appropriate handler."""
    bind_lambda_context(context)
    bind_request_id(getattr(context, "aws_request_id", "unknown"))

    method = event.get("httpMethod", "")
    _log.info("handler.invoked", method=method)

    # ── CORS preflight ────────────────────────────────────────────────────────
    if method == "OPTIONS":
        return _cors_preflight()

    # ── Path parameters ───────────────────────────────────────────────────────
    try:
        restaurant_id, item_id = _extract_path_params(event)
    except MissingParameterError as exc:
        return _error_response(exc)

    # ── Build service (fresh boto3 clients per cold start, reused on warm) ────
    service = _build_service()

    # ── Route ─────────────────────────────────────────────────────────────────
    try:
        if method == "GET":
            return _handle_get(event, service, restaurant_id, item_id)

        if method in ("PUT", "DELETE"):
            return _handle_mutate(event, service, method, restaurant_id, item_id)

        return ResponseBuilder.error(
            "METHOD_NOT_ALLOWED", f"Method '{method}' is not supported.", status=405,
            headers=_CORS_HEADERS,
        )

    except (AuthError, ForbiddenError) as exc:
        _log.warning("handler.auth.failed", error_code=exc.error_code, method=method)
        return ResponseBuilder.from_exception(exc, headers=_CORS_HEADERS)

    except ResourceNotFoundError as exc:
        return ResponseBuilder.from_exception(exc, headers=_CORS_HEADERS)

    except (BadRequestError,) as exc:
        return ResponseBuilder.from_exception(exc, headers=_CORS_HEADERS)

    except StorageError as exc:
        _log.error("handler.storage.error", error_code=exc.error_code, exc_message=exc.message)
        return ResponseBuilder.from_exception(exc, headers=_CORS_HEADERS)

    except AppBaseException as exc:
        _log.error("handler.unexpected.app_error", error_code=exc.error_code)
        return ResponseBuilder.from_exception(exc, headers=_CORS_HEADERS)

    except Exception as exc:  # noqa: BLE001
        _log.error("handler.unexpected.error", exc_type=type(exc).__name__, exc_message=str(exc))
        return ResponseBuilder.error(
            "INTERNAL_ERROR", "An unexpected error occurred.", status=500,
            headers=_CORS_HEADERS,
        )


# ── Route handlers ────────────────────────────────────────────────────────────

def _handle_get(
    event:         dict,
    service:       ArAssetsService,
    restaurant_id: str,
    item_id:       str,
) -> dict:
    """Public route — no auth. tenant_id from header or query string."""
    tenant_id = _extract_tenant_id(event)
    if not tenant_id:
        return ResponseBuilder.error(
            "MISSING_TENANT_ID",
            "X-Tenant-Id header or tenantId query parameter is required.",
            status=400,
            headers=_CORS_HEADERS,
        )

    data = service.get_ar_asset(tenant_id, restaurant_id, item_id)
    return ResponseBuilder.success(data=data, headers=_CORS_HEADERS)


def _handle_mutate(
    event:         dict,
    service:       ArAssetsService,
    method:        str,
    restaurant_id: str,
    item_id:       str,
) -> dict:
    """Auth-required route — validates JWT and enforces RBAC."""
    # Verify JWT — raises AuthError on failure
    user = _auth.get_user_from_event(event)

    # RBAC — raises RbacError on failure
    _auth.require_roles(user, _MUTATE_ROLES)

    tenant_id = user.tenant_id
    if not tenant_id:
        return ResponseBuilder.error(
            "MISSING_TENANT_ID",
            "tenant_id not found in JWT token.",
            status=400,
            headers=_CORS_HEADERS,
        )

    _log.info(
        "handler.mutate.authorized",
        method=method,
        user_sub=user.sub,
        tenant_id=tenant_id,
    )

    if method == "PUT":
        data = service.update_ar_asset(
            tenant_id, restaurant_id, item_id,
            raw_body=event.get("body"),
        )
        return ResponseBuilder.success(data=data, headers=_CORS_HEADERS)

    # DELETE
    data = service.delete_ar_asset(tenant_id, restaurant_id, item_id)
    return ResponseBuilder.success(data=data, headers=_CORS_HEADERS)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_path_params(event: dict) -> tuple[str, str]:
    params        = event.get("pathParameters") or {}
    restaurant_id = params.get("restaurantId")
    item_id       = params.get("itemId")

    if not restaurant_id:
        raise MissingParameterError("restaurantId")
    if not item_id:
        raise MissingParameterError("itemId")

    return restaurant_id, item_id


def _extract_tenant_id(event: dict) -> str | None:
    headers = event.get("headers") or {}
    tid = headers.get("x-tenant-id") or headers.get("X-Tenant-Id")
    if not tid:
        params = event.get("queryStringParameters") or {}
        tid = params.get("tenantId")
    return tid or None


def _cors_preflight() -> dict:
    return {
        "statusCode": 200,
        "headers":    _CORS_HEADERS,
        "body":       "",
    }


def _error_response(exc: AppBaseException) -> dict:
    return ResponseBuilder.from_exception(exc, headers=_CORS_HEADERS)


def _build_service() -> ArAssetsService:
    """
    Construct ArAssetsService with injected boto3 clients.

    boto3 clients are cached by get_s3_client() / get_dynamodb_resource()
    so this is cheap on warm invocations.
    """
    import boto3  # noqa: PLC0415 — boto3 available in Lambda runtime

    s3_client = get_s3_client()
    ddb_table = get_dynamodb_resource().Table(_TABLE_MENU)
    cf_client = boto3.client("cloudfront")

    return ArAssetsService(
        s3_client=s3_client,
        ddb_table=ddb_table,
        cf_client=cf_client,
        bucket_name=_BUCKET_NAME,
        cf_domain=_CF_DOMAIN,
    )
