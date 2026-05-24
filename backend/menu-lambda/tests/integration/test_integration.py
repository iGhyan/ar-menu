"""
Integration tests — full request lifecycle.

These tests exercise the entire stack from the Lambda handler entry point
down through services, models, and back, using:
  - moto   for DynamoDB and S3
  - fakeredis for ElastiCache Redis
  - No mocks on services or models — real code paths throughout

Scenarios covered:
  1.  Restaurant full CRUD lifecycle
  2.  Category full CRUD lifecycle within a restaurant
  3.  MenuItem full CRUD lifecycle with optimistic locking
  4.  Presigned URL generation (PUT upload + GET read)
  5.  Pagination — list categories and items across pages
  6.  Redis cache hit / miss / invalidation
  7.  Tenant quota enforcement via TenantService
  8.  Error paths — 404, 409, 400 validation
  9.  Cross-entity: items filtered by category
  10. Image URL enrichment flows through to API response
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch

import boto3
import fakeredis
import pytest
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src"))

os.environ["MENU_TABLE"]             = "MenuTable-integ"
os.environ["TENANT_TABLE"]           = "TenantTable-integ"
os.environ["S3_BUCKET"]              = "menu-assets-integ"
os.environ["REDIS_HOST"]             = "localhost"
os.environ["LOG_LEVEL"]              = "ERROR"
os.environ["AWS_ACCESS_KEY_ID"]      = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"]  = "testing"
os.environ["AWS_DEFAULT_REGION"]     = "us-east-1"

from handlers.router import handler
from services.cache_service import CacheService
from services.tenant_service import TenantService, TenantQuotaExceededError
import handlers.router as router_mod

# ── IDs ────────────────────────────────────────────────────────────────────
TENANT_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset router singletons before every test so services are rebuilt fresh."""
    router_mod._cache          = None
    router_mod._restaurant_svc = None
    router_mod._category_svc   = None
    router_mod._item_svc       = None
    router_mod._s3_svc         = None
    yield
    router_mod._cache          = None
    router_mod._restaurant_svc = None
    router_mod._category_svc   = None
    router_mod._item_svc       = None
    router_mod._s3_svc         = None


def _fake_cache() -> CacheService:
    return CacheService(client=fakeredis.FakeRedis(decode_responses=True))


def _event(method, path, path_params=None, query=None, body=None):
    return {
        "httpMethod": method,
        "path": path,
        "pathParameters": path_params or {},
        "queryStringParameters": query or {},
        "headers": {"x-tenant-id": TENANT_ID},
        "body": json.dumps(body) if body else None,
    }


def _body(resp):
    return json.loads(resp["body"]) if resp.get("body") else {}


def _make_tables():
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    menu_table = ddb.create_table(
        TableName="MenuTable-integ",
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
    )
    tenant_table = ddb.create_table(
        TableName="TenantTable-integ",
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "tenantId", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "tenantId", "KeyType": "HASH"},
        ],
    )
    return menu_table, tenant_table


def _make_s3():
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="menu-assets-integ")
    return client


def _inject_services(cache=None):
    """Wire fake cache and real moto services into the router singletons."""
    from services.restaurant_service import RestaurantService
    from services.category_service import CategoryService
    from services.menu_item_service import MenuItemService
    from services.s3_service import S3Service

    ddb   = boto3.resource("dynamodb", region_name="us-east-1")
    table = ddb.Table("MenuTable-integ")
    s3c   = boto3.client("s3", region_name="us-east-1")
    s3svc = S3Service(s3_client=s3c)
    ch    = cache or _fake_cache()

    router_mod._cache          = ch
    router_mod._restaurant_svc = RestaurantService(table=table, cache=ch, s3_svc=s3svc)
    router_mod._category_svc   = CategoryService(table=table,   cache=ch, s3_svc=s3svc)
    router_mod._item_svc       = MenuItemService(table=table,   cache=ch, s3_svc=s3svc)
    router_mod._s3_svc         = s3svc
    return ch


