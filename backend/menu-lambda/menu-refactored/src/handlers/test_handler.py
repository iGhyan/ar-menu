"""Unit tests for src/handlers/."""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src"))

from handlers.request import parse_event, RequestContext
from handlers.restaurant_handler import handle_restaurant
from handlers.category_handler import handle_category
from handlers.item_handler import handle_menu_item
from handlers.presigned_handler import handle_presigned_url
from handlers.router import handler, _extract_path_params
from models.base import ValidationError
from models.restaurant import Restaurant
from models.category import MenuCategory
from models.menu_item import MenuItem
from models.schemas import PresignedUrlRequest
from services.restaurant_service import RestaurantNotFoundError
from services.category_service import CategoryNotFoundError
from services.menu_item_service import MenuItemNotFoundError, MenuItemConflictError

# ── Shared IDs ────────────────────────────────────────────────────────────

TENANT_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
REST_ID   = "b2c3d4e5-f6a7-8901-bcde-f12345678901"
CAT_ID    = "c3d4e5f6-a7b8-9012-cdef-123456789012"
ITEM_ID   = "d4e5f6a7-b8c9-0123-defa-234567890123"


# ── Event builders ────────────────────────────────────────────────────────

def _event(
    method: str = "GET",
    path: str = "/menus/restaurants",
    path_params: dict | None = None,
    query_params: dict | None = None,
    body: dict | None = None,
    tenant_id: str = TENANT_ID,
) -> dict:
    return {
        "httpMethod": method,
        "path": path,
        "pathParameters": path_params or {},
        "queryStringParameters": query_params or {},
        "headers": {"x-tenant-id": tenant_id},
        "body": json.dumps(body) if body else None,
    }


def _ctx(
    method: str = "GET",
    path: str = "/menus/restaurants",
    path_params: dict | None = None,
    query_params: dict | None = None,
    body: dict | None = None,
    tenant_id: str = TENANT_ID,
) -> RequestContext:
    return RequestContext(
        method=method,
        path=path,
        path_params=path_params or {},
        query_params=query_params or {},
        headers={"x-tenant-id": tenant_id},
        body=body or {},
        tenant_id=tenant_id,
    )


def _mock_restaurant() -> Restaurant:
    from models.address import Address
    return Restaurant(
        restaurantId=REST_ID,
        tenantId=TENANT_ID,
        name="The Golden Fork",
        address=Address("1 Main", "Karachi", "PK", "75000"),
        timezone="Asia/Karachi",
        currencyCode="PKR",
        isActive=True,
        createdAt="2025-04-20T10:00:00Z",
        updatedAt="2025-04-20T10:00:00Z",
    )


def _mock_category() -> MenuCategory:
    return MenuCategory(
        categoryId=CAT_ID,
        tenantId=TENANT_ID,
        restaurantId=REST_ID,
        name="Starters",
        displayOrder=1,
        isActive=True,
    )


def _mock_item() -> MenuItem:
    return MenuItem(
        itemId=ITEM_ID,
        tenantId=TENANT_ID,
        restaurantId=REST_ID,
        categoryId=CAT_ID,
        name="Crispy Lamb Chops",
        description="Slow-cooked.",
        priceMinorUnits=1200,
        isActive=True,
        version=1,
        createdAt="2025-04-20T10:00:00Z",
        updatedAt="2025-04-20T10:00:00Z",
        imageKey="items/r/lamb.webp",
    )


def _body(resp: dict) -> dict:
    return json.loads(resp["body"]) if resp.get("body") else {}


# ════════════════════════════════════════════════════════════════════════════
# parse_event
# ════════════════════════════════════════════════════════════════════════════

