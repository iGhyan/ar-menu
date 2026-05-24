"""
Presigned URL handler.

Route handled:
  POST /menus/presigned-url

Body:
  {
    "tenantId":     "uuid",
    "restaurantId": "uuid",
    "assetType":    "logo | category-image | item-image | ar-model",
    "contentType":  "image/webp | image/jpeg | image/png | model/gltf-binary",
    "entityId":     "uuid (required for category-image, item-image, ar-model)"
  }
"""
from __future__ import annotations

from handlers.request import RequestContext
from models.base import ValidationError
from models.schemas import PresignedUrlRequest
from services.s3_service import S3Service
from utils.logger import get_logger
from utils import ok, bad_request, internal_error

log = get_logger(__name__)


def handle_presigned_url(ctx: RequestContext, svc: S3Service) -> dict:
    """Generate a PUT presigned URL for an admin asset upload."""
    try:
        if ctx.method != "POST":
            return bad_request(f"Method {ctx.method} not allowed")

        req = PresignedUrlRequest.from_dict(ctx.body)
        result = svc.generate_presigned_url(req)
        return ok(result)

    except ValidationError as exc:
        return bad_request("Validation failed", exc.errors)
    except Exception as exc:
        log.error("Unhandled error in presigned URL handler", extra={"error": str(exc)})
        return internal_error()