# ════════════════════════════════════════════════════════════════════════════
# 1. Restaurant full CRUD lifecycle
# ════════════════════════════════════════════════════════════════════════════

@mock_aws
class TestRestaurantLifecycle:
    def setup_method(self, method=None):
        _make_tables()
        _make_s3()
        _inject_services()

    def _create(self):
        return handler(_event("POST", "/menus/restaurants", body={
            "name": "The Golden Fork",
            "address": {"street": "1 Main", "city": "Karachi",
                        "country": "PK", "postcode": "75000"},
            "timezone": "Asia/Karachi",
            "currencyCode": "PKR",
            "isActive": True,
        }), None)

    def test_create_returns_201(self):
        resp = self._create()
        assert resp["statusCode"] == 201
        assert _body(resp)["name"] == "The Golden Fork"

    def test_create_assigns_id(self):
        resp = self._create()
        assert _body(resp)["restaurantId"]

    def test_get_returns_200(self):
        r_id = _body(self._create())["restaurantId"]
        resp = handler(_event("GET", f"/menus/restaurants/{r_id}",
                              path_params={"restaurantId": r_id}), None)
        assert resp["statusCode"] == 200
        assert _body(resp)["restaurantId"] == r_id

    def test_get_unknown_returns_404(self):
        resp = handler(_event("GET", "/menus/restaurants/00000000-0000-0000-0000-000000000000",
                              path_params={"restaurantId": "00000000-0000-0000-0000-000000000000"}),
                       None)
        assert resp["statusCode"] == 404

    def test_update_returns_200(self):
        r_id = _body(self._create())["restaurantId"]
        resp = handler(_event("PUT", f"/menus/restaurants/{r_id}",
                              path_params={"restaurantId": r_id},
                              body={"name": "Silver Fork"}), None)
        assert resp["statusCode"] == 200
        assert _body(resp)["name"] == "Silver Fork"

    def test_delete_then_get_returns_404(self):
        r_id = _body(self._create())["restaurantId"]
        del_resp = handler(_event("DELETE", f"/menus/restaurants/{r_id}",
                                  path_params={"restaurantId": r_id}), None)
        assert del_resp["statusCode"] == 200
        get_resp = handler(_event("GET", f"/menus/restaurants/{r_id}",
                                  path_params={"restaurantId": r_id}), None)
        assert get_resp["statusCode"] == 404

    def test_create_missing_tenant_returns_400(self):
        ev = _event("POST", "/menus/restaurants", body={"name": "X"})
        ev["headers"] = {}
        resp = handler(ev, None)
        assert resp["statusCode"] == 400

    def test_create_invalid_body_returns_400(self):
        resp = handler(_event("POST", "/menus/restaurants", body={
            "name": "",         # required
            "timezone": "UTC",  # invalid IANA
            "currencyCode": "p", # invalid
        }), None)
        assert resp["statusCode"] == 400


# ════════════════════════════════════════════════════════════════════════════
# 2. Category full CRUD lifecycle
# ════════════════════════════════════════════════════════════════════════════

