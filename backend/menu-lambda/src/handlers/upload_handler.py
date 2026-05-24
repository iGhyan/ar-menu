"""
Upload handler — direct multipart/form-data file uploads.

Routes handled:
  POST /menus/upload/restaurants/{restaurantId}/logo
  POST /menus/upload/restaurants/{restaurantId}/categories/{categoryId}/image
  POST /menus/upload/restaurants/{restaurantId}/items/{itemId}/image
  POST /menus/upload/restaurants/{restaurantId}/items/{itemId}/ar-model

All routes accept multipart/form-data with:
  file      : binary file field
  tenantId  : UUID string field

Response on success:
  {
    "s3Key":   "TENANT#.../...",
    "fileUrl": "https://s3.amazonaws.com/...?X-Amz-Signature=...",
    "size":    12345,
    "type":    "image/jpeg"
  }
"""
from __future__ import annotations

from handlers.request import RequestContext
from repository.s3 import (
    S3Repository,
    FileTooLargeError,
    InvalidContentTypeError,
    MissingFileError,
    MissingFieldError,
)
from utils.logger import get_logger
from utils import ok, bad_request, internal_error

log = get_logger(__name__)


def handle_upload(
    ctx: RequestContext,
    repo: S3Repository,
    raw_event: dict,
) -> dict:
    """
    Entry point called by router for all /menus/upload/* paths.
    raw_event is passed through so the repository can parse multipart body.
    """
    try:
        if ctx.method != "POST":
            return bad_request(f"Method {ctx.method} not allowed on upload endpoints")

        path = ctx.path
        path_params = ctx.path_params
        restaurant_id = path_params.get("restaurantId", "")

        if not restaurant_id:
            return bad_request("restaurantId path parameter is required")

        if not ctx.tenant_id:
            return bad_request("X-Tenant-Id header is required")

        # ── Route: restaurant logo ─────────────────────────────────────────
        if path.endswith("/logo"):
            result = repo.upload_restaurant_logo(raw_event, restaurant_id)
            return ok({
                **result,
                "message": "Restaurant logo uploaded successfully. "
                           "Save s3Key to the restaurant via PUT /menus/restaurants/{id}",
            })

        # ── Route: category image ──────────────────────────────────────────
        category_id = path_params.get("categoryId", "")
        if "/categories/" in path and path.endswith("/image"):
            if not category_id:
                return bad_request("categoryId path parameter is required")
            result = repo.upload_category_image(raw_event, restaurant_id, category_id)
            return ok({
                **result,
                "message": "Category image uploaded successfully. "
                           "Save s3Key to the category via PUT /menus/restaurants/{id}/categories/{catId}",
            })

        # ── Route: item image ──────────────────────────────────────────────
        item_id = path_params.get("itemId", "")
        if "/items/" in path and path.endswith("/image"):
            if not item_id:
                return bad_request("itemId path parameter is required")
            result = repo.upload_item_image(raw_event, restaurant_id, item_id)
            return ok({
                **result,
                "message": "Item image uploaded successfully. "
                           "Save s3Key to the item via PUT /menus/restaurants/{id}/items/{itemId}",
            })

        # ── Route: item AR model ───────────────────────────────────────────
        if "/items/" in path and path.endswith("/ar-model"):
            if not item_id:
                return bad_request("itemId path parameter is required")
            result = repo.upload_item_ar_model(raw_event, restaurant_id, item_id)
            return ok({
                **result,
                "message": "Item AR model uploaded successfully. "
                           "Save s3Key to the item via PUT /menus/restaurants/{id}/items/{itemId}",
            })

        return bad_request(f"Unknown upload route: {path}")

    except MissingFileError as exc:
        return bad_request(str(exc))
    except MissingFieldError as exc:
        return bad_request(str(exc))
    except InvalidContentTypeError as exc:
        return bad_request(str(exc))
    except FileTooLargeError as exc:
        return bad_request(str(exc))
    except Exception as exc:
        log.error("Unhandled error in upload handler", extra={"error": str(exc)})
        return internal_error()