class TestParseEvent:
    def test_basic_get(self):
        ev = _event("GET", "/menus/restaurants", path_params={"restaurantId": REST_ID})
        ctx = parse_event(ev)
        assert ctx.method == "GET"
        assert ctx.path == "/menus/restaurants"
        assert ctx.path_params["restaurantId"] == REST_ID
        assert ctx.tenant_id == TENANT_ID

    def test_json_body_parsed(self):
        ev = _event("POST", body={"name": "Fork"})
        ctx = parse_event(ev)
        assert ctx.body["name"] == "Fork"

    def test_invalid_json_body_defaults_to_empty(self):
        ev = _event("POST")
        ev["body"] = "not-json"
        ctx = parse_event(ev)
        assert ctx.body == {}

    def test_non_dict_json_body_defaults_to_empty(self):
        ev = _event("POST")
        ev["body"] = json.dumps([1, 2, 3])
        ctx = parse_event(ev)
        assert ctx.body == {}

    def test_tenant_from_body_fallback(self):
        ev = _event(tenant_id="")
        ev["headers"] = {}
        ev["body"] = json.dumps({"tenantId": TENANT_ID})
        ctx = parse_event(ev)
        assert ctx.tenant_id == TENANT_ID

    def test_empty_tenant_when_none_provided(self):
        ev = _event(tenant_id="")
        ev["headers"] = {}
        ctx = parse_event(ev)
        assert ctx.tenant_id == ""

    def test_headers_lower_cased(self):
        ev = _event()
        ev["headers"] = {"X-Tenant-Id": TENANT_ID, "Content-Type": "application/json"}
        ctx = parse_event(ev)
        assert "x-tenant-id" in ctx.headers
        assert "content-type" in ctx.headers

    def test_none_path_params_defaults_to_empty(self):
        ev = _event()
        ev["pathParameters"] = None
        ctx = parse_event(ev)
        assert ctx.path_params == {}

    def test_none_query_params_defaults_to_empty(self):
        ev = _event()
        ev["queryStringParameters"] = None
        ctx = parse_event(ev)
        assert ctx.query_params == {}

    def test_none_body_defaults_to_empty(self):
        ev = _event()
        ev["body"] = None
        ctx = parse_event(ev)
        assert ctx.body == {}


# ════════════════════════════════════════════════════════════════════════════
# RestaurantHandler
# ════════════════════════════════════════════════════════════════════════════

class TestRestaurantHandler:
    def test_get_success(self):
        svc = MagicMock()
        svc.get.return_value = _mock_restaurant()
        ctx = _ctx("GET", path_params={"restaurantId": REST_ID})
        resp = handle_restaurant(ctx, svc)
        assert resp["statusCode"] == 200
        assert _body(resp)["restaurantId"] == REST_ID

    def test_get_missing_restaurant_id(self):
        svc = MagicMock()
        ctx = _ctx("GET", path_params={})
        resp = handle_restaurant(ctx, svc)
        assert resp["statusCode"] == 400

    def test_get_missing_tenant(self):
        svc = MagicMock()
        ctx = _ctx("GET", path_params={"restaurantId": REST_ID}, tenant_id="")
        resp = handle_restaurant(ctx, svc)
        assert resp["statusCode"] == 400

    def test_get_not_found(self):
        svc = MagicMock()
        svc.get.side_effect = RestaurantNotFoundError("not found")
        ctx = _ctx("GET", path_params={"restaurantId": REST_ID})
        resp = handle_restaurant(ctx, svc)
        assert resp["statusCode"] == 404

    def test_create_success(self):
        svc = MagicMock()
        svc.create.return_value = _mock_restaurant()
        ctx = _ctx("POST", body={"name": "Fork"})
        resp = handle_restaurant(ctx, svc)
        assert resp["statusCode"] == 201

    def test_create_missing_tenant(self):
        svc = MagicMock()
        ctx = _ctx("POST", tenant_id="")
        resp = handle_restaurant(ctx, svc)
        assert resp["statusCode"] == 400

    def test_create_validation_error(self):
        svc = MagicMock()
        svc.create.side_effect = ValidationError({"name": "required"})
        ctx = _ctx("POST", body={})
        resp = handle_restaurant(ctx, svc)
        assert resp["statusCode"] == 400
        assert "errors" in _body(resp)

    def test_update_success(self):
        svc = MagicMock()
        svc.update.return_value = _mock_restaurant()
        ctx = _ctx("PUT", path_params={"restaurantId": REST_ID}, body={"name": "New"})
        resp = handle_restaurant(ctx, svc)
        assert resp["statusCode"] == 200

    def test_update_missing_restaurant_id(self):
        svc = MagicMock()
        ctx = _ctx("PUT", path_params={})
        resp = handle_restaurant(ctx, svc)
        assert resp["statusCode"] == 400

    def test_delete_success(self):
        svc = MagicMock()
        ctx = _ctx("DELETE", path_params={"restaurantId": REST_ID})
        resp = handle_restaurant(ctx, svc)
        assert resp["statusCode"] == 200
        assert "deleted" in _body(resp)["message"].lower()

    def test_delete_missing_restaurant_id(self):
        svc = MagicMock()
        ctx = _ctx("DELETE", path_params={})
        resp = handle_restaurant(ctx, svc)
        assert resp["statusCode"] == 400

    def test_unsupported_method(self):
        svc = MagicMock()
        ctx = _ctx("PATCH")
        resp = handle_restaurant(ctx, svc)
        assert resp["statusCode"] == 400

    def test_unexpected_exception_returns_500(self):
        svc = MagicMock()
        svc.get.side_effect = RuntimeError("boom")
        ctx = _ctx("GET", path_params={"restaurantId": REST_ID})
        resp = handle_restaurant(ctx, svc)
        assert resp["statusCode"] == 500


