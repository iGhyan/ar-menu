"""
Category handler — CRUD + list with optional inline image upload.

POST /menus/restaurants/{restaurantId}/categories
  Accepts EITHER:
    a) application/json     — no image
    b) multipart/form-data  — image + fields in one request

  Form-data fields:
    file         : image file (jpeg/png/webp)
    name         : category name
    displayOrder : integer
    isActive     : true / false
    OR use 'data' field with full JSON + file
"""
from __future__ import annotations

from handlers.request import RequestContext
from models.base import ValidationError
from models.schemas import PaginatedResponse
from repository.s3 import S3Repository, InvalidContentTypeError, FileTooLargeError
from services.category_service import CategoryService, CategoryNotFoundError
from utils.logger import get_logger
from utils import ok, created, bad_request, not_found, internal_error

log = get_logger(__name__)


def handle_category(
    ctx: RequestContext,
    svc: CategoryService,
    repo: S3Repository = None,
) -> dict:
    try:
        restaurant_id = ctx.path_params.get("restaurantId", "")
        category_id   = ctx.path_params.get("categoryId", "")

        if not ctx.tenant_id:
            return bad_request("X-Tenant-Id header is required")
        if not restaurant_id:
            return bad_request("restaurantId path parameter is required")

        if ctx.method == "GET":
            if category_id:
                return _get(ctx, svc, restaurant_id, category_id)
            return _list(ctx, svc, restaurant_id)
        if ctx.method == "POST":
            return _create(ctx, svc, repo, restaurant_id)
        if ctx.method == "PUT":
            if not category_id:
                return bad_request("categoryId path parameter is required")
            return _update(ctx, svc, restaurant_id, category_id)
        if ctx.method == "DELETE":
            if not category_id:
                return bad_request("categoryId path parameter is required")
            return _delete(ctx, svc, restaurant_id, category_id)

        return bad_request(f"Method {ctx.method} not allowed")

    except CategoryNotFoundError as exc:
        return not_found(str(exc))
    except ValidationError as exc:
        return bad_request("Validation failed", exc.errors)
    except (InvalidContentTypeError, FileTooLargeError) as exc:
        return bad_request(str(exc))
    except Exception as exc:
        log.error("Unhandled error in category handler", extra={"error": str(exc)})
        return internal_error()


def _get(ctx, svc, restaurant_id, category_id):
    cat = svc.get(ctx.tenant_id, restaurant_id, category_id)
    return ok(cat.to_dict())


def _list(ctx, svc, restaurant_id):
    cursor = ctx.query_params.get("cursor")
    cats, next_cursor = svc.list(ctx.tenant_id, restaurant_id, encoded_lek=cursor)
    resp = PaginatedResponse(
        items=[c.to_dict() for c in cats],
        count=len(cats),
        lastEvaluatedKey=next_cursor,
    )
    return ok(resp.to_dict())


def _create(ctx, svc, repo, restaurant_id):
    body = dict(ctx.body)

    # Convert isActive string to bool for form-data
    if "isActive" in body and isinstance(body["isActive"], str):
        body["isActive"] = body["isActive"].lower() == "true"
    if "displayOrder" in body and isinstance(body["displayOrder"], str):
        body["displayOrder"] = int(body["displayOrder"])

    # Step 1 — Create category
    cat = svc.create(ctx.tenant_id, restaurant_id, body)

    # Step 2 — Upload image if file present
    if ctx.is_multipart and repo is not None:
        try:
            s3_key, image_url = repo.upload_category_image(
                ctx.raw_event,
                restaurant_id,
                cat.categoryId,
                ctx.tenant_id,
            )
            if s3_key:
                # Step 3 — Update category with imageKey
                cat = svc.update(
                    ctx.tenant_id, restaurant_id, cat.categoryId,
                    {"imageKey": s3_key},
                )
                cat.imageUrl = image_url
        except Exception as exc:
            log.warning("Image upload failed after category creation", extra={
                "categoryId": cat.categoryId, "error": str(exc)
            })

    return created(cat.to_dict())


def _update(ctx, svc, restaurant_id, category_id):
    cat = svc.update(ctx.tenant_id, restaurant_id, category_id, ctx.body)
    return ok(cat.to_dict())


def _delete(ctx, svc, restaurant_id, category_id):
    svc.delete(ctx.tenant_id, restaurant_id, category_id)
    return ok({"message": "Category deleted"})