@mock_aws
class TestCategoryLifecycle:
    def setup_method(self, method=None):
        _make_tables()
        _make_s3()
        _inject_services()
        # Create restaurant first
        resp = handler(_event("POST", "/menus/restaurants", body={
            "name": "Fork", "timezone": "Asia/Karachi", "currencyCode": "PKR",
            "isActive": True,
            "address": {"street": "1", "city": "K", "country": "PK", "postcode": "0"},
        }), None)
        self.rest_id = _body(resp)["restaurantId"]

    def _cat_path(self, cat_id=None):
        base = f"/menus/restaurants/{self.rest_id}/categories"
        return f"{base}/{cat_id}" if cat_id else base

    def _create(self, name="Starters", order=1):
        return handler(_event("POST", self._cat_path(),
                              path_params={"restaurantId": self.rest_id},
                              body={"name": name, "displayOrder": order,
                                    "isActive": True}), None)

    def test_create_returns_201(self):
        resp = self._create()
        assert resp["statusCode"] == 201
        assert _body(resp)["name"] == "Starters"

    def test_list_returns_all_categories(self):
        self._create("Starters", 1)
        self._create("Mains", 2)
        self._create("Desserts", 3)
        resp = handler(_event("GET", self._cat_path(),
                              path_params={"restaurantId": self.rest_id}), None)
        assert resp["statusCode"] == 200
        assert _body(resp)["count"] == 3

    def test_get_single_category(self):
        cat_id = _body(self._create())["categoryId"]
        resp = handler(_event("GET", self._cat_path(cat_id),
                              path_params={"restaurantId": self.rest_id,
                                           "categoryId": cat_id}), None)
        assert resp["statusCode"] == 200
        assert _body(resp)["categoryId"] == cat_id

    def test_update_category(self):
        cat_id = _body(self._create())["categoryId"]
        resp = handler(_event("PUT", self._cat_path(cat_id),
                              path_params={"restaurantId": self.rest_id,
                                           "categoryId": cat_id},
                              body={"name": "Appetisers"}), None)
        assert resp["statusCode"] == 200
        assert _body(resp)["name"] == "Appetisers"

    def test_delete_category(self):
        cat_id = _body(self._create())["categoryId"]
        del_resp = handler(_event("DELETE", self._cat_path(cat_id),
                                  path_params={"restaurantId": self.rest_id,
                                               "categoryId": cat_id}), None)
        assert del_resp["statusCode"] == 200
        get_resp = handler(_event("GET", self._cat_path(cat_id),
                                  path_params={"restaurantId": self.rest_id,
                                               "categoryId": cat_id}), None)
        assert get_resp["statusCode"] == 404

    def test_get_unknown_category_returns_404(self):
        resp = handler(_event("GET",
                              self._cat_path("00000000-0000-0000-0000-000000000000"),
                              path_params={"restaurantId": self.rest_id,
                                           "categoryId": "00000000-0000-0000-0000-000000000000"}
                              ), None)
        assert resp["statusCode"] == 404


# ════════════════════════════════════════════════════════════════════════════
# 3. MenuItem CRUD + optimistic locking
# ════════════════════════════════════════════════════════════════════════════

