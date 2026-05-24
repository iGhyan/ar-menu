"""
S3Service -- presigned URL generation for admin asset uploads and client reads.

Write path (admin): generate_presigned_url()  -- PUT, 15-min expiry
Read  path (client): generate_read_url()       -- GET, 60-min expiry, used to
                      enrich entity responses with imageUrl / logoUrl fields.
"""
from __future__ import annotations

import os
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

from models.schemas import PresignedUrlRequest
from utils.logger import get_logger

log = get_logger(__name__)

_BUCKET         = os.environ.get("S3_BUCKET", "menu-assets")
_WRITE_EXPIRY   = int(os.environ.get("S3_PRESIGN_EXPIRY_SECONDS", "900"))   # 15 min
_READ_EXPIRY    = int(os.environ.get("S3_READ_URL_EXPIRY_SECONDS", "3600")) # 60 min


class S3Service:
    def __init__(self, s3_client=None) -> None:
        self._s3 = s3_client or boto3.client("s3")

    # -- Write (admin upload) ----------------------------------------------

    def generate_presigned_url(self, request: PresignedUrlRequest) -> dict[str, Any]:
        """
        Generate a presigned PUT URL for a given asset type.

        Returns:
            {
                "uploadUrl": "https://...",
                "s3Key":     "TENANT#.../...",
                "expiresIn": 900
            }
        """
        request.validate()
        key = request.s3_key()

        try:
            url = self._s3.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": _BUCKET,
                    "Key": key,
                    "ContentType": request.contentType,
                },
                ExpiresIn=_WRITE_EXPIRY,
            )
        except ClientError as exc:
            log.error(
                "Failed to generate presigned PUT URL",
                extra={"key": key, "error": str(exc)},
            )
            raise

        log.info("Presigned PUT URL generated", extra={
            "tenantId": request.tenantId,
            "restaurantId": request.restaurantId,
            "assetType": request.assetType,
            "s3Key": key,
        })
        return {
            "uploadUrl": url,
            "s3Key": key,
            "expiresIn": _WRITE_EXPIRY,
        }

    # -- Read (client image fetch) -----------------------------------------

    def generate_read_url(self, s3_key: Optional[str]) -> Optional[str]:
        """
        Generate a presigned GET URL for an existing S3 object.

        Returns None when s3_key is None/empty so callers can skip safely:
            entity.logoUrl = s3_svc.generate_read_url(entity.logoKey)
        """
        if not s3_key:
            return None

        try:
            url = self._s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": _BUCKET, "Key": s3_key},
                ExpiresIn=_READ_EXPIRY,
            )
            log.info("Presigned GET URL generated", extra={"s3Key": s3_key})
            return url
        except ClientError as exc:
            log.warning(
                "Failed to generate presigned GET URL — returning None",
                extra={"key": s3_key, "error": str(exc)},
            )
            return None