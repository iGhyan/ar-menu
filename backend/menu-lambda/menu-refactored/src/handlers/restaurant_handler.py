"""
Restaurant handler — CRUD with optional inline image upload.

POST accepts EITHER:
  a) application/json       — JSON body, no image
  b) multipart/form-data    — image + text fields in ONE request

Form-data field options for POST:
  Option A — flat fields:
    file, name, street, city, country, postcode,
    timezone, currencyCode, isActive

  Option B — JSON in 'data' field + file:
    data: {"name":"Fork","address":{...},...}
    file: <image>
"""
from __future__ import annotations

from handlers.request import RequestContext
from models.base import ValidationError
from models.schemas import PaginatedResponse
from repository.s3 import S3Repository, InvalidContentTypeError, FileTooLargeError
from services.restaurant_service import RestaurantService, RestaurantNotFoundError
from utils.logger import get_logger
from utils import ok, created, bad_request, not_found, internal_error

log = get_logger(__name__)


def handle_restaurant(
    ctx: RequestContext,
    svc: RestaurantService,
    repo: S3Repository = None,
) -> dict:
    try:
        if ctx.method == "GET":
            restaurant_id = ctx.path_params.get("restaurantId", "")
            if not restaurant_id:
                return _list_all(ctx, svc)
            return _get(ctx, svc)
        if ctx.method == "POST":
            return _create(ctx, svc, repo)
        if ctx.method == "PUT":
            return _update(ctx, svc)
        if ctx.method == "DELETE":
            return _delete(ctx, svc)
        return bad_request(f"Method {ctx.method} not allowed")
    except RestaurantNotFoundError as exc:
        return not_found(str(exc))
    except ValidationError as exc:
        return bad_request("Validation failed", exc.errors)
    except (InvalidContentTypeError, FileTooLargeError) as exc:
        return bad_request(str(exc))
    except Exception as exc:
        log.error("Unhandled restaurant handler error", extra={"error": str(exc)})
        return internal_error()


def _get(ctx: RequestContext, svc: RestaurantService) -> dict:
    restaurant_id = ctx.path_params.get("restaurantId", "")
    if not restaurant_id:
        return bad_request("restaurantId path parameter is required")
    if not ctx.tenant_id:
        return bad_request("X-Tenant-Id header is required")
    restaurant = svc.get(ctx.tenant_id, restaurant_id)
    return ok(restaurant.to_dict())


def _list_all(ctx: RequestContext, svc: RestaurantService) -> dict:
    if not ctx.tenant_id:
        return bad_request("X-Tenant-Id header is required")
    cursor = ctx.query_params.get("cursor")
    restaurants, next_cursor = svc.list_all(ctx.tenant_id, encoded_lek=cursor)
    resp = PaginatedResponse(
        items=[r.to_dict() for r in restaurants],
        count=len(restaurants),
        lastEvaluatedKey=next_cursor,
    )
    return ok(resp.to_dict())


def _build_body_from_form(raw: dict) -> dict:
    """
    Convert flat form-data fields into a nested body dict.

    Supports two formats:

    Format 1 — flat fields:
      name, street, city, country, postcode, timezone, currencyCode, isActive

    Format 2 — already nested (came from 'data' JSON field):
      name, address: {street, city, country, postcode}, timezone, ...
    """
    body = dict(raw)

    # Build nested address if flat fields are present
    if "address" not in body:
        address = {}
        for f in ("street", "city", "country", "postcode"):
            if f in body:
                address[f] = body.pop(f)
        if address:
            body["address"] = address

    # Convert string booleans
    if "isActive" in body and isinstance(body["isActive"], str):
        body["isActive"] = body["isActive"].lower() in ("true", "1", "yes")

    return body


def _create(
    ctx: RequestContext,
    svc: RestaurantService,
    repo: S3Repository,
) -> dict:
    if not ctx.tenant_id:
        return bad_request("X-Tenant-Id header is required")

    # Build body — works for JSON and form-data
    body = _build_body_from_form(ctx.body) if ctx.is_multipart else dict(ctx.body)

    # Step 1 — Create restaurant
    restaurant = svc.create(ctx.tenant_id, body)

    # Step 2 — Upload logo if file present
    if ctx.is_multipart and repo is not None:
        try:
            s3_key, logo_url = repo.upload_restaurant_logo(
                ctx.raw_event,
                restaurant.restaurantId,
                ctx.tenant_id,
            )
            if s3_key:
                # Step 3 — Save logoKey back to restaurant
                restaurant = svc.update(
                    ctx.tenant_id,
                    restaurant.restaurantId,
                    {"logoKey": s3_key},
                )
                restaurant.logoUrl = logo_url
                log.info("Logo saved to restaurant", extra={
                    "restaurantId": restaurant.restaurantId,
                    "s3Key": s3_key,
                })
        except Exception as exc:
            log.warning("Logo upload failed — restaurant still created", extra={
                "restaurantId": restaurant.restaurantId,
                "error": str(exc),
            })

    return created(restaurant.to_dict())


def _update(ctx: RequestContext, svc: RestaurantService) -> dict:
    restaurant_id = ctx.path_params.get("restaurantId", "")
    if not restaurant_id:
        return bad_request("restaurantId path parameter is required")
    if not ctx.tenant_id:
        return bad_request("X-Tenant-Id header is required")
    restaurant = svc.update(ctx.tenant_id, restaurant_id, ctx.body)
    return ok(restaurant.to_dict())


def _delete(ctx: RequestContext, svc: RestaurantService) -> dict:
    restaurant_id = ctx.path_params.get("restaurantId", "")
    if not restaurant_id:
        return bad_request("restaurantId path parameter is required")
    if not ctx.tenant_id:
        return bad_request("X-Tenant-Id header is required")
    svc.delete(ctx.tenant_id, restaurant_id)
    return ok({"message": "Restaurant deleted"})