# ════════════════════════════════════════════════════════════════════════════
# CategoryHandler
# ════════════════════════════════════════════════════════════════════════════

class TestCategoryHandler:
    def test_get_single(self):
        svc = MagicMock()
        svc.get.return_value = _mock_category()
        ctx = _ctx("GET", path_params={"restaurantId": REST_ID, "categoryId": CAT_ID})
        resp = handle_category(ctx, svc)
        assert resp["statusCode"] == 200
        assert _body(resp)["categoryId"] == CAT_ID

    def test_get_list(self):
        svc = MagicMock()
        svc.list.return_value = ([_mock_category()], None)
        ctx = _ctx("GET", path_params={"restaurantId": REST_ID})
        resp = handle_category(ctx, svc)
        assert resp["statusCode"] == 200
        assert _body(resp)["count"] == 1

    def test_get_list_with_cursor(self):
        svc = MagicMock()
        svc.list.return_value = ([], None)
        ctx = _ctx("GET",
                   path_params={"restaurantId": REST_ID},
                   query_params={"cursor": "abc123"})
        handle_category(ctx, svc)
        svc.list.assert_called_once_with(TENANT_ID, REST_ID, encoded_lek="abc123")

    def test_create_success(self):
        svc = MagicMock()
        svc.create.return_value = _mock_category()
        ctx = _ctx("POST",
                   path_params={"restaurantId": REST_ID},
                   body={"name": "Starters", "displayOrder": 1})
        resp = handle_category(ctx, svc)
        assert resp["statusCode"] == 201

    def test_update_success(self):
        svc = MagicMock()
        svc.update.return_value = _mock_category()
        ctx = _ctx("PUT",
                   path_params={"restaurantId": REST_ID, "categoryId": CAT_ID},
                   body={"name": "Desserts"})
        resp = handle_category(ctx, svc)
        assert resp["statusCode"] == 200

    def test_update_missing_category_id(self):
        svc = MagicMock()
        ctx = _ctx("PUT", path_params={"restaurantId": REST_ID})
        resp = handle_category(ctx, svc)
        assert resp["statusCode"] == 400

    def test_delete_success(self):
        svc = MagicMock()
        ctx = _ctx("DELETE",
                   path_params={"restaurantId": REST_ID, "categoryId": CAT_ID})
        resp = handle_category(ctx, svc)
        assert resp["statusCode"] == 200

    def test_delete_missing_category_id(self):
        svc = MagicMock()
        ctx = _ctx("DELETE", path_params={"restaurantId": REST_ID})
        resp = handle_category(ctx, svc)
        assert resp["statusCode"] == 400

    def test_not_found(self):
        svc = MagicMock()
        svc.get.side_effect = CategoryNotFoundError("not found")
        ctx = _ctx("GET", path_params={"restaurantId": REST_ID, "categoryId": CAT_ID})
        resp = handle_category(ctx, svc)
        assert resp["statusCode"] == 404

    def test_missing_tenant(self):
        svc = MagicMock()
        ctx = _ctx("GET", path_params={"restaurantId": REST_ID}, tenant_id="")
        resp = handle_category(ctx, svc)
        assert resp["statusCode"] == 400

    def test_missing_restaurant_id(self):
        svc = MagicMock()
        ctx = _ctx("GET", path_params={})
        resp = handle_category(ctx, svc)
        assert resp["statusCode"] == 400

    def test_unexpected_exception_returns_500(self):
        svc = MagicMock()
        svc.list.side_effect = RuntimeError("boom")
        ctx = _ctx("GET", path_params={"restaurantId": REST_ID})
        resp = handle_category(ctx, svc)
        assert resp["statusCode"] == 500