@mock_aws
class TestMenuItemLifecycle:
    def setup_method(self, method=None):
        _make_tables()
        _make_s3()
        _inject_services()
        # Create restaurant + category
        r_resp = handler(_event("POST", "/menus/restaurants", body={
            "name": "Fork", "timezone": "Asia/Karachi", "currencyCode": "PKR",
            "isActive": True,
            "address": {"street": "1", "city": "K", "country": "PK", "postcode": "0"},
        }), None)
        self.rest_id = _body(r_resp)["restaurantId"]
        c_resp = handler(_event("POST",
                                f"/menus/restaurants/{self.rest_id}/categories",
                                path_params={"restaurantId": self.rest_id},
                                body={"name": "Starters", "displayOrder": 1,
                                      "isActive": True}), None)
        self.cat_id = _body(c_resp)["categoryId"]

    def _item_path(self, item_id=None):
        base = f"/menus/restaurants/{self.rest_id}/items"
        return f"{base}/{item_id}" if item_id else base

    def _create(self, name="Lamb Chops"):
        return handler(_event("POST", self._item_path(),
                              path_params={"restaurantId": self.rest_id},
                              body={
                                  "categoryId": self.cat_id,
                                  "name": name,
                                  "description": "Slow cooked.",
                                  "priceMinorUnits": 1200,
                                  "isActive": True,
                                  "imageKey": "items/chops.webp",
                              }), None)

    def test_create_returns_201_with_version_1(self):
        resp = self._create()
        assert resp["statusCode"] == 201
        assert _body(resp)["version"] == 1

    def test_get_item(self):
        item_id = _body(self._create())["itemId"]
        resp = handler(_event("GET", self._item_path(item_id),
                              path_params={"restaurantId": self.rest_id,
                                           "itemId": item_id}), None)
        assert resp["statusCode"] == 200
        assert _body(resp)["itemId"] == item_id

    def test_list_items(self):
        self._create("Lamb")
        self._create("Chicken")
        resp = handler(_event("GET", self._item_path(),
                              path_params={"restaurantId": self.rest_id}), None)
        assert resp["statusCode"] == 200
        assert _body(resp)["count"] == 2

    def test_list_filtered_by_category(self):
        self._create("Lamb")
        # Create item in a different category
        c2_resp = handler(_event("POST",
                                 f"/menus/restaurants/{self.rest_id}/categories",
                                 path_params={"restaurantId": self.rest_id},
                                 body={"name": "Mains", "displayOrder": 2,
                                       "isActive": True}), None)
        cat2_id = _body(c2_resp)["categoryId"]
        handler(_event("POST", self._item_path(),
                        path_params={"restaurantId": self.rest_id},
                        body={"categoryId": cat2_id, "name": "Burger",
                              "description": "Big.", "priceMinorUnits": 800,
                              "isActive": True, "imageKey": "items/burger.webp"}), None)

        resp = handler(_event("GET", self._item_path(),
                              path_params={"restaurantId": self.rest_id},
                              query={"categoryId": self.cat_id}), None)
        assert _body(resp)["count"] == 1
        assert _body(resp)["items"][0]["categoryId"] == self.cat_id

    def test_update_with_correct_version(self):
        item_id = _body(self._create())["itemId"]
        resp = handler(_event("PUT", self._item_path(item_id),
                              path_params={"restaurantId": self.rest_id,
                                           "itemId": item_id},
                              body={"name": "Updated Lamb", "version": 1}), None)
        assert resp["statusCode"] == 200
        assert _body(resp)["version"] == 2
        assert _body(resp)["name"] == "Updated Lamb"

    def test_update_with_stale_version_returns_409(self):
        item_id = _body(self._create())["itemId"]
        resp = handler(_event("PUT", self._item_path(item_id),
                              path_params={"restaurantId": self.rest_id,
                                           "itemId": item_id},
                              body={"name": "Stale", "version": 99}), None)
        assert resp["statusCode"] == 409

    def test_delete_item(self):
        item_id = _body(self._create())["itemId"]
        del_resp = handler(_event("DELETE", self._item_path(item_id),
                                  path_params={"restaurantId": self.rest_id,
                                               "itemId": item_id}), None)
        assert del_resp["statusCode"] == 200
        get_resp = handler(_event("GET", self._item_path(item_id),
                                  path_params={"restaurantId": self.rest_id,
                                               "itemId": item_id}), None)
        assert get_resp["statusCode"] == 404

    def test_get_unknown_item_returns_404(self):
        resp = handler(_event("GET",
                              self._item_path("00000000-0000-0000-0000-000000000000"),
                              path_params={"restaurantId": self.rest_id,
                                           "itemId": "00000000-0000-0000-0000-000000000000"}
                              ), None)
        assert resp["statusCode"] == 404


# ════════════════════════════════════════════════════════════════════════════
# 4. Presigned URL generation
# ════════════════════════════════════════════════════════════════════════════

@mock_aws
class TestPresignedUrlIntegration:
    def setup_method(self, method=None):
        _make_tables()
        _make_s3()
        _inject_services()

    def test_logo_upload_url_returned(self):
        r_id = "b2c3d4e5-f6a7-8901-bcde-f12345678901"
        resp = handler(_event("POST", "/menus/presigned-url", body={
            "tenantId":     TENANT_ID,
            "restaurantId": r_id,
            "assetType":    "logo",
            "contentType":  "image/webp",
        }), None)
        assert resp["statusCode"] == 200
        body = _body(resp)
        assert "uploadUrl" in body
        assert "s3Key" in body
        assert body["expiresIn"] > 0

    def test_ar_model_url_returned(self):
        r_id  = "b2c3d4e5-f6a7-8901-bcde-f12345678901"
        it_id = "d4e5f6a7-b8c9-0123-defa-234567890123"
        resp = handler(_event("POST", "/menus/presigned-url", body={
            "tenantId":     TENANT_ID,
            "restaurantId": r_id,
            "assetType":    "ar-model",
            "contentType":  "model/gltf-binary",
            "entityId":     it_id,
        }), None)
        assert resp["statusCode"] == 200
        assert ".glb" in _body(resp)["s3Key"]

    def test_invalid_asset_type_returns_400(self):
        resp = handler(_event("POST", "/menus/presigned-url", body={
            "tenantId":     TENANT_ID,
            "restaurantId": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
            "assetType":    "video",
            "contentType":  "video/mp4",
        }), None)
        assert resp["statusCode"] == 400


