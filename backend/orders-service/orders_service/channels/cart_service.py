"""
orders_service/channels/cart_service.py
========================================
CartService — clears the Redis cart on successful order placement.

Redis is OPTIONAL: if REDIS_HOST is unset or "localhost", the service
is a no-op. Failure is always logged and never propagates.
"""

from __future__ import annotations

import os
from typing import Optional

from shared.structured_logger import get_logger

_log          = get_logger("orders.cart")
_redis_client = None


def _get_redis_client():
    global _redis_client
    host = os.environ.get("REDIS_HOST", "")
    if not host or host == "localhost":
        return None
    if _redis_client is None:
        import redis  # noqa: PLC0415 — optional dependency
        _redis_client = redis.Redis(
            host=host,
            port=int(os.environ.get("REDIS_PORT", 6379)),
            socket_timeout=2,
            socket_connect_timeout=2,
            decode_responses=True,
        )
    return _redis_client


class CartService:
    """Clears a guest cart from Redis after a successful order placement."""

    def clear_cart(self, tenant_id: str, table_id: str) -> None:
        """
        Delete the cart key for this tenant + table.

        Never raises — cart clear is best-effort.
        """
        client = _get_redis_client()
        if client is None:
            _log.info("cart.clear.skipped", reason="Redis not configured")
            return

        cart_key = f"CART#{tenant_id}#{table_id}"
        try:
            client.delete(cart_key)
            _log.info("cart.cleared", cart_key=cart_key)
        except Exception as exc:  # noqa: BLE001
            _log.warning("cart.clear.failed", cart_key=cart_key, exc_message=str(exc))
