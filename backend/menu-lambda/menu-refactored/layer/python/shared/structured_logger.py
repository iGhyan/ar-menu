"""
shared/structured_logger.py
============================
JSON-structured logger for AWS Lambda / CloudWatch.

Usage
-----
    from shared.structured_logger import get_logger

    log = get_logger("glb-validator")
    log.info("file.processing.started", bucket=bucket, key=key, tenant_id=tid)
    log.warning("file.validation.rejected", error_code="INVALID_MAGIC", key=key)
    log.error("storage.read.failed", bucket=bucket, key=key, exc=str(e))

Each call emits a single-line JSON object to stdout — CloudWatch ingests it as
a structured log event, enabling Metric Filters, Insights queries, and Alarms
on any field (e.g. filter on error_code="INVALID_MAGIC").

Thread / Lambda safety
----------------------
- correlation_id is set once per invocation via `bind_correlation_id()` and
  stored in a threading.local so concurrent warm-start invocations don't bleed.
- `bind_lambda_context()` is a convenience wrapper for Lambda handlers.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from typing import Any

# ── Module-level threading.local for per-invocation context ──────────────────
_local = threading.local()


def bind_correlation_id(correlation_id: str) -> None:
    """Call at the start of each Lambda invocation with context.aws_request_id."""
    _local.correlation_id = correlation_id


def bind_lambda_context(context) -> None:
    """Convenience: extract request id and function metadata from Lambda context."""
    _local.correlation_id    = getattr(context, "aws_request_id", "unknown")
    _local.function_name     = getattr(context, "function_name", "unknown")
    _local.function_version  = getattr(context, "function_version", "unknown")


def _get_correlation_id() -> str:
    return getattr(_local, "correlation_id", "unknown")


# ── Structured logger class ───────────────────────────────────────────────────

class StructuredLogger:
    """
    Thin wrapper around stdlib logging that emits JSON records.

    Parameters
    ----------
    service : str
        Logical service name embedded in every log record.
    level   : int
        Minimum log level (default: logging.INFO).
    """

    LEVELS = {
        "debug":    logging.DEBUG,
        "info":     logging.INFO,
        "warning":  logging.WARNING,
        "error":    logging.ERROR,
        "critical": logging.CRITICAL,
    }

    def __init__(self, service: str, level: int = logging.INFO):
        self._service = service
        self._logger  = logging.getLogger(service)
        self._logger.setLevel(level)

        # Attach a stream handler only if none exists (avoid duplicate lines on
        # repeated imports in tests / warm containers).
        if not self._logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(_JsonFormatter())
            self._logger.addHandler(handler)
            self._logger.propagate = False

    # ── Public logging methods ────────────────────────────────────────────────

    def debug(self, event: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit(logging.INFO, event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._emit(logging.WARNING, event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit(logging.ERROR, event, **fields)

    def critical(self, event: str, **fields: Any) -> None:
        self._emit(logging.CRITICAL, event, **fields)

    def exception(self, event: str, exc: BaseException, **fields: Any) -> None:
        """Log at ERROR level with exception type and message."""
        self._emit(
            logging.ERROR, event,
            exc_type=type(exc).__name__,
            exc_message=str(exc),
            **fields,
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _emit(self, level: int, event: str, **fields: Any) -> None:
        record = {
            "level":          logging.getLevelName(level),
            "event":          event,
            "service":        self._service,
            "correlation_id": _get_correlation_id(),
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            **fields,
        }
        # Pass the pre-built dict as the message; _JsonFormatter will encode it.
        self._logger.log(level, record)


class _JsonFormatter(logging.Formatter):
    """Formats log records whose `msg` is already a dict into compact JSON."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        if isinstance(record.msg, dict):
            return json.dumps(record.msg, default=str)
        # Fallback for plain-string messages (e.g. from third-party libs)
        return json.dumps({
            "level":   record.levelname,
            "event":   record.getMessage(),
            "service": record.name,
        })


# ── Factory (preferred entry point) ──────────────────────────────────────────

_loggers: dict[str, StructuredLogger] = {}


def get_logger(service: str, level: int | None = None) -> StructuredLogger:
    """
    Return a cached StructuredLogger for *service*.

    Level defaults to the LOG_LEVEL env var (INFO if unset).
    """
    if service not in _loggers:
        if level is None:
            env_level = os.environ.get("LOG_LEVEL", "INFO").upper()
            level = getattr(logging, env_level, logging.INFO)
        _loggers[service] = StructuredLogger(service, level)
    return _loggers[service]
