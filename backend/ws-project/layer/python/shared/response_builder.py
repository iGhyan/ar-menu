"""
shared/response_builder.py
===========================
Standardised HTTP-style response envelope for Lambda functions.

Every response includes:
  - statusCode  : HTTP status integer
  - body        : JSON-serialisable dict with fixed schema
  - headers     : minimal CORS / content-type headers

Body schema
-----------
Success:
    {
      "success":    true,
      "data":       <any>,
      "message":    "...",
      "timestamp":  "2026-05-27T...",
      "request_id": "..."
    }

Error:
    {
      "success":    false,
      "error": {
        "code":    "VALIDATION_ERROR",
        "message": "File size exceeds 50 MB."
      },
      "timestamp":  "...",
      "request_id": "..."
    }

Partial (some records processed, some failed):
    {
      "success":    true,
      "data":       [ ... ],
      "summary": {
        "total":    5,
        "approved": 3,
        "rejected": 1,
        "skipped":  1
      },
      "timestamp":  "...",
      "request_id": "..."
    }

Usage
-----
    from shared.response_builder import ResponseBuilder

    return ResponseBuilder.success(data=results)
    return ResponseBuilder.error("FILE_SIZE_EXCEEDED", "File too large", status=422)
    return ResponseBuilder.partial(results)
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

_local = threading.local()


def bind_request_id(request_id: str) -> None:
    """Store the current Lambda request ID for injection into all responses."""
    _local.request_id = request_id


def _get_request_id() -> str:
    return getattr(_local, "request_id", "unknown")


# ── Default headers ───────────────────────────────────────────────────────────

_DEFAULT_HEADERS = {
    "Content-Type":                "application/json",
    "Access-Control-Allow-Origin": "*",
}


# ── Builder ───────────────────────────────────────────────────────────────────

class ResponseBuilder:
    """Static factory for Lambda response dicts."""

    @staticmethod
    def success(
        data: Any = None,
        message: str = "OK",
        status: int = 200,
        headers: dict | None = None,
    ) -> dict:
        """Return a 2xx response with optional data payload."""
        body: dict[str, Any] = {
            "success":    True,
            "message":    message,
            "timestamp":  _now_iso(),
            "request_id": _get_request_id(),
        }
        if data is not None:
            body["data"] = data

        return _envelope(status, body, headers)

    @staticmethod
    def error(
        error_code: str,
        message: str,
        status: int = 500,
        detail: dict | None = None,
        headers: dict | None = None,
    ) -> dict:
        """Return a 4xx / 5xx response with structured error information."""
        err: dict[str, Any] = {
            "code":    error_code,
            "message": message,
        }
        if detail:
            err["detail"] = detail

        body: dict[str, Any] = {
            "success":    False,
            "error":      err,
            "timestamp":  _now_iso(),
            "request_id": _get_request_id(),
        }
        return _envelope(status, body, headers)

    @staticmethod
    def from_exception(exc: Exception, headers: dict | None = None) -> dict:
        """
        Build an error response from an AppBaseException (or any exception).

        Imports lazily to avoid circular dependencies.
        """
        from shared.exceptions import AppBaseException  # noqa: PLC0415

        if isinstance(exc, AppBaseException):
            return ResponseBuilder.error(
                error_code=exc.error_code,
                message=exc.message,
                status=exc.http_status,
                detail=exc.context or None,
                headers=headers,
            )
        return ResponseBuilder.error(
            error_code="INTERNAL_ERROR",
            message=str(exc),
            status=500,
            headers=headers,
        )

    @staticmethod
    def partial(
        results: list[dict],
        status: int = 207,
        headers: dict | None = None,
    ) -> dict:
        """
        Return a 207 Multi-Status response for batch operations where some
        records succeeded and others failed.

        Each item in *results* is expected to have a "result" key with value
        "approved" | "rejected" | "skipped".
        """
        summary = {
            "total":    len(results),
            "approved": sum(1 for r in results if r.get("result") == "approved"),
            "rejected": sum(1 for r in results if r.get("result") == "rejected"),
            "skipped":  sum(1 for r in results if r.get("result") == "skipped"),
        }
        body: dict[str, Any] = {
            "success":    True,
            "data":       results,
            "summary":    summary,
            "timestamp":  _now_iso(),
            "request_id": _get_request_id(),
        }
        return _envelope(status, body, headers)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _envelope(status: int, body: dict, extra_headers: dict | None) -> dict:
    headers = {**_DEFAULT_HEADERS, **(extra_headers or {})}
    return {
        "statusCode": status,
        "headers":    headers,
        "body":       json.dumps(body, default=str),
    }