# ════════════════════════════════════════════════════════════════════════════
# ItemHandler
# ════════════════════════════════════════════════════════════════════════════

class TestItemHandler:
    def test_get_single(self):
        svc = MagicMock()
        svc.get.return_value = _mock_item()
        ctx = _ctx("GET", path_params={"restaurantId": REST_ID, "itemId": ITEM_ID})
        resp = handle_menu_item(ctx, svc)
        assert resp["statusCode"] == 200
        assert _body(resp)["itemId"] == ITEM_ID

    def test_get_list(self):
        svc = MagicMock()
        svc.list.return_value = ([_mock_item()], None)
        ctx = _ctx("GET", path_params={"restaurantId": REST_ID})
        resp = handle_menu_item(ctx, svc)
        assert resp["statusCode"] == 200
        assert _body(resp)["count"] == 1

    def test_get_list_with_category_filter(self):
        svc = MagicMock()
        svc.list.return_value = ([], None)
        ctx = _ctx("GET",
                   path_params={"restaurantId": REST_ID},
                   query_params={"categoryId": CAT_ID})
        handle_menu_item(ctx, svc)
        svc.list.assert_called_once_with(
            TENANT_ID, REST_ID, encoded_lek=None, category_id=CAT_ID
        )

    def test_create_success(self):
        svc = MagicMock()
        svc.create.return_value = _mock_item()
        ctx = _ctx("POST", path_params={"restaurantId": REST_ID}, body={"name": "x"})
        resp = handle_menu_item(ctx, svc)
        assert resp["statusCode"] == 201

    def test_update_success(self):
        svc = MagicMock()
        svc.update.return_value = _mock_item()
        ctx = _ctx("PUT",
                   path_params={"restaurantId": REST_ID, "itemId": ITEM_ID},
                   body={"name": "New", "version": 1})
        resp = handle_menu_item(ctx, svc)
        assert resp["statusCode"] == 200

    def test_update_conflict_returns_409(self):
        svc = MagicMock()
        svc.update.side_effect = MenuItemConflictError("version mismatch")
        ctx = _ctx("PUT",
                   path_params={"restaurantId": REST_ID, "itemId": ITEM_ID},
                   body={"version": 1})
        resp = handle_menu_item(ctx, svc)
        assert resp["statusCode"] == 409

    def test_update_missing_item_id(self):
        svc = MagicMock()
        ctx = _ctx("PUT", path_params={"restaurantId": REST_ID})
        resp = handle_menu_item(ctx, svc)
        assert resp["statusCode"] == 400

    def test_update_value_error_returns_400(self):
        svc = MagicMock()
        svc.update.side_effect = ValueError("version is required")
        ctx = _ctx("PUT",
                   path_params={"restaurantId": REST_ID, "itemId": ITEM_ID},
                   body={})
        resp = handle_menu_item(ctx, svc)
        assert resp["statusCode"] == 400

    def test_delete_success(self):
        svc = MagicMock()
        ctx = _ctx("DELETE",
                   path_params={"restaurantId": REST_ID, "itemId": ITEM_ID})
        resp = handle_menu_item(ctx, svc)
        assert resp["statusCode"] == 200

    def test_delete_missing_item_id(self):
        svc = MagicMock()
        ctx = _ctx("DELETE", path_params={"restaurantId": REST_ID})
        resp = handle_menu_item(ctx, svc)
        assert resp["statusCode"] == 400

    def test_not_found(self):
        svc = MagicMock()
        svc.get.side_effect = MenuItemNotFoundError("not found")
        ctx = _ctx("GET", path_params={"restaurantId": REST_ID, "itemId": ITEM_ID})
        resp = handle_menu_item(ctx, svc)
        assert resp["statusCode"] == 404

    def test_validation_error(self):
        svc = MagicMock()
        svc.create.side_effect = ValidationError({"name": "required"})
        ctx = _ctx("POST", path_params={"restaurantId": REST_ID}, body={})
        resp = handle_menu_item(ctx, svc)
        assert resp["statusCode"] == 400

    def test_unexpected_exception_returns_500(self):
        svc = MagicMock()
        svc.get.side_effect = RuntimeError("boom")
        ctx = _ctx("GET", path_params={"restaurantId": REST_ID, "itemId": ITEM_ID})
        resp = handle_menu_item(ctx, svc)
        assert resp["statusCode"] == 500


