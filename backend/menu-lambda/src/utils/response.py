"""
HTTP response builder for API Gateway Lambda proxy integration.
All responses share the same envelope; CORS headers always present.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any


class _ExtendedEncoder(json.JSONEncoder):
    """Handle Decimal and other non-serialisable types."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            # Return int when there is no fractional part, float otherwise
            return int(obj) if obj % 1 == 0 else float(obj)
        return super().default(obj)


_CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization,X-Tenant-Id",
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
}


def _build(status_code: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": _CORS_HEADERS,
        "body": json.dumps(body, cls=_ExtendedEncoder),
    }


# ── Success helpers ───────────────────────────────────────────────────────

def ok(data: Any) -> dict[str, Any]:
    """200 OK."""
    return _build(200, data)


def created(data: Any) -> dict[str, Any]:
    """201 Created."""
    return _build(201, data)


def no_content() -> dict[str, Any]:
    """204 No Content."""
    return {"statusCode": 204, "headers": _CORS_HEADERS, "body": ""}


# ── Error helpers ─────────────────────────────────────────────────────────

def bad_request(message: str, errors: dict | None = None) -> dict[str, Any]:
    """400 Bad Request."""
    body: dict[str, Any] = {"error": "BAD_REQUEST", "message": message}
    if errors:
        body["errors"] = errors
    return _build(400, body)


def not_found(resource: str) -> dict[str, Any]:
    """404 Not Found."""
    return _build(404, {"error": "NOT_FOUND", "message": f"{resource} not found"})


def conflict(message: str) -> dict[str, Any]:
    """409 Conflict — optimistic lock failure."""
    return _build(409, {"error": "CONFLICT", "message": message})


def unprocessable(message: str) -> dict[str, Any]:
    """422 Unprocessable Entity."""
    return _build(422, {"error": "UNPROCESSABLE", "message": message})


def internal_error(message: str = "Internal server error") -> dict[str, Any]:
    """500 Internal Server Error."""
    return _build(500, {"error": "INTERNAL_ERROR", "message": message})


def service_unavailable(message: str = "Service temporarily unavailable") -> dict[str, Any]:
    """503 Service Unavailable."""
    return _build(503, {"error": "SERVICE_UNAVAILABLE", "message": message})