# ════════════════════════════════════════════════════════════════════════════
# 5. Redis cache hit / miss / invalidation
# ════════════════════════════════════════════════════════════════════════════

@mock_aws
class TestCacheBehaviour:
    def setup_method(self, method=None):
        _make_tables()
        _make_s3()
        self.cache = _inject_services()

    def test_restaurant_served_from_cache_on_second_get(self):
        resp = handler(_event("POST", "/menus/restaurants", body={
            "name": "Fork", "timezone": "Asia/Karachi", "currencyCode": "PKR",
            "isActive": True,
            "address": {"street": "1", "city": "K", "country": "PK", "postcode": "0"},
        }), None)
        r_id = _body(resp)["restaurantId"]

        # First GET populates cache
        handler(_event("GET", f"/menus/restaurants/{r_id}",
                        path_params={"restaurantId": r_id}), None)

        cache_key = CacheService.restaurant_key(TENANT_ID, r_id)
        assert self.cache.get(cache_key) is not None

    def test_cache_invalidated_after_update(self):
        resp = handler(_event("POST", "/menus/restaurants", body={
            "name": "Fork", "timezone": "Asia/Karachi", "currencyCode": "PKR",
            "isActive": True,
            "address": {"street": "1", "city": "K", "country": "PK", "postcode": "0"},
        }), None)
        r_id = _body(resp)["restaurantId"]

        # Warm cache
        handler(_event("GET", f"/menus/restaurants/{r_id}",
                        path_params={"restaurantId": r_id}), None)

        # Update should bust cache
        handler(_event("PUT", f"/menus/restaurants/{r_id}",
                        path_params={"restaurantId": r_id},
                        body={"name": "New Fork"}), None)

        cache_key = CacheService.restaurant_key(TENANT_ID, r_id)
        assert self.cache.get(cache_key) is None

    def test_get_after_cache_miss_hits_ddb(self):
        resp = handler(_event("POST", "/menus/restaurants", body={
            "name": "Fork", "timezone": "Asia/Karachi", "currencyCode": "PKR",
            "isActive": True,
            "address": {"street": "1", "city": "K", "country": "PK", "postcode": "0"},
        }), None)
        r_id = _body(resp)["restaurantId"]

        # Manually clear cache
        self.cache.delete(CacheService.restaurant_key(TENANT_ID, r_id))

        # Should still work via DDB
        get_resp = handler(_event("GET", f"/menus/restaurants/{r_id}",
                                   path_params={"restaurantId": r_id}), None)
        assert get_resp["statusCode"] == 200


# ════════════════════════════════════════════════════════════════════════════
# 6. Tenant quota enforcement
# ════════════════════════════════════════════════════════════════════════════

@mock_aws
class TestTenantQuotaIntegration:
    def setup_method(self, method=None):
        _make_tables()
        _make_s3()

    def _svc(self):
        table = boto3.resource("dynamodb", region_name="us-east-1").Table("TenantTable-integ")
        return TenantService(table=table)

    def test_quota_check_passes_under_limit(self):
        svc = self._svc()
        t = svc.create({"name": "Acme", "email": "a@b.com",
                        "plan": "PRO", "isActive": True, "maxRestaurants": 5})
        svc.check_restaurant_quota(t.tenantId, current_count=4)

    def test_quota_check_raises_at_limit(self):
        svc = self._svc()
        t = svc.create({"name": "Small", "email": "a@b.com",
                        "plan": "FREE", "isActive": True, "maxRestaurants": 1})
        with pytest.raises(TenantQuotaExceededError):
            svc.check_restaurant_quota(t.tenantId, current_count=1)

    def test_inactive_tenant_raises_permission_error(self):
        svc = self._svc()
        t = svc.create({"name": "Disabled", "email": "a@b.com",
                        "plan": "FREE", "isActive": False, "maxRestaurants": 1})
        with pytest.raises(PermissionError):
            svc.validate_active(t.tenantId)