# ════════════════════════════════════════════════════════════════════════════
# PresignedHandler
# ════════════════════════════════════════════════════════════════════════════

class TestPresignedHandler:
    def _valid_body(self):
        return {
            "tenantId": TENANT_ID,
            "restaurantId": REST_ID,
            "assetType": "logo",
            "contentType": "image/webp",
        }

    def test_success(self):
        svc = MagicMock()
        svc.generate_presigned_url.return_value = {
            "uploadUrl": "https://s3.example.com/key",
            "s3Key": "TENANT#.../logo.webp",
            "expiresIn": 900,
        }
        ctx = _ctx("POST", body=self._valid_body())
        resp = handle_presigned_url(ctx, svc)
        assert resp["statusCode"] == 200
        assert "uploadUrl" in _body(resp)

    def test_wrong_method(self):
        svc = MagicMock()
        ctx = _ctx("GET")
        resp = handle_presigned_url(ctx, svc)
        assert resp["statusCode"] == 400

    def test_validation_error(self):
        svc = MagicMock()
        svc.generate_presigned_url.side_effect = ValidationError({"assetType": "required"})
        ctx = _ctx("POST", body={})
        resp = handle_presigned_url(ctx, svc)
        assert resp["statusCode"] == 400

    def test_unexpected_exception_returns_500(self):
        svc = MagicMock()
        svc.generate_presigned_url.side_effect = RuntimeError("s3 error")
        ctx = _ctx("POST", body=self._valid_body())
        resp = handle_presigned_url(ctx, svc)
        assert resp["statusCode"] == 500


# ════════════════════════════════════════════════════════════════════════════
# Router
# ════════════════════════════════════════════════════════════════════════════

