"""
Router — Lambda handler entry point with Cognito JWT auth + RBAC.

RBAC:
  GET  /menus/...  → Public (guests read menu)
  POST/PUT/DELETE  → Admin or Tenant only
  upload/presigned → Admin or Tenant only
  tables           → GET public, POST/PUT/DELETE admin/tenant
"""
from __future__ import annotations

import re
from typing import Any

from handlers.request import parse_event
from handlers.restaurant_handler import handle_restaurant
from handlers.category_handler import handle_category
from handlers.item_handler import handle_menu_item
from handlers.table_handler import handle_table
from handlers.presigned_handler import handle_presigned_url
from handlers.upload_handler import handle_upload
from services.cache_service import CacheService
from services.restaurant_service import RestaurantService
from services.category_service import CategoryService
from services.menu_item_service import MenuItemService
from services.s3_service import S3Service
from repository.s3 import S3Repository
from utils.logger import get_logger
from utils.response import bad_request, internal_error

from cognito_auth import (
    get_user_from_event,
    is_admin_or_tenant,
    r_unauthorized,
    r_forbidden,
)

log = get_logger(__name__)

_ROUTES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^/menus/presigned-url$"),                                             "presigned"),
    (re.compile(r"^/menus/upload/restaurants/[^/]+/logo$"),                             "upload"),
    (re.compile(r"^/menus/upload/restaurants/[^/]+/categories/[^/]+/image$"),           "upload"),
    (re.compile(r"^/menus/upload/restaurants/[^/]+/items/[^/]+/image$"),                "upload"),
    (re.compile(r"^/menus/upload/restaurants/[^/]+/items/[^/]+/ar-model$"),             "upload"),
    # ── Table routes (before restaurant to avoid conflict) ────────────────────
    (re.compile(r"^/menus/restaurants/[^/]+/tables/[^/]+$"),                            "table"),
    (re.compile(r"^/menus/restaurants/[^/]+/tables$"),                                  "table"),
    # ── Other routes ──────────────────────────────────────────────────────────
    (re.compile(r"^/menus/restaurants/[^/]+/categories/[^/]+$"),                        "category"),
    (re.compile(r"^/menus/restaurants/[^/]+/categories$"),                              "category"),
    (re.compile(r"^/menus/restaurants/[^/]+/items/[^/]+$"),                             "item"),
    (re.compile(r"^/menus/restaurants/[^/]+/items$"),                                   "item"),
    (re.compile(r"^/menus/restaurants/[^/]+$"),                                         "restaurant"),
    (re.compile(r"^/menus/restaurants$"),                                               "restaurant"),
]

_cache:          CacheService | None = None
_restaurant_svc: RestaurantService | None = None
_category_svc:   CategoryService | None = None
_item_svc:       MenuItemService | None = None
_s3_svc:         S3Service | None = None
_s3_repo:        S3Repository | None = None


def _get_services():
    global _cache, _restaurant_svc, _category_svc, _item_svc, _s3_svc, _s3_repo
    if _cache is None:          _cache          = CacheService()
    if _s3_svc is None:         _s3_svc         = S3Service()
    if _s3_repo is None:        _s3_repo        = S3Repository()
    if _restaurant_svc is None: _restaurant_svc = RestaurantService(cache=_cache, s3_svc=_s3_svc)
    if _category_svc is None:   _category_svc   = CategoryService(cache=_cache, s3_svc=_s3_svc)
    if _item_svc is None:       _item_svc       = MenuItemService(cache=_cache, s3_svc=_s3_svc)
    return _restaurant_svc, _category_svc, _item_svc, _s3_svc, _s3_repo


def _extract_path_params(path: str) -> dict[str, str]:
    patterns = [
        re.compile(r"^/menus/upload/restaurants/(?P<restaurantId>[^/]+)/categories/(?P<categoryId>[^/]+)/image$"),
        re.compile(r"^/menus/upload/restaurants/(?P<restaurantId>[^/]+)/items/(?P<itemId>[^/]+)/image$"),
        re.compile(r"^/menus/upload/restaurants/(?P<restaurantId>[^/]+)/items/(?P<itemId>[^/]+)/ar-model$"),
        re.compile(r"^/menus/upload/restaurants/(?P<restaurantId>[^/]+)/logo$"),
        # ── Table params ──────────────────────────────────────────────────────
        re.compile(r"^/menus/restaurants/(?P<restaurantId>[^/]+)/tables/(?P<tableId>[^/]+)$"),
        re.compile(r"^/menus/restaurants/(?P<restaurantId>[^/]+)/tables$"),
        # ── Other params ──────────────────────────────────────────────────────
        re.compile(r"^/menus/restaurants/(?P<restaurantId>[^/]+)/categories/(?P<categoryId>[^/]+)$"),
        re.compile(r"^/menus/restaurants/(?P<restaurantId>[^/]+)/categories$"),
        re.compile(r"^/menus/restaurants/(?P<restaurantId>[^/]+)/items/(?P<itemId>[^/]+)$"),
        re.compile(r"^/menus/restaurants/(?P<restaurantId>[^/]+)/items$"),
        re.compile(r"^/menus/restaurants/(?P<restaurantId>[^/]+)$"),
    ]
    for p in patterns:
        m = p.match(path)
        if m:
            return m.groupdict()
    return {}


_WRITE_METHODS        = {"POST", "PUT", "DELETE", "PATCH"}
_AUTH_REQUIRED_ROUTES = {"presigned", "upload"}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    log.info("Request received", extra={
        "method": event.get("httpMethod"),
        "path":   event.get("path"),
    })

    try:
        ctx = parse_event(event)
        fallback = _extract_path_params(ctx.path)
        ctx.path_params = {**fallback, **ctx.path_params}

        route_key: str | None = None
        for pattern, key in _ROUTES:
            if pattern.match(ctx.path):
                route_key = key
                break

        if route_key is None:
            return bad_request(f"Unknown route: {ctx.path}")

        # ── Auth check ────────────────────────────────────────────────────────
        needs_auth = (
            ctx.method in _WRITE_METHODS or
            route_key in _AUTH_REQUIRED_ROUTES
        )

        if needs_auth:
            try:
                user = get_user_from_event(event)
            except ValueError as e:
                log.warning("Auth failed: %s", str(e))
                return r_unauthorized(str(e))
            except Exception as e:
                log.error("Auth error: %s", str(e))
                return r_unauthorized("Invalid or expired token")

            if not is_admin_or_tenant(user):
                return r_forbidden("Only admin or tenant can modify menu")

            jwt_tenant = user.get("tenant_id")
            if jwt_tenant:
                ctx.tenant_id = jwt_tenant
                log.info("tenant_id from JWT: %s", jwt_tenant)

        # ── Route to handler ─────────────────────────────────────────────────
        restaurant_svc, category_svc, item_svc, s3_svc, s3_repo = _get_services()

        if route_key == "presigned":  return handle_presigned_url(ctx, s3_svc)
        if route_key == "upload":     return handle_upload(ctx, s3_repo, event)
        if route_key == "restaurant": return handle_restaurant(ctx, restaurant_svc, s3_repo)
        if route_key == "category":   return handle_category(ctx, category_svc, s3_repo)
        if route_key == "item":       return handle_menu_item(ctx, item_svc, s3_repo)
        if route_key == "table":      return handle_table(ctx)

        return bad_request(f"Unknown route: {ctx.path}")

    except Exception as exc:
        log.error("Fatal error in router", extra={"error": str(exc)})
        return internal_error()