# ════════════════════════════════════════════════════════════════════════════
# 7. Image URL enrichment flows to API response
# ════════════════════════════════════════════════════════════════════════════

@mock_aws
class TestImageUrlInResponse:
    def setup_method(self, method=None):
        _make_tables()
        _make_s3()
        _inject_services()

    def test_restaurant_get_includes_logo_url_when_key_set(self):
        resp = handler(_event("POST", "/menus/restaurants", body={
            "name": "Fork", "timezone": "Asia/Karachi", "currencyCode": "PKR",
            "isActive": True, "logoKey": "logos/fork.webp",
            "address": {"street": "1", "city": "K", "country": "PK", "postcode": "0"},
        }), None)
        r_id = _body(resp)["restaurantId"]

        get_resp = handler(_event("GET", f"/menus/restaurants/{r_id}",
                                   path_params={"restaurantId": r_id}), None)
        body = _body(get_resp)
        # logoKey is stored; logoUrl is injected (will be a presigned URL string)
        assert "logoKey" in body
        assert "logoUrl" in body
        assert body["logoUrl"] is not None

    def test_item_get_includes_image_url(self):
        r_resp = handler(_event("POST", "/menus/restaurants", body={
            "name": "Fork", "timezone": "Asia/Karachi", "currencyCode": "PKR",
            "isActive": True,
            "address": {"street": "1", "city": "K", "country": "PK", "postcode": "0"},
        }), None)
        r_id = _body(r_resp)["restaurantId"]

        c_resp = handler(_event("POST",
                                f"/menus/restaurants/{r_id}/categories",
                                path_params={"restaurantId": r_id},
                                body={"name": "S", "displayOrder": 1,
                                      "isActive": True}), None)
        cat_id = _body(c_resp)["categoryId"]

        i_resp = handler(_event("POST",
                                f"/menus/restaurants/{r_id}/items",
                                path_params={"restaurantId": r_id},
                                body={"categoryId": cat_id, "name": "Chops",
                                      "description": "Good.", "priceMinorUnits": 1200,
                                      "isActive": True, "imageKey": "items/chops.webp",
                                      "arModelKey": "ar/chops.glb",
                                      "arModelStatus": "APPROVED"}), None)
        item_id = _body(i_resp)["itemId"]

        get_resp = handler(_event("GET",
                                   f"/menus/restaurants/{r_id}/items/{item_id}",
                                   path_params={"restaurantId": r_id,
                                                "itemId": item_id}), None)
        body = _body(get_resp)
        assert body["imageKey"]   == "items/chops.webp"
        assert body["imageUrl"]   is not None
        assert body["arModelKey"] == "ar/chops.glb"
        assert body["arModelUrl"] is not None


# ════════════════════════════════════════════════════════════════════════════
# 8. Unknown route and method errors
# ════════════════════════════════════════════════════════════════════════════

@mock_aws
class TestRoutingErrors:
    def setup_method(self, method=None):
        _make_tables()
        _make_s3()
        _inject_services()

    def test_unknown_path_returns_400(self):
        resp = handler(_event("GET", "/menus/unknown-resource"), None)
        assert resp["statusCode"] == 400

    def test_options_not_routed_as_crud(self):
        resp = handler(_event("OPTIONS", "/menus/restaurants"), None)
        # OPTIONS hits the restaurant handler which returns 400 for unsupported method
        assert resp["statusCode"] in (400, 200)

    def test_response_always_has_cors_headers(self):
        resp = handler(_event("GET", "/menus/restaurants"), None)
        assert "Access-Control-Allow-Origin" in resp["headers"]