class TestRouter:
    def _patch_services(self, rest=None, cat=None, item=None, s3=None):
        """Return a patcher that injects mock services."""
        return patch(
            "handlers.router._get_services",
            return_value=(
                rest or MagicMock(),
                cat  or MagicMock(),
                item or MagicMock(),
                s3   or MagicMock(),
            ),
        )

    def test_routes_restaurant_get(self):
        rest_svc = MagicMock()
        rest_svc.get.return_value = _mock_restaurant()
        with self._patch_services(rest=rest_svc):
            resp = handler(
                _event("GET",
                       f"/menus/restaurants/{REST_ID}",
                       path_params={"restaurantId": REST_ID}),
                None,
            )
        assert resp["statusCode"] == 200

    def test_routes_restaurant_post(self):
        rest_svc = MagicMock()
        rest_svc.create.return_value = _mock_restaurant()
        with self._patch_services(rest=rest_svc):
            resp = handler(
                _event("POST", "/menus/restaurants",
                       body={"name": "Fork", "timezone": "Asia/Karachi"}),
                None,
            )
        assert resp["statusCode"] == 201

    def test_routes_categories(self):
        cat_svc = MagicMock()
        cat_svc.list.return_value = ([], None)
        with self._patch_services(cat=cat_svc):
            resp = handler(
                _event("GET",
                       f"/menus/restaurants/{REST_ID}/categories",
                       path_params={"restaurantId": REST_ID}),
                None,
            )
        assert resp["statusCode"] == 200

    def test_routes_items(self):
        item_svc = MagicMock()
        item_svc.list.return_value = ([], None)
        with self._patch_services(item=item_svc):
            resp = handler(
                _event("GET",
                       f"/menus/restaurants/{REST_ID}/items",
                       path_params={"restaurantId": REST_ID}),
                None,
            )
        assert resp["statusCode"] == 200

    def test_routes_presigned_url(self):
        s3_svc = MagicMock()
        s3_svc.generate_presigned_url.return_value = {
            "uploadUrl": "https://s3.example.com",
            "s3Key": "k",
            "expiresIn": 900,
        }
        with self._patch_services(s3=s3_svc):
            resp = handler(
                _event("POST", "/menus/presigned-url",
                       body={
                           "tenantId": TENANT_ID,
                           "restaurantId": REST_ID,
                           "assetType": "logo",
                           "contentType": "image/webp",
                       }),
                None,
            )
        assert resp["statusCode"] == 200

    def test_unknown_route_returns_400(self):
        with self._patch_services():
            resp = handler(_event("GET", "/menus/unknown-path"), None)
        assert resp["statusCode"] == 400

    def test_fatal_exception_returns_500(self):
        with patch("handlers.router._get_services", side_effect=RuntimeError("fatal")):
            resp = handler(_event("GET", "/menus/restaurants"), None)
        assert resp["statusCode"] == 500

    def test_path_param_fallback_restaurant(self):
        params = _extract_path_params(f"/menus/restaurants/{REST_ID}")
        assert params["restaurantId"] == REST_ID

    def test_path_param_fallback_category(self):
        params = _extract_path_params(
            f"/menus/restaurants/{REST_ID}/categories/{CAT_ID}"
        )
        assert params["restaurantId"] == REST_ID
        assert params["categoryId"] == CAT_ID

    def test_path_param_fallback_item(self):
        params = _extract_path_params(
            f"/menus/restaurants/{REST_ID}/items/{ITEM_ID}"
        )
        assert params["restaurantId"] == REST_ID
        assert params["itemId"] == ITEM_ID

    def test_path_param_fallback_unknown(self):
        params = _extract_path_params("/menus/unknown")
        assert params == {}

    def test_singleton_services_reused(self):
        """_get_services must return same objects on repeated calls."""
        import handlers.router as router_mod
        # Reset singletons
        router_mod._cache = None
        router_mod._restaurant_svc = None
        router_mod._category_svc   = None
        router_mod._item_svc       = None
        router_mod._s3_svc         = None

        with patch("handlers.router.CacheService") as mock_cache_cls, \
             patch("handlers.router.RestaurantService") as mock_rest_cls, \
             patch("handlers.router.CategoryService")   as mock_cat_cls, \
             patch("handlers.router.MenuItemService")   as mock_item_cls, \
             patch("handlers.router.S3Service")         as mock_s3_cls:

            mock_cache_cls.return_value = MagicMock()
            mock_rest_cls.return_value  = MagicMock()
            mock_cat_cls.return_value   = MagicMock()
            mock_item_cls.return_value  = MagicMock()
            mock_s3_cls.return_value    = MagicMock()

            r1, c1, i1, s1 = router_mod._get_services()
            r2, c2, i2, s2 = router_mod._get_services()

        assert r1 is r2
        assert c1 is c2
        assert i1 is i2
        assert s1 is s2
        # Constructors called only once
        mock_rest_cls.assert_called_once()