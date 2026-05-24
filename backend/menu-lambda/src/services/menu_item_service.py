"""
MenuItemService — DynamoDB CRUD for MenuItem entities.

Optimistic locking:
  Updates use a ConditionExpression on `version`.
  If the stored version doesn't match the caller's version a ConflictError
  is raised so the handler can return HTTP 409.

Cache invalidation mirrors CategoryService — per-item key + list key.
"""
from __future__ import annotations

import os
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from models.menu_item import MenuItem
from services.cache_service import CacheService
from services.s3_service import S3Service
from utils.dynamo_helpers import (
    decimal_to_python, build_update_expression,
    encode_lek, decode_lek,
)
from utils.ids import new_id, utc_now
from utils.logger import get_logger
from utils.retry import retry

log = get_logger(__name__)

_TABLE_NAME = os.environ.get("MENU_TABLE", "MenuTable")
_PAGE_LIMIT  = 50


class MenuItemNotFoundError(Exception):
    pass


class MenuItemConflictError(Exception):
    """Raised when optimistic lock version mismatch is detected."""
    pass


class MenuItemService:
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

    @retry(retries=3, base_delay=0.1, exceptions=(ClientError,))
    def _ddb_get(self, pk: str, sk: str) -> Optional[dict]:
        resp = self._table.get_item(Key={"PK": pk, "SK": sk})
        item = resp.get("Item")
        return decimal_to_python(item) if item else None

    @retry(retries=3, base_delay=0.1, exceptions=(ClientError,))
    def _ddb_put(self, item: dict) -> None:
        self._table.put_item(Item=item)

    def _ddb_update_with_version(
        self, pk: str, sk: str, updates: dict, expected_version: int
    ) -> dict:
        """
        Update with optimistic locking.
        ConditionalCheckFailedException is NOT retried - it is a business error.

        version always uses the stable alias #ver in both UpdateExpression and
        ConditionExpression to avoid DynamoDB path-overlap validation errors.
        """
        new_version = expected_version + 1

        # Stable alias for the reserved word "version"
        attr_names: dict = {"#ver": "version"}
        attr_values: dict = {":newver": new_version, ":oldver": expected_version}
        set_parts: list = ["#ver = :newver"]

        for idx, (field, value) in enumerate(updates.items()):
            nk = f"#f{idx}"
            vk = f":u{idx}"
            attr_names[nk] = field
            attr_values[vk] = value
            set_parts.append(f"{nk} = {vk}")

        update_expr    = "SET " + ", ".join(set_parts)
        condition_expr = "#ver = :oldver"

        try:
            resp = self._table.update_item(
                Key={"PK": pk, "SK": sk},
                UpdateExpression=update_expr,
                ConditionExpression=condition_expr,
                ExpressionAttributeNames=attr_names,
                ExpressionAttributeValues=attr_values,
                ReturnValues="ALL_NEW",
            )
            return decimal_to_python(resp.get("Attributes", {}))
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "ConditionalCheckFailedException":
                raise MenuItemConflictError(
                    f"Version conflict: expected {expected_version}"
                ) from exc
            raise

    def _ddb_delete(self, pk: str, sk: str) -> None:
        self._table.delete_item(Key={"PK": pk, "SK": sk})

    @retry(retries=3, base_delay=0.1, exceptions=(ClientError,))
    def _ddb_list(
        self, pk: str, exclusive_start_key: Optional[dict] = None
    ) -> tuple[list[dict], Optional[dict]]:
        kwargs: dict = {
            "KeyConditionExpression": (
                Key("PK").eq(pk) & Key("SK").begins_with("ITEM#")
            ),
            "Limit": _PAGE_LIMIT,
        }
        if exclusive_start_key:
            kwargs["ExclusiveStartKey"] = exclusive_start_key

        resp = self._table.query(**kwargs)
        items = [decimal_to_python(i) for i in resp.get("Items", [])]
        return items, resp.get("LastEvaluatedKey")

    def _pk(self, tenant_id: str, restaurant_id: str) -> str:
        return f"TENANT#{tenant_id}#RESTAURANT#{restaurant_id}"

    def _sk(self, item_id: str) -> str:
        return f"ITEM#{item_id}"

    # ── Public API ────────────────────────────────────────────────────────

    def create(
        self, tenant_id: str, restaurant_id: str, body: dict
    ) -> MenuItem:
        item_id = new_id()
        now = utc_now()

        menu_item = MenuItem(
            itemId=item_id,
            tenantId=tenant_id,
            restaurantId=restaurant_id,
            categoryId=body.get("categoryId", ""),
            name=body.get("name", ""),
            description=body.get("description", ""),
            priceMinorUnits=int(body.get("priceMinorUnits", 0)),
            isActive=bool(body.get("isActive", True)),
            version=1,
            createdAt=now,
            updatedAt=now,
            imageKey=body.get("imageKey"),
            allergens=list(body.get("allergens") or []),
            arModelKey=body.get("arModelKey"),
        )
        menu_item.validate()

        self._ddb_put(menu_item.to_dynamo_item())

        self._cache.set(
            CacheService.item_key(tenant_id, restaurant_id, item_id),
            menu_item.to_dict(),
        )
        self._cache.delete(
            CacheService.items_list_key(tenant_id, restaurant_id)
        )

        log.info("MenuItem created", extra={
            "tenantId": tenant_id,
            "restaurantId": restaurant_id,
            "itemId": item_id,
        })
        return menu_item

    def get(
        self, tenant_id: str, restaurant_id: str, item_id: str
    ) -> MenuItem:
        cache_key = CacheService.item_key(tenant_id, restaurant_id, item_id)
        pk = self._pk(tenant_id, restaurant_id)
        sk = self._sk(item_id)

        raw = self._cache.get_or_load(
            cache_key,
            loader=lambda: self._ddb_get(pk, sk),
        )
        if raw is None:
            raise MenuItemNotFoundError(f"Item {item_id} not found")
        item = MenuItem.from_dict(raw)
        item.imageUrl   = self._s3.generate_read_url(item.imageKey)
        item.arModelUrl = self._s3.generate_read_url(item.arModelKey)
        return item

    def list(
        self,
        tenant_id: str,
        restaurant_id: str,
        encoded_lek: Optional[str] = None,
        category_id: Optional[str] = None,
    ) -> tuple[list[MenuItem], Optional[str]]:
        """
        List items for a restaurant, optionally filtered by categoryId.
        category_id filter is applied in-memory after the DDB query
        (no GSI needed for MVP — add GSI for high-volume tenants).
        """
        pk = self._pk(tenant_id, restaurant_id)
        exclusive_start = decode_lek(encoded_lek)

        # Only cache unfiltered first page
        if exclusive_start is None and category_id is None:
            cache_key = CacheService.items_list_key(tenant_id, restaurant_id)
            cached = self._cache.get(cache_key)
            if cached is not None:
                items = [MenuItem.from_dict(i) for i in cached.get("items", [])]
                return items, cached.get("lek")

        raw_items, lek = self._ddb_list(pk, exclusive_start)

        if category_id:
            raw_items = [i for i in raw_items if i.get("categoryId") == category_id]

        menu_items = [MenuItem.from_dict(i) for i in raw_items]
        for mi in menu_items:
            mi.imageUrl   = self._s3.generate_read_url(mi.imageKey)
            mi.arModelUrl = self._s3.generate_read_url(mi.arModelKey)

        if exclusive_start is None and category_id is None and lek is None:
            self._cache.set(
                CacheService.items_list_key(tenant_id, restaurant_id),
                {"items": [i.to_dict() for i in menu_items], "lek": None},
            )

        return menu_items, encode_lek(lek)

    def update(
        self,
        tenant_id: str,
        restaurant_id: str,
        item_id: str,
        body: dict,
    ) -> MenuItem:
        """
        Partial update with optimistic locking.
        Caller must supply `version` matching the current stored version.
        """
        current = self.get(tenant_id, restaurant_id, item_id)

        expected_version = body.get("version")
        if expected_version is None:
            raise ValueError("version is required for update")
        expected_version = int(expected_version)

        mutable = {
            "name", "description", "priceMinorUnits",
            "isActive", "imageKey", "allergens",
            "arModelKey", "categoryId",
        }
        # Exclude "version" — it is managed exclusively by the locking helper
        updates = {k: v for k, v in body.items() if k in mutable}
        updates["updatedAt"] = utc_now()

        pk = self._pk(tenant_id, restaurant_id)
        sk = self._sk(item_id)

        attrs = self._ddb_update_with_version(pk, sk, updates, expected_version)

        self._cache.delete(
            CacheService.item_key(tenant_id, restaurant_id, item_id),
            CacheService.items_list_key(tenant_id, restaurant_id),
        )

        log.info("MenuItem updated", extra={
            "tenantId": tenant_id,
            "restaurantId": restaurant_id,
            "itemId": item_id,
            "newVersion": expected_version + 1,
        })
        return MenuItem.from_dict(attrs)

    def delete(
        self, tenant_id: str, restaurant_id: str, item_id: str
    ) -> None:
        self.get(tenant_id, restaurant_id, item_id)  # 404 guard

        pk = self._pk(tenant_id, restaurant_id)
        sk = self._sk(item_id)
        self._ddb_delete(pk, sk)

        self._cache.delete(
            CacheService.item_key(tenant_id, restaurant_id, item_id),
            CacheService.items_list_key(tenant_id, restaurant_id),
        )
        log.info("MenuItem deleted", extra={
            "tenantId": tenant_id,
            "restaurantId": restaurant_id,
            "itemId": item_id,
        })