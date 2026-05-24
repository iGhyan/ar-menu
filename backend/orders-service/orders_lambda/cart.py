from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)
_redis_client = None


def _get_client():
    global _redis_client
    host = os.environ.get("REDIS_HOST", "")
    if not host or host == "localhost":
        return None
    if _redis_client is None:
        import redis
        _redis_client = redis.Redis(
            host=host,
            port=int(os.environ.get("REDIS_PORT", 6379)),
            socket_timeout=2,
            socket_connect_timeout=2,
            decode_responses=True,
        )
    return _redis_client


def clear_cart(tenant_id: str, table_id: str) -> None:
    client = _get_client()
    if client is None:
        logger.warning("Redis not configured — cart clear skipped")
        return
    cart_key = f"CART#{tenant_id}#{table_id}"
    try:
        client.delete(cart_key)
        logger.info("Cart cleared: key=%s", cart_key)
    except Exception:
        logger.warning("Redis cart clear failed: key=%s", cart_key, exc_info=True)