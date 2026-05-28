"""
ar_assets/service.py
=====================
ArAssetsService — encapsulates all business logic for AR asset operations.

Responsibilities
----------------
- GET  : fetch arModelKey from DynamoDB, generate S3 presigned URL
- PUT  : validate + update AR metadata fields in DynamoDB
- DELETE: remove AR metadata from DynamoDB + create CloudFront invalidation

All AWS clients are injected — no module-level globals — making the service
100% unit-testable without patching boto3 directly.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError

from shared.exceptions import (
    BadRequestError,
    CloudFrontError,
    InvalidJsonError,
    MissingParameterError,
    NoValidFieldsError,
    PresignError,
    ResourceNotFoundError,
    S3ReadError,
    S3WriteError,
)
from shared.structured_logger import get_logger

_log = get_logger("ar-assets.service")

# ── Allowed fields for PUT ────────────────────────────────────────────────────
_ALLOWED_AR_FIELDS = {"arModelKey", "arScale", "arPlacement"}

# ── Presigned URL TTL ─────────────────────────────────────────────────────────
PRESIGN_TTL = 900  # seconds


class ArAssetsService:
    """
    Business logic for AR asset CRUD operations.

    Parameters
    ----------
    s3_client         : boto3 S3 client
    ddb_table         : boto3 DynamoDB Table resource
    cf_client         : boto3 CloudFront client
    bucket_name       : S3 bucket name for AR assets
    cf_domain         : CloudFront distribution domain name
    """

    def __init__(
        self,
        s3_client:   Any,
        ddb_table:   Any,
        cf_client:   Any,
        bucket_name: str,
        cf_domain:   str,
    ):
        self._s3         = s3_client
        self._ddb        = ddb_table
        self._cf         = cf_client
        self._bucket     = bucket_name
        self._cf_domain  = cf_domain
        self._cf_dist_id: str | None = None   # cached CF distribution ID

    # ── GET ───────────────────────────────────────────────────────────────────

    def get_ar_asset(
        self,
        tenant_id:     str,
        restaurant_id: str,
        item_id:       str,
    ) -> dict:
        """
        Fetch AR model info and return a presigned S3 URL.

        Returns
        -------
        dict with presignedUrl, expiresIn, cfDomain, itemId, restaurantId

        Raises
        ------
        ResourceNotFoundError — item or arModelKey not found in DynamoDB
        S3ReadError           — S3 presign fails with access denied
        PresignError          — S3 presign fails for any other reason
        """
        _log.info(
            "ar.get.started",
            tenant_id=tenant_id,
            restaurant_id=restaurant_id,
            item_id=item_id,
        )

        item = self._ddb_get(tenant_id, restaurant_id, item_id)

        if "arModelKey" not in item:
            raise ResourceNotFoundError(
                resource="AR model",
                identifier=item_id,
            )

        ar_model_key = item["arModelKey"]
        presigned_url = self._presign(ar_model_key)

        _log.info("ar.get.success", item_id=item_id, key=ar_model_key)

        return {
            "itemId":       item_id,
            "restaurantId": restaurant_id,
            "presignedUrl": presigned_url,
            "expiresIn":    PRESIGN_TTL,
            "cfDomain":     self._cf_domain,
        }

    # ── PUT ───────────────────────────────────────────────────────────────────

    def update_ar_asset(
        self,
        tenant_id:     str,
        restaurant_id: str,
        item_id:       str,
        raw_body:      str | None,
    ) -> dict:
        """
        Parse request body and update allowed AR fields in DynamoDB.

        Returns
        -------
        dict with itemId and list of updated field names

        Raises
        ------
        InvalidJsonError      — body is not valid JSON
        NoValidFieldsError    — body has no recognised AR fields
        ResourceNotFoundError — DynamoDB item does not exist
        """
        body = _parse_json_body(raw_body)
        updates = {
            k: _sanitize(v)
            for k, v in body.items()
            if k in _ALLOWED_AR_FIELDS
        }

        if not updates:
            raise NoValidFieldsError(allowed_fields=sorted(_ALLOWED_AR_FIELDS))

        _log.info(
            "ar.put.started",
            tenant_id=tenant_id,
            restaurant_id=restaurant_id,
            item_id=item_id,
            fields=list(updates),
        )

        self._ddb_update(tenant_id, restaurant_id, item_id, updates)

        _log.info("ar.put.success", item_id=item_id, fields=list(updates))
        return {"itemId": item_id, "updated": list(updates)}

    # ── DELETE ────────────────────────────────────────────────────────────────

    def delete_ar_asset(
        self,
        tenant_id:     str,
        restaurant_id: str,
        item_id:       str,
    ) -> dict:
        """
        Remove AR metadata from DynamoDB and invalidate CloudFront cache.

        CloudFront invalidation failure is logged as a warning but does NOT
        cause the operation to fail (it is non-critical).

        Returns
        -------
        dict with itemId and arMetadataRemoved=True

        Raises
        ------
        ResourceNotFoundError — DynamoDB item does not exist
        """
        _log.info(
            "ar.delete.started",
            tenant_id=tenant_id,
            restaurant_id=restaurant_id,
            item_id=item_id,
        )

        self._ddb_remove_ar_attrs(tenant_id, restaurant_id, item_id)

        # CloudFront invalidation — non-critical, never raises
        cf_path = (
            f"/TENANT#{tenant_id}/restaurants/{restaurant_id}"
            f"/ar-models/{item_id}.glb"
        )
        self._invalidate_cf_safe(cf_path, item_id)

        _log.info("ar.delete.success", item_id=item_id)
        return {"itemId": item_id, "arMetadataRemoved": True}

    # ── DynamoDB helpers ──────────────────────────────────────────────────────

    def _ddb_key(
        self,
        tenant_id:     str,
        restaurant_id: str,
        item_id:       str,
    ) -> dict:
        return {
            "PK": f"TENANT#{tenant_id}#RESTAURANT#{restaurant_id}",
            "SK": f"ITEM#{item_id}",
        }

    def _ddb_get(
        self,
        tenant_id:     str,
        restaurant_id: str,
        item_id:       str,
    ) -> dict:
        """Return the DynamoDB Item dict, raises ResourceNotFoundError if absent."""
        key = self._ddb_key(tenant_id, restaurant_id, item_id)
        try:
            resp = self._ddb.get_item(
                Key=key,
                ProjectionExpression="arModelKey",
            )
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            _log.error("ddb.get_item.failed", item_id=item_id, error_code=code)
            raise S3ReadError(bucket=self._bucket, key=item_id, cause=str(exc)) from exc

        item = resp.get("Item")
        if not item:
            raise ResourceNotFoundError(resource="Menu item", identifier=item_id)
        return item

    def _ddb_update(
        self,
        tenant_id:     str,
        restaurant_id: str,
        item_id:       str,
        updates:       dict,
    ) -> None:
        key          = self._ddb_key(tenant_id, restaurant_id, item_id)
        expr_parts   = [f"#f_{k} = :v_{k}" for k in updates]
        update_expr  = "SET " + ", ".join(expr_parts)
        expr_names   = {f"#f_{k}": k for k in updates}
        expr_values  = {f":v_{k}": v for k, v in updates.items()}

        try:
            self._ddb.update_item(
                Key=key,
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values,
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "ConditionalCheckFailedException":
                raise ResourceNotFoundError(resource="Menu item", identifier=item_id) from exc
            _log.error("ddb.update_item.failed", item_id=item_id, error_code=code)
            raise BadRequestError(f"Database update failed: {code}") from exc

    def _ddb_remove_ar_attrs(
        self,
        tenant_id:     str,
        restaurant_id: str,
        item_id:       str,
    ) -> None:
        key = self._ddb_key(tenant_id, restaurant_id, item_id)
        try:
            self._ddb.update_item(
                Key=key,
                UpdateExpression="REMOVE arModelKey, arScale, arPlacement",
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "ConditionalCheckFailedException":
                raise ResourceNotFoundError(resource="Menu item", identifier=item_id) from exc
            _log.error("ddb.remove_ar.failed", item_id=item_id, error_code=code)
            raise BadRequestError(f"Database remove failed: {code}") from exc

    # ── S3 presign ────────────────────────────────────────────────────────────

    def _presign(self, ar_model_key: str) -> str:
        try:
            return self._s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": ar_model_key},
                ExpiresIn=PRESIGN_TTL,
            )
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            _log.error("s3.presign.failed", key=ar_model_key, error_code=code)
            if code in ("AccessDenied", "403"):
                raise S3ReadError(
                    bucket=self._bucket,
                    key=ar_model_key,
                    cause="Access denied by S3 bucket policy.",
                ) from exc
            raise PresignError(
                bucket=self._bucket,
                key=ar_model_key,
                cause=str(exc),
            ) from exc

    # ── CloudFront helpers ────────────────────────────────────────────────────

    def _invalidate_cf_safe(self, path: str, item_id: str) -> None:
        """Attempt CF invalidation — logs warning on any failure, never raises."""
        try:
            dist_id = self._resolve_cf_dist_id()
            self._cf.create_invalidation(
                DistributionId=dist_id,
                InvalidationBatch={
                    "Paths": {"Quantity": 1, "Items": [path]},
                    "CallerReference": str(int(time.time())),
                },
            )
            _log.info("cf.invalidation.created", path=path, dist_id=dist_id)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "cf.invalidation.failed",
                path=path,
                item_id=item_id,
                exc_message=str(exc),
            )

    def _resolve_cf_dist_id(self) -> str:
        """Return CloudFront distribution ID for self._cf_domain (cached)."""
        if self._cf_dist_id:
            return self._cf_dist_id

        paginator = self._cf.get_paginator("list_distributions")
        for page in paginator.paginate():
            for dist in page["DistributionList"].get("Items", []):
                if dist["DomainName"] == self._cf_domain:
                    self._cf_dist_id = dist["Id"]
                    return dist["Id"]

        raise CloudFrontError(
            f"No CloudFront distribution found for domain '{self._cf_domain}'.",
            cf_domain=self._cf_domain,
        )


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _parse_json_body(raw: str | None) -> dict:
    """Parse JSON request body. Raises InvalidJsonError on failure."""
    import json
    try:
        return json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError) as exc:
        raise InvalidJsonError() from exc


def _sanitize(obj: Any) -> Any:
    """Recursively convert float → Decimal for DynamoDB compatibility."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(i) for i in obj]
    return obj
