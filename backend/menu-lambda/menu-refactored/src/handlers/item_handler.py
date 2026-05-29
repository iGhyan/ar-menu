"""
Menu item handler — CRUD + list with optional inline image/AR upload.

POST accepts EITHER:
  a) application/json     — no files
  b) multipart/form-data  — image + AR model + fields in ONE request

Form-data fields:
  file           : item image (jpeg/png/webp)       ← optional
  arFile         : AR model (.glb)                  ← optional
  categoryId     : UUID
  name           : item name
  description    : max 500 chars
  priceMinorUnits: integer (e.g. 1200 = PKR 12.00)
  isActive       : true / false
  allergens      : comma-separated e.g. GLUTEN,DAIRY
"""
from __future__ import annotations

from handlers.request import RequestContext
from models.base import ValidationError
from models.schemas import PaginatedResponse
from repository.s3 import S3Repository, InvalidContentTypeError, FileTooLargeError
from services.menu_item_service import (
    MenuItemService, MenuItemNotFoundError, MenuItemConflictError,
)
from utils.logger import get_logger
from utils import ok, created, bad_request, not_found, conflict, internal_error

log = get_logger(__name__)


def handle_menu_item(
    ctx: RequestContext,
    svc: MenuItemService,
    repo: S3Repository = None,
) -> dict:
    try:
        restaurant_id = ctx.path_params.get("restaurantId", "")
        item_id       = ctx.path_params.get("itemId", "")

        if not ctx.tenant_id:
            return bad_request("X-Tenant-Id header is required")
        if not restaurant_id:
            return bad_request("restaurantId path parameter is required")

        if ctx.method == "GET":
            if item_id:
                return _get(ctx, svc, restaurant_id, item_id)
            return _list(ctx, svc, restaurant_id)
        if ctx.method == "POST":
            return _create(ctx, svc, repo, restaurant_id)
        if ctx.method == "PUT":
            if not item_id:
                return bad_request("itemId path parameter is required")
            return _update(ctx, svc, restaurant_id, item_id)
        if ctx.method == "DELETE":
            if not item_id:
                return bad_request("itemId path parameter is required")
            return _delete(ctx, svc, restaurant_id, item_id)

        return bad_request(f"Method {ctx.method} not allowed")

    except MenuItemNotFoundError as exc:
        return not_found(str(exc))
    except MenuItemConflictError as exc:
        return conflict(str(exc))
    except ValidationError as exc:
        return bad_request("Validation failed", exc.errors)
    except ValueError as exc:
        return bad_request(str(exc))
    except (InvalidContentTypeError, FileTooLargeError) as exc:
        return bad_request(str(exc))
    except Exception as exc:
        log.error("Unhandled item handler error", extra={"error": str(exc)})
        return internal_error()


def _get(ctx, svc, restaurant_id, item_id):
    item = svc.get(ctx.tenant_id, restaurant_id, item_id)
    return ok(item.to_dict())


def _list(ctx, svc, restaurant_id):
    cursor      = ctx.query_params.get("cursor")
    category_id = ctx.query_params.get("categoryId")
    items, next_cursor = svc.list(
        ctx.tenant_id, restaurant_id,
        encoded_lek=cursor, category_id=category_id,
    )
    resp = PaginatedResponse(
        items=[i.to_dict() for i in items],
        count=len(items),
        lastEvaluatedKey=next_cursor,
    )
    return ok(resp.to_dict())


def _build_item_body(body: dict) -> dict:
    """Normalise form-data string values to correct Python types."""
    body = dict(body)

    if "isActive" in body and isinstance(body["isActive"], str):
        body["isActive"] = body["isActive"].lower() in ("true", "1", "yes")

    if "priceMinorUnits" in body and isinstance(body["priceMinorUnits"], str):
        try:
            body["priceMinorUnits"] = int(body["priceMinorUnits"])
        except ValueError:
            pass

    if "allergens" in body and isinstance(body["allergens"], str):
        raw = body["allergens"].strip()
        body["allergens"] = [
            a.strip().upper() for a in raw.split(",") if a.strip()
        ] if raw else []

    return body


def _create(ctx, svc, repo, restaurant_id):
    body = _build_item_body(ctx.body)

    # Step 1 — Create item (version = 1)
    item = svc.create(ctx.tenant_id, restaurant_id, body)

    # Step 2 — Upload image and/or AR model if files present
    if ctx.is_multipart and repo is not None:
        try:
            assets = repo.upload_item_assets(
                ctx.raw_event,
                restaurant_id,
                item.itemId,
                ctx.tenant_id,
            )

            updates = {"version": 1}
            has_updates = False

            if assets.get("imageKey"):
                updates["imageKey"] = assets["imageKey"]
                item.imageUrl = assets["imageUrl"]
                has_updates = True

            if assets.get("arModelKey"):
                updates["arModelKey"]    = assets["arModelKey"]
                item.arModelUrl          = assets["arModelUrl"]
                has_updates = True

            # Step 3 — Save keys back to item
            if has_updates:
                item = svc.update(
                    ctx.tenant_id, restaurant_id, item.itemId, updates
                )
                # Re-inject URLs (not stored in DDB)
                if assets.get("imageUrl"):
                    item.imageUrl = assets["imageUrl"]
                if assets.get("arModelUrl"):
                    item.arModelUrl = assets["arModelUrl"]

        except Exception as exc:
            log.warning("Asset upload failed — item still created", extra={
                "itemId": item.itemId, "error": str(exc)
            })

    return created(item.to_dict())


def _update(ctx, svc, restaurant_id, item_id):
    item = svc.update(ctx.tenant_id, restaurant_id, item_id, ctx.body)
    return ok(item.to_dict())


def _delete(ctx, svc, restaurant_id, item_id):
    svc.delete(ctx.tenant_id, restaurant_id, item_id)
    return ok({"message": "Item deleted"})