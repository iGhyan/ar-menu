"""
Retry decorator with exponential backoff + full jitter.

Usage:
    @retry(retries=3, base_delay=0.1, exceptions=(ClientError,))
    def call_dynamo():
        ...

DynamoDB throttle errors use error code "ProvisionedThroughputExceededException"
or "RequestLimitExceeded"; both are caught via the generic ClientError.
"""
from __future__ import annotations

import random
import time
from functools import wraps
from typing import Callable, Tuple, Type

from utils.logger import get_logger

log = get_logger(__name__)

_DEFAULT_RETRYABLE_CODES = frozenset({
    "ProvisionedThroughputExceededException",
    "RequestLimitExceeded",
    "ThrottlingException",
    "ServiceUnavailable",
    "InternalServerError",
})


def _is_retryable(exc: Exception, retryable_codes: frozenset) -> bool:
    """Return True if the botocore ClientError is retryable."""
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
    return code in retryable_codes


def retry(
    retries: int = 3,
    base_delay: float = 0.1,
    max_delay: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    retryable_codes: frozenset = _DEFAULT_RETRYABLE_CODES,
) -> Callable:
    """
    Decorator: retry *retries* times with full-jitter exponential backoff.

    Jitter formula: sleep = random(0, min(max_delay, base_delay * 2^attempt))
    This avoids thundering-herd on simultaneous Lambda throttles.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc: Exception | None = None

            for attempt in range(retries + 1):
                try:
                    return func(*args, **kwargs)

                except exceptions as exc:
                    # For ClientError subclasses check the error code
                    if hasattr(exc, "response") and not _is_retryable(exc, retryable_codes):
                        raise

                    last_exc = exc

                    if attempt == retries:
                        log.error(
                            "Max retries reached",
                            extra={
                                "function": func.__name__,
                                "attempt": attempt,
                                "error": str(exc),
                            },
                        )
                        raise

                    cap = min(max_delay, base_delay * (2 ** attempt))
                    sleep = random.uniform(0, cap)

                    log.warning(
                        "Retryable error — backing off",
                        extra={
                            "function": func.__name__,
                            "attempt": attempt,
                            "sleep_s": round(sleep, 3),
                            "error": str(exc),
                        },
                    )
                    time.sleep(sleep)

            raise last_exc  # pragma: no cover

        return wrapper
    return decorator