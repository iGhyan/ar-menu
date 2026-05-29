"""
shared/error_handler.py
========================
Decorator-based error handling — eliminates repetitive try/except blocks.

The decorator:
1. Catches `ClientError` from botocore and converts it to a typed StorageError
   or NotificationError (based on the calling module).
2. Catches application-level `AppBaseException` subclasses and re-raises or
   returns a default depending on the `reraise` flag.
3. Logs every exception as a structured JSON record with full context.

Usage
-----
    from shared.error_handler import handle_errors
    from shared.exceptions import S3ReadError

    @handle_errors(reraise=False, default=(False, "storage error"))
    def read_glb_header(bucket, key):
        ...

    # For functions that SHOULD propagate exceptions upstream:
    @handle_errors(reraise=True)
    def critical_operation():
        ...
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, TypeVar

from botocore.exceptions import ClientError

from shared.exceptions import (
    AppBaseException,
    NotificationError,
    S3ReadError,
    S3WriteError,
    StorageError,
)
from shared.structured_logger import get_logger

_log = get_logger("error-handler")

F = TypeVar("F", bound=Callable[..., Any])


def handle_errors(
    reraise: bool = True,
    default: Any = None,
    log_level: str = "error",
) -> Callable[[F], F]:
    """
    Decorator factory for uniform error handling.

    Parameters
    ----------
    reraise : bool
        If True, exceptions propagate after logging.
        If False, *default* is returned instead.
    default : Any
        Value returned when reraise=False and an exception is caught.
    log_level : str
        Logging level for caught exceptions ('debug', 'info', 'warning', 'error').
    """

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)

            except AppBaseException as exc:
                _log_exception(log_level, fn.__name__, exc, exc.context)
                if reraise:
                    raise
                return default

            except ClientError as exc:
                typed = _convert_client_error(exc, args, kwargs)
                _log_exception(log_level, fn.__name__, typed, typed.context)
                if reraise:
                    raise typed from exc
                return default

            except Exception as exc:  # noqa: BLE001
                _log_exception("error", fn.__name__, exc, {})
                if reraise:
                    raise
                return default

        return wrapper  # type: ignore[return-value]

    return decorator


# ── Helpers ───────────────────────────────────────────────────────────────────

def _log_exception(level: str, fn_name: str, exc: Exception, context: dict) -> None:
    emit = getattr(_log, level, _log.error)
    emit(
        "exception.caught",
        function=fn_name,
        exc_type=type(exc).__name__,
        exc_message=str(exc),
        **context,
    )


def _convert_client_error(exc: ClientError, args: tuple, kwargs: dict) -> StorageError:
    """
    Map a botocore ClientError to a typed StorageError.

    Best-effort: tries to extract bucket/key from positional args or kwargs.
    Falls back to a generic StorageError if arguments are not recognisable.
    """
    error_code = exc.response.get("Error", {}).get("Code", "Unknown")
    cause      = f"AWS error code: {error_code} — {exc}"

    # Try to extract bucket / key heuristically
    bucket = kwargs.get("bucket") or (args[0] if args else "unknown")
    key    = kwargs.get("key")    or (args[1] if len(args) > 1 else "unknown")

    # SNS errors don't have bucket/key
    operation = exc.operation_name or ""
    if operation in ("Publish",):
        topic_arn = kwargs.get("topic_arn", "unknown")
        return NotificationError(topic_arn=str(topic_arn), cause=cause)

    if operation in ("GetObject", "HeadObject"):
        return S3ReadError(bucket=str(bucket), key=str(key), cause=cause)

    if operation in ("PutObject", "CopyObject", "DeleteObject"):
        return S3WriteError(bucket=str(bucket), key=str(key), cause=cause)

    return StorageError(message=cause)
