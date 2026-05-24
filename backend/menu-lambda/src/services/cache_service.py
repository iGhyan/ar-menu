"""
CacheService — Redis write-through cache with DynamoDB fallback.

If Redis is unavailable (e.g. localhost in dev), all operations
silently fall through to DynamoDB. No errors surface to the caller.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Optional

import redis

from utils.logger import get_logger

log = get_logger(__name__)

_REDIS_HOST    = os.environ.get("REDIS_HOST", "localhost")
_REDIS_PORT    = int(os.environ.get("REDIS_PORT", "6379"))
_REDIS_TIMEOUT = float(os.environ.get("REDIS_TIMEOUT", "1"))   # 1 second max
_DEFAULT_TTL   = int(os.environ.get("CACHE_TTL_SECONDS", "300"))


class CacheService:

    def __init__(self, client: Optional[redis.Redis] = None) -> None:
        self._client: Optional[redis.Redis] = client
        self._connected: bool = client is not None
        self._unavailable: bool = False   # set True after first failed connect

    def _get_client(self) -> Optional[redis.Redis]:
        # Skip immediately if already known unavailable
        if self._unavailable:
            return None

        if self._client is not None:
            return self._client

        try:
            self._client = redis.Redis(
                host=_REDIS_HOST,
                port=_REDIS_PORT,
                socket_timeout=_REDIS_TIMEOUT,
                socket_connect_timeout=_REDIS_TIMEOUT,
                decode_responses=True,
            )
            self._client.ping()
            self._connected  = True
            self._unavailable = False
            log.info("Redis connected", extra={"host": _REDIS_HOST})
        except Exception as exc:
            log.warning("Redis unavailable — cache disabled", extra={"error": str(exc)})
            self._client      = None
            self._connected   = False
            self._unavailable = True   # Don't retry on every request
        return self._client

    def get(self, key: str) -> Optional[dict]:
        client = self._get_client()
        if client is None:
            return None
        try:
            raw = client.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            log.warning("Redis GET error", extra={"key": key, "error": str(exc)})
            return None

    def set(self, key: str, value: dict, ttl: int = _DEFAULT_TTL) -> None:
        client = self._get_client()
        if client is None:
            return
        try:
            client.setex(key, ttl, json.dumps(value, default=str))
        except Exception as exc:
            log.warning("Redis SET error", extra={"key": key, "error": str(exc)})

    def delete(self, *keys: str) -> None:
        client = self._get_client()
        if client is None:
            return
        try:
            client.delete(*keys)
        except Exception as exc:
            log.warning("Redis DELETE error", extra={"keys": list(keys), "error": str(exc)})

    def get_or_load(
        self,
        key: str,
        loader: Callable[[], Optional[dict]],
        ttl: int = _DEFAULT_TTL,
    ) -> Optional[dict]:
        cached = self.get(key)
        if cached is not None:
            return cached
        result = loader()
        if result is not None:
            self.set(key, result, ttl)
        return result

    # ── Key builders ──────────────────────────────────────────────────────

    @staticmethod
    def restaurant_key(tenant_id: str, restaurant_id: str) -> str:
        return f"menu:restaurant:{tenant_id}:{restaurant_id}"

    @staticmethod
    def category_key(tenant_id: str, restaurant_id: str, category_id: str) -> str:
        return f"menu:category:{tenant_id}:{restaurant_id}:{category_id}"

    @staticmethod
    def categories_list_key(tenant_id: str, restaurant_id: str) -> str:
        return f"menu:categories:{tenant_id}:{restaurant_id}"

    @staticmethod
    def item_key(tenant_id: str, restaurant_id: str, item_id: str) -> str:
        return f"menu:item:{tenant_id}:{restaurant_id}:{item_id}"

    @staticmethod
    def items_list_key(tenant_id: str, restaurant_id: str) -> str:
        return f"menu:items:{tenant_id}:{restaurant_id}"