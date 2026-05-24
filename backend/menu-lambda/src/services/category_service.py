"""
CategoryService — DynamoDB CRUD for MenuCategory entities.

Uses Query (not Scan) by PK prefix + SK begins_with("CATEGORY#").
Cache keys:
  - Per-category: menu:category:{tenantId}:{restaurantId}:{categoryId}
  - List:         menu:categories:{tenantId}:{restaurantId}
"""
from __future__ import annotations

import os
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from models.category import MenuCategory
from services.cache_service import CacheService
from services.s3_service import S3Service
from utils.dynamo_helpers import decimal_to_python, build_update_expression, encode_lek, decode_lek
from utils.ids import new_id, utc_now
from utils.logger import get_logger
from utils.retry import retry

log = get_logger(__name__)

_TABLE_NAME = os.environ.get("MENU_TABLE", "MenuTable")
_PAGE_LIMIT  = 50


class CategoryNotFoundError(Exception):
    pass


class CategoryService:
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

    @retry(retries=3, base_delay=0.1, exceptions=(ClientError,))
    def _ddb_list(
        self, pk: str, exclusive_start_key: Optional[dict] = None
    ) -> tuple[list[dict], Optional[dict]]:
        kwargs: dict = {
            "KeyConditionExpression": (
                Key("PK").eq(pk) & Key("SK").begins_with("CATEGORY#")
            ),
            "Limit": _PAGE_LIMIT,
        }
        if exclusive_start_key:
            kwargs["ExclusiveStartKey"] = exclusive_start_key

        resp = self._table.query(**kwargs)
        items = [decimal_to_python(i) for i in resp.get("Items", [])]
        lek = resp.get("LastEvaluatedKey")
        return items, lek

    def _pk(self, tenant_id: str, restaurant_id: str) -> str:
        return f"TENANT#{tenant_id}#RESTAURANT#{restaurant_id}"

    def _sk(self, category_id: str) -> str:
        return f"CATEGORY#{category_id}"

    # ── Public API ────────────────────────────────────────────────────────

    def create(
        self, tenant_id: str, restaurant_id: str, body: dict
    ) -> MenuCategory:
        category_id = new_id()

        category = MenuCategory(
            categoryId=category_id,
            tenantId=tenant_id,
            restaurantId=restaurant_id,
            name=body.get("name", ""),
            displayOrder=int(body.get("displayOrder", 0)),
            isActive=bool(body.get("isActive", True)),
            imageKey=body.get("imageKey"),
        )
        category.validate()

        self._ddb_put(category.to_dynamo_item())

        # Write-through: individual key + bust list
        self._cache.set(
            CacheService.category_key(tenant_id, restaurant_id, category_id),
            category.to_dict(),
        )
        self._cache.delete(
            CacheService.categories_list_key(tenant_id, restaurant_id)
        )

        log.info("Category created", extra={
            "tenantId": tenant_id,
            "restaurantId": restaurant_id,
            "categoryId": category_id,
        })
        return category

    def get(
        self, tenant_id: str, restaurant_id: str, category_id: str
    ) -> MenuCategory:
        cache_key = CacheService.category_key(tenant_id, restaurant_id, category_id)
        pk = self._pk(tenant_id, restaurant_id)
        sk = self._sk(category_id)

        raw = self._cache.get_or_load(
            cache_key,
            loader=lambda: self._ddb_get(pk, sk),
        )
        if raw is None:
            raise CategoryNotFoundError(
                f"Category {category_id} not found"
            )
        cat = MenuCategory.from_dict(raw)
        cat.imageUrl = self._s3.generate_read_url(cat.imageKey)
        return cat

    def list(
        self,
        tenant_id: str,
        restaurant_id: str,
        encoded_lek: Optional[str] = None,
    ) -> tuple[list[MenuCategory], Optional[str]]:
        """
        List all categories for a restaurant (paginated).
        Returns (categories, next_encoded_lek).
        Only caches the first page (no cursor) to keep things simple.
        """
        pk = self._pk(tenant_id, restaurant_id)
        cache_key = CacheService.categories_list_key(tenant_id, restaurant_id)
        exclusive_start = decode_lek(encoded_lek)

        if exclusive_start is None:
            # First page — try cache
            cached = self._cache.get(cache_key)
            if cached is not None:
                cats = [MenuCategory.from_dict(c) for c in cached.get("items", [])]
                return cats, cached.get("lek")

        items, lek = self._ddb_list(pk, exclusive_start)
        categories = [MenuCategory.from_dict(i) for i in items]
        for c in categories:
            c.imageUrl = self._s3.generate_read_url(c.imageKey)

        if exclusive_start is None and lek is None:
            # Complete first page — cache it
            self._cache.set(cache_key, {
                "items": [c.to_dict() for c in categories],
                "lek": None,
            })

        return categories, encode_lek(lek)

    def update(
        self,
        tenant_id: str,
        restaurant_id: str,
        category_id: str,
        body: dict,
    ) -> MenuCategory:
        self.get(tenant_id, restaurant_id, category_id)  # 404 guard

        mutable = {"name", "displayOrder", "isActive", "imageKey"}
        updates = {k: v for k, v in body.items() if k in mutable}
        updates["updatedAt"] = utc_now()

        pk = self._pk(tenant_id, restaurant_id)
        sk = self._sk(category_id)
        attrs = self._ddb_update(pk, sk, updates)

        # Bust both the individual and list caches
        self._cache.delete(
            CacheService.category_key(tenant_id, restaurant_id, category_id),
            CacheService.categories_list_key(tenant_id, restaurant_id),
        )

        log.info("Category updated", extra={
            "tenantId": tenant_id,
            "restaurantId": restaurant_id,
            "categoryId": category_id,
        })
        return MenuCategory.from_dict(attrs)

    def delete(
        self, tenant_id: str, restaurant_id: str, category_id: str
    ) -> None:
        self.get(tenant_id, restaurant_id, category_id)  # 404 guard

        pk = self._pk(tenant_id, restaurant_id)
        sk = self._sk(category_id)
        self._ddb_delete(pk, sk)

        self._cache.delete(
            CacheService.category_key(tenant_id, restaurant_id, category_id),
            CacheService.categories_list_key(tenant_id, restaurant_id),
        )
        log.info("Category deleted", extra={
            "tenantId": tenant_id,
            "restaurantId": restaurant_id,
            "categoryId": category_id,
        })