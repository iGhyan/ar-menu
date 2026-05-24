"""
Centralised domain exception hierarchy.

All service-layer exceptions inherit from MenuDomainError so handlers
can catch the base type when needed, while still being able to
discriminate on the specific subclass for HTTP status mapping.

HTTP mapping (used by handlers):
  NotFoundError       → 404
  ConflictError       → 409
  ValidationError     → 400  (re-exported from models.base)
  AuthorisationError  → 403
  QuotaExceededError  → 429
  DependencyError     → 503
"""
from __future__ import annotations

from typing import Optional


class MenuDomainError(Exception):
    """Base class for all domain-layer errors."""

    def __init__(self, message: str, code: Optional[str] = None) -> None:
        self.message = message
        self.code = code or self.__class__.__name__
        super().__init__(message)


# ── 404 ───────────────────────────────────────────────────────────────────

class NotFoundError(MenuDomainError):
    """Resource does not exist."""


class RestaurantNotFoundError(NotFoundError):
    pass


class CategoryNotFoundError(NotFoundError):
    pass


class MenuItemNotFoundError(NotFoundError):
    pass


class TenantNotFoundError(NotFoundError):
    pass


# ── 409 ───────────────────────────────────────────────────────────────────

class ConflictError(MenuDomainError):
    """State conflict — e.g. optimistic lock version mismatch."""


class MenuItemConflictError(ConflictError):
    """Raised when a MenuItem update arrives with a stale version number."""


# ── 403 ───────────────────────────────────────────────────────────────────

class AuthorisationError(MenuDomainError):
    """Caller does not have permission to perform the operation."""


class TenantMismatchError(AuthorisationError):
    """Resource belongs to a different tenant."""


# ── 429 ───────────────────────────────────────────────────────────────────

class QuotaExceededError(MenuDomainError):
    """Tenant has exceeded a plan-level quota (e.g. max restaurants)."""


# ── 503 ───────────────────────────────────────────────────────────────────

class DependencyError(MenuDomainError):
    """An upstream dependency (DDB, S3, Redis) is unavailable."""


# ── Utility ───────────────────────────────────────────────────────────────

def is_not_found(exc: Exception) -> bool:
    """Return True for any NotFoundError subclass."""
    return isinstance(exc, NotFoundError)


def is_conflict(exc: Exception) -> bool:
    """Return True for any ConflictError subclass."""
    return isinstance(exc, ConflictError)


def http_status_for(exc: MenuDomainError) -> int:
    """Map a domain exception to its canonical HTTP status code."""
    if isinstance(exc, NotFoundError):
        return 404
    if isinstance(exc, ConflictError):
        return 409
    if isinstance(exc, AuthorisationError):
        return 403
    if isinstance(exc, QuotaExceededError):
        return 429
    if isinstance(exc, DependencyError):
        return 503
    return 500