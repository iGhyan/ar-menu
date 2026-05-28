"""tests/integration/test_router.py
Integration tests for the menu-lambda router.
Services are mocked — no real DDB/S3/Redis calls.
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch

from tests.conftest import (
    api_event, make_jwt,
    TENANT_ID, RESTAURANT_ID, CATEGORY_ID, ITEM_ID,
    RESTAURANT_BODY, CATEGORY_BODY, ITEM_BODY,
)


# ── Mock service factory ───────────────────────────────────────────────────────

def _mock_restaurant(restaurant_id=RESTAURANT_ID, name="Test Restaurant"):
    r = MagicMock()
    r.restaurantId = restaurant_id
    r.to_dict.return_value = {"restaurantId": restaurant_id, "name": name}
    return r


def _mock_category(category_id=CATEGORY_ID, name="Burgers"):
    c = MagicMock()
    c.categoryId = category_id
    c.to_dict.return_value = {"categoryId": category_id, "name": name}
    return c


def _mock_item(item_id=ITEM_ID, name="Burger"):
    i = MagicMock()
    i.itemId = item_id
    i.to_dict.return_value = {"itemId": item_id, "name": name}
    return i


def _invoke(event, mock_restaurant_svc=None, mock_category_svc=None,
            mock_item_svc=None, mock_s3_svc=None, mock_s3_repo=None):
    """Invoke router.handler with all services mocked."""
    mock_cache    = MagicMock()
    mock_cache.get.return_value = None
    mock_cache.get_or_load.side_effect = lambda key, loader, ttl=300: loader()

    _restaurant_svc = mock_restaurant_svc or MagicMock()
    _category_svc   = mock_category_svc   or MagicMock()
    _item_svc       = mock_item_svc        or MagicMock()
    _s3_svc         = mock_s3_svc          or MagicMock()
    _s3_repo        = mock_s3_repo          or MagicMock()

    with patch("handlers.router._cache",          mock_cache), \
         patch("handlers.router._restaurant_svc", _restaurant_svc), \
         patch("handlers.router._category_svc",   _category_svc), \
         patch("handlers.router._item_svc",        _item_svc), \
         patch("handlers.router._s3_svc",          _s3_svc), \
         patch("handlers.router._s3_repo",         _s3_repo):
        from handlers.router import handler
        return handler(event, {})


# ══════════════════════════════════════════════════════════════════════════════
# RESTAURANT routes
# ══════════════════════════════════════════════════════════════════════════════

class TestRestaurantRoutes:
    def test_get_restaurant_returns_200(self):
        mock_svc = MagicMock()
        mock_svc.get.return_value = _mock_restaurant()
        event = api_event(
            "GET",
            f"/menus/restaurants/{RESTAURANT_ID}",
            path_params={"restaurantId": RESTAURANT_ID},
            tenant_id=TENANT_ID,
        )
        resp = _invoke(event, mock_restaurant_svc=mock_svc)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["restaurantId"] == RESTAURANT_ID

    def test_list_restaurants_returns_200(self):
        mock_svc = MagicMock()
        mock_svc.list_all.return_value = ([_mock_restaurant()], None)
        event = api_event("GET", "/menus/restaurants", tenant_id=TENANT_ID)
        resp  = _invoke(event, mock_restaurant_svc=mock_svc)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert "items" in body
        assert body["count"] == 1

    def test_post_restaurant_requires_auth(self):
        event = api_event("POST", "/menus/restaurants", body=RESTAURANT_BODY,
                          tenant_id=TENANT_ID)  # No token
        resp  = _invoke(event)
        assert resp["statusCode"] == 401

    def test_post_restaurant_with_admin_token_returns_201(self):
        mock_svc = MagicMock()
        mock_svc.create.return_value = _mock_restaurant()
        token = make_jwt(groups=["menulay_admin"], tenant_id=TENANT_ID)
        event = api_event("POST", "/menus/restaurants",
                          body=RESTAURANT_BODY, token=token)
        resp  = _invoke(event, mock_restaurant_svc=mock_svc)
        assert resp["statusCode"] == 201

    def test_post_restaurant_with_tenant_token_returns_201(self):
        mock_svc = MagicMock()
        mock_svc.create.return_value = _mock_restaurant()
        token = make_jwt(groups=["menulay_tenant"], tenant_id=TENANT_ID)
        event = api_event("POST", "/menus/restaurants",
                          body=RESTAURANT_BODY, token=token)
        resp  = _invoke(event, mock_restaurant_svc=mock_svc)
        assert resp["statusCode"] == 201

    def test_post_restaurant_kitchen_staff_forbidden(self):
        token = make_jwt(groups=["menulay_kitchen_staff"], tenant_id=TENANT_ID)
        event = api_event("POST", "/menus/restaurants",
                          body=RESTAURANT_BODY, token=token)
        resp  = _invoke(event)
        assert resp["statusCode"] == 403

    def test_put_restaurant_requires_auth(self):
        event = api_event("PUT", f"/menus/restaurants/{RESTAURANT_ID}",
                          body={"name": "Updated"},
                          path_params={"restaurantId": RESTAURANT_ID},
                          tenant_id=TENANT_ID)
        resp = _invoke(event)
        assert resp["statusCode"] == 401

    def test_put_restaurant_with_auth_returns_200(self):
        mock_svc = MagicMock()
        mock_svc.update.return_value = _mock_restaurant(name="Updated")
        token = make_jwt(groups=["menulay_admin"], tenant_id=TENANT_ID)
        event = api_event("PUT", f"/menus/restaurants/{RESTAURANT_ID}",
                          body={"name": "Updated"}, token=token,
                          path_params={"restaurantId": RESTAURANT_ID})
        resp = _invoke(event, mock_restaurant_svc=mock_svc)
        assert resp["statusCode"] == 200

    def test_delete_restaurant_with_auth_returns_200(self):
        mock_svc = MagicMock()
        mock_svc.delete.return_value = None
        token = make_jwt(groups=["menulay_admin"], tenant_id=TENANT_ID)
        event = api_event("DELETE", f"/menus/restaurants/{RESTAURANT_ID}",
                          token=token,
                          path_params={"restaurantId": RESTAURANT_ID},
                          tenant_id=TENANT_ID)
        resp = _invoke(event, mock_restaurant_svc=mock_svc)
        assert resp["statusCode"] == 200

    def test_get_missing_tenant_id_returns_400(self):
        event = api_event("GET", f"/menus/restaurants/{RESTAURANT_ID}",
                          path_params={"restaurantId": RESTAURANT_ID})
        # No tenant_id header
        resp = _invoke(event)
        assert resp["statusCode"] == 400

    def test_unknown_route_returns_400(self):
        event = api_event("GET", "/menus/unknown-path", tenant_id=TENANT_ID)
        resp  = _invoke(event)
        assert resp["statusCode"] == 400

    def test_expired_token_returns_401(self):
        token = make_jwt(expired=True, groups=["menulay_admin"])
        event = api_event("POST", "/menus/restaurants",
                          body=RESTAURANT_BODY, token=token)
        resp = _invoke(event)
        assert resp["statusCode"] == 401


# ══════════════════════════════════════════════════════════════════════════════
# CATEGORY routes
# ══════════════════════════════════════════════════════════════════════════════

class TestCategoryRoutes:
    def test_get_category_returns_200(self):
        mock_svc = MagicMock()
        mock_svc.get.return_value = _mock_category()
        event = api_event(
            "GET",
            f"/menus/restaurants/{RESTAURANT_ID}/categories/{CATEGORY_ID}",
            path_params={"restaurantId": RESTAURANT_ID, "categoryId": CATEGORY_ID},
            tenant_id=TENANT_ID,
        )
        resp = _invoke(event, mock_category_svc=mock_svc)
        assert resp["statusCode"] == 200

    def test_list_categories_returns_200(self):
        mock_svc = MagicMock()
        mock_svc.list.return_value = ([_mock_category()], None)
        event = api_event(
            "GET",
            f"/menus/restaurants/{RESTAURANT_ID}/categories",
            path_params={"restaurantId": RESTAURANT_ID},
            tenant_id=TENANT_ID,
        )
        resp = _invoke(event, mock_category_svc=mock_svc)
        assert resp["statusCode"] == 200

    def test_post_category_with_auth_returns_201(self):
        mock_svc = MagicMock()
        mock_svc.create.return_value = _mock_category()
        token = make_jwt(groups=["menulay_admin"], tenant_id=TENANT_ID)
        event = api_event(
            "POST",
            f"/menus/restaurants/{RESTAURANT_ID}/categories",
            body=CATEGORY_BODY, token=token,
            path_params={"restaurantId": RESTAURANT_ID},
        )
        resp = _invoke(event, mock_category_svc=mock_svc)
        assert resp["statusCode"] == 201

    def test_post_category_without_auth_returns_401(self):
        event = api_event(
            "POST",
            f"/menus/restaurants/{RESTAURANT_ID}/categories",
            body=CATEGORY_BODY,
            path_params={"restaurantId": RESTAURANT_ID},
            tenant_id=TENANT_ID,
        )
        resp = _invoke(event)
        assert resp["statusCode"] == 401

    def test_delete_category_with_auth_returns_200(self):
        mock_svc = MagicMock()
        mock_svc.delete.return_value = None
        token = make_jwt(groups=["menulay_admin"], tenant_id=TENANT_ID)
        event = api_event(
            "DELETE",
            f"/menus/restaurants/{RESTAURANT_ID}/categories/{CATEGORY_ID}",
            token=token, tenant_id=TENANT_ID,
            path_params={"restaurantId": RESTAURANT_ID, "categoryId": CATEGORY_ID},
        )
        resp = _invoke(event, mock_category_svc=mock_svc)
        assert resp["statusCode"] == 200


# ══════════════════════════════════════════════════════════════════════════════
# ITEM routes
# ══════════════════════════════════════════════════════════════════════════════

class TestItemRoutes:
    def test_get_item_returns_200(self):
        mock_svc = MagicMock()
        mock_svc.get.return_value = _mock_item()
        event = api_event(
            "GET",
            f"/menus/restaurants/{RESTAURANT_ID}/items/{ITEM_ID}",
            path_params={"restaurantId": RESTAURANT_ID, "itemId": ITEM_ID},
            tenant_id=TENANT_ID,
        )
        resp = _invoke(event, mock_item_svc=mock_svc)
        assert resp["statusCode"] == 200

    def test_list_items_returns_200(self):
        mock_svc = MagicMock()
        mock_svc.list.return_value = ([_mock_item()], None)
        event = api_event(
            "GET",
            f"/menus/restaurants/{RESTAURANT_ID}/items",
            path_params={"restaurantId": RESTAURANT_ID},
            tenant_id=TENANT_ID,
        )
        resp = _invoke(event, mock_item_svc=mock_svc)
        assert resp["statusCode"] == 200

    def test_post_item_with_auth_returns_201(self):
        mock_svc = MagicMock()
        mock_svc.create.return_value = _mock_item()
        token = make_jwt(groups=["menulay_admin"], tenant_id=TENANT_ID)
        event = api_event(
            "POST",
            f"/menus/restaurants/{RESTAURANT_ID}/items",
            body=ITEM_BODY, token=token,
            path_params={"restaurantId": RESTAURANT_ID},
        )
        resp = _invoke(event, mock_item_svc=mock_svc)
        assert resp["statusCode"] == 201

    def test_post_item_without_auth_returns_401(self):
        event = api_event(
            "POST",
            f"/menus/restaurants/{RESTAURANT_ID}/items",
            body=ITEM_BODY,
            path_params={"restaurantId": RESTAURANT_ID},
            tenant_id=TENANT_ID,
        )
        resp = _invoke(event)
        assert resp["statusCode"] == 401

    def test_put_item_with_auth_returns_200(self):
        mock_svc = MagicMock()
        mock_svc.update.return_value = _mock_item(name="Updated Burger")
        token = make_jwt(groups=["menulay_admin"], tenant_id=TENANT_ID)
        event = api_event(
            "PUT",
            f"/menus/restaurants/{RESTAURANT_ID}/items/{ITEM_ID}",
            body={"name": "Updated Burger"}, token=token,
            path_params={"restaurantId": RESTAURANT_ID, "itemId": ITEM_ID},
        )
        resp = _invoke(event, mock_item_svc=mock_svc)
        assert resp["statusCode"] == 200

    def test_delete_item_with_auth_returns_200(self):
        mock_svc = MagicMock()
        mock_svc.delete.return_value = None
        token = make_jwt(groups=["menulay_admin"], tenant_id=TENANT_ID)
        event = api_event(
            "DELETE",
            f"/menus/restaurants/{RESTAURANT_ID}/items/{ITEM_ID}",
            token=token, tenant_id=TENANT_ID,
            path_params={"restaurantId": RESTAURANT_ID, "itemId": ITEM_ID},
        )
        resp = _invoke(event, mock_item_svc=mock_svc)
        assert resp["statusCode"] == 200


# ══════════════════════════════════════════════════════════════════════════════
# PRESIGNED URL route
# ══════════════════════════════════════════════════════════════════════════════

class TestPresignedRoute:
    def test_presigned_requires_auth(self):
        event = api_event("POST", "/menus/presigned-url",
                          body={"tenantId": TENANT_ID, "restaurantId": RESTAURANT_ID,
                                "assetType": "logo", "contentType": "image/webp"})
        resp = _invoke(event)
        assert resp["statusCode"] == 401

    def test_presigned_with_auth_calls_s3_service(self):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = {
            "url": "https://s3.example.com/presigned",
            "key": "TENANT#t1/restaurants/r1/logo.webp",
        }
        token = make_jwt(groups=["menulay_admin"], tenant_id=TENANT_ID)
        event = api_event(
            "POST", "/menus/presigned-url",
            body={
                "tenantId":     TENANT_ID,
                "restaurantId": RESTAURANT_ID,
                "assetType":    "logo",
                "contentType":  "image/webp",
            },
            token=token,
        )
        resp = _invoke(event, mock_s3_svc=mock_s3)
        assert resp["statusCode"] == 200
        mock_s3.generate_presigned_url.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# RBAC and JWT edge cases
# ══════════════════════════════════════════════════════════════════════════════

class TestAuthRbac:
    def test_tenant_id_overridden_from_jwt(self):
        """tenant_id in ctx should come from JWT, not X-Tenant-Id header."""
        mock_svc = MagicMock()
        mock_svc.create.return_value = _mock_restaurant()

        jwt_tenant = "jwt-tenant-overrides"
        token = make_jwt(groups=["menulay_admin"], tenant_id=jwt_tenant)
        event = api_event(
            "POST", "/menus/restaurants",
            body=RESTAURANT_BODY, token=token,
            tenant_id="header-tenant-ignored",  # should be overridden
        )
        _invoke(event, mock_restaurant_svc=mock_svc)
        call_tenant = mock_svc.create.call_args[0][0]
        assert call_tenant == jwt_tenant

    def test_wrong_issuer_returns_401(self):
        token = make_jwt(groups=["menulay_admin"], issuer="https://evil.com/fake")
        event = api_event("POST", "/menus/restaurants",
                          body=RESTAURANT_BODY, token=token)
        resp = _invoke(event)
        assert resp["statusCode"] == 401

    def test_wrong_client_id_returns_401(self):
        token = make_jwt(groups=["menulay_admin"], client_id="wrong-client")
        event = api_event("POST", "/menus/restaurants",
                          body=RESTAURANT_BODY, token=token)
        resp = _invoke(event)
        assert resp["statusCode"] == 401

    def test_get_is_public_no_auth_needed(self):
        mock_svc = MagicMock()
        mock_svc.list_all.return_value = ([], None)
        # No token, no X-Tenant-Id — BUT tenant_id is required for the handler
        event = api_event("GET", "/menus/restaurants", tenant_id=TENANT_ID)
        resp = _invoke(event, mock_restaurant_svc=mock_svc)
        # GET is public — no 401 from router
        assert resp["statusCode"] == 200
