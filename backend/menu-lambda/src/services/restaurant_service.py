"""
RestaurantService — DynamoDB CRUD for Restaurant entities.

Write-through cache pattern:
  create / update / delete → mutate DDB first → invalidate / populate Redis
  get                      → Redis first → DDB fallback via CacheService.get_or_load
"""
from __future__ import annotations

import os
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from models.restaurant import Restaurant
from models.address import Address
from services.cache_service import CacheService
from services.s3_service import S3Service
from utils.dynamo_helpers import decimal_to_python, build_update_expression
from utils.ids import new_id, utc_now
from utils.logger import get_logger
from utils.retry import retry

log = get_logger(__name__)

_TABLE_NAME = os.environ.get("MENU_TABLE", "MenuTable")


class RestaurantNotFoundError(Exception):
    pass


class RestaurantService:
    def __init__(
        self,
        table=None,
        cache: Optional[CacheService] = None,
        s3_svc: Optional[S3Service] = None,
    ) -> None:
        self._table = table or boto3.resource("dynamodb").Table(_TABLE_NAME)
        self._cache = cache or CacheService()
        self._s3 = s3_svc or S3Service()

    # ── Private helpers ───────────────────────────────────────────────────

    def _cache_key(self, tenant_id: str, restaurant_id: str) -> str:
        return CacheService.restaurant_key(tenant_id, restaurant_id)

    @retry(retries=3, base_delay=0.1, exceptions=(ClientError,))
    def _ddb_get(self, pk: str, sk: str) -> Optional[dict]:
        resp = self._table.get_item(Key={"PK": pk, "SK": sk})
        item = resp.get("Item")
        return decimal_to_python(item) if item else None

    @retry(retries=3, base_delay=0.1, exceptions=(ClientError,))
    def _ddb_put(self, item: dict) -> None:
        self._table.put_item(Item=item)

    @retry(retries=3, base_delay=0.1, exceptions=(ClientError,))
    def _ddb_update(self, pk: str, sk: str, updates: dict) -> dict:
        expr, names, values = build_update_expression(updates)
        resp = self._table.update_item(
            Key={"PK": pk, "SK": sk},
            UpdateExpression=expr,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
        return decimal_to_python(resp.get("Attributes", {}))

    @retry(retries=3, base_delay=0.1, exceptions=(ClientError,))
    def _ddb_delete(self, pk: str, sk: str) -> None:
        self._table.delete_item(Key={"PK": pk, "SK": sk})

    # ── Public API ────────────────────────────────────────────────────────

    def create(self, tenant_id: str, body: dict) -> Restaurant:
        """Create a new restaurant; generate ID and timestamps."""
        restaurant_id = new_id()
        now = utc_now()

        addr_raw = body.get("address") or {}
        restaurant = Restaurant(
            restaurantId=restaurant_id,
            tenantId=tenant_id,
            name=body.get("name", ""),
            address=Address.from_dict(addr_raw),
            timezone=body.get("timezone", ""),
            currencyCode=body.get("currencyCode", ""),
            isActive=bool(body.get("isActive", True)),
            createdAt=now,
            updatedAt=now,
            logoKey=body.get("logoKey"),
        )
        restaurant.validate()

        dynamo_item = restaurant.to_dynamo_item()
        self._ddb_put(dynamo_item)

        # Write-through: populate cache immediately after successful write
        cache_key = self._cache_key(tenant_id, restaurant_id)
        self._cache.set(cache_key, restaurant.to_dict())

        log.info("Restaurant created", extra={
            "tenantId": tenant_id, "restaurantId": restaurant_id
        })
        return restaurant

    def get(self, tenant_id: str, restaurant_id: str) -> Restaurant:
        """Fetch restaurant; Redis → DDB fallback."""
        cache_key = self._cache_key(tenant_id, restaurant_id)
        pk = f"TENANT#{tenant_id}#RESTAURANT#{restaurant_id}"
        sk = "METADATA"

        raw = self._cache.get_or_load(
            cache_key,
            loader=lambda: self._ddb_get(pk, sk),
        )

        if raw is None:
            raise RestaurantNotFoundError(
                f"Restaurant {restaurant_id} not found for tenant {tenant_id}"
            )
        restaurant = Restaurant.from_dict(raw)
        restaurant.logoUrl = self._s3.generate_read_url(restaurant.logoKey)
        return restaurant

    def update(self, tenant_id: str, restaurant_id: str, body: dict) -> Restaurant:
        """Partial update — only fields present in body are changed."""
        # Verify existence first
        self.get(tenant_id, restaurant_id)

        mutable = {
            "name", "timezone", "currencyCode",
            "isActive", "logoKey",
        }
        updates: dict = {
            k: v for k, v in body.items() if k in mutable
        }

        # Address is a nested map — replace entirely if provided
        if "address" in body:
            updates["address"] = body["address"]

        updates["updatedAt"] = utc_now()

        pk = f"TENANT#{tenant_id}#RESTAURANT#{restaurant_id}"
        attrs = self._ddb_update(pk, "METADATA", updates)

        # Invalidate so next GET rebuilds from DDB
        self._cache.delete(self._cache_key(tenant_id, restaurant_id))

        restaurant = Restaurant.from_dynamo_item(attrs)
        log.info("Restaurant updated", extra={
            "tenantId": tenant_id, "restaurantId": restaurant_id
        })
        return restaurant


    def list_all(
        self,
        tenant_id: str,
        encoded_lek: Optional[str] = None,
    ) -> tuple[list[Restaurant], Optional[str]]:
        """
        List all restaurants for a tenant.
        Uses DynamoDB Scan with FilterExpression on PK prefix and SK = METADATA.
        Returns (restaurants, next_encoded_lek).
        """
        from boto3.dynamodb.conditions import Attr
        from utils.dynamo_helpers import encode_lek, decode_lek

        exclusive_start = decode_lek(encoded_lek)
        pk_prefix       = f"TENANT#{tenant_id}#RESTAURANT#"

        scan_kwargs: dict = {
            "FilterExpression": (
                Attr("PK").begins_with(pk_prefix) &
                Attr("SK").eq("METADATA")
            ),
            "Limit": 100,
        }
        if exclusive_start:
            scan_kwargs["ExclusiveStartKey"] = exclusive_start

        resp  = self._table.scan(**scan_kwargs)
        items = [decimal_to_python(i) for i in resp.get("Items", [])]
        lek   = resp.get("LastEvaluatedKey")

        restaurants = []
        for raw in items:
            r = Restaurant.from_dynamo_item(raw)
            r.logoUrl = self._s3.generate_read_url(r.logoKey)
            restaurants.append(r)

        log.info("Restaurants listed", extra={
            "tenantId": tenant_id, "count": len(restaurants)
        })
        return restaurants, encode_lek(lek)

    def delete(self, tenant_id: str, restaurant_id: str) -> None:
        """Delete restaurant and invalidate cache."""
        self.get(tenant_id, restaurant_id)  # 404 guard

        pk = f"TENANT#{tenant_id}#RESTAURANT#{restaurant_id}"
        self._ddb_delete(pk, "METADATA")
        self._cache.delete(self._cache_key(tenant_id, restaurant_id))

        log.info("Restaurant deleted", extra={
            "tenantId": tenant_id, "restaurantId": restaurant_id
        })