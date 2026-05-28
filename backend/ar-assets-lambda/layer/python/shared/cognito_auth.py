"""
shared/cognito_auth.py
=======================
Cognito JWT verification + RBAC helpers — shared Lambda Layer module.

Replaces the per-lambda `cognito_auth.py` file. Any Lambda that needs
Cognito auth imports from here — one place to update when pool config changes.

Security note
-------------
This implementation performs STRUCTURAL validation of the JWT (expiry, issuer,
client_id, token_use) using base64-decoded claims. It does NOT perform
cryptographic signature verification (RS256) against the JWKS public keys.

For production hardening, replace `_decode_payload` with a proper library
such as `python-jose` or `PyJWT[rsa]` added to the Layer's requirements.txt.

Usage
-----
    from shared.cognito_auth import CognitoAuth, UserContext
    from shared.exceptions import AuthError, ForbiddenError, RbacError

    auth = CognitoAuth()

    # In your handler:
    user: UserContext = auth.get_user_from_event(event)  # raises AuthError on failure
    auth.require_roles(user, ["menulay_admin", "menulay_tenant"])  # raises RbacError
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from shared.exceptions import (
    ForbiddenError,
    RbacError,
    TokenExpiredError,
    TokenInvalidError,
    TokenMissingError,
)
from shared.structured_logger import get_logger

_log = get_logger("cognito-auth")

# ── Cognito configuration (from env vars — overridable per environment) ────────
_REGION       = os.environ.get("COGNITO_REGION",      "ap-south-1")
_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "ap-south-1_SCyQ50etN")
_CLIENT_ID    = os.environ.get("COGNITO_CLIENT_ID",    "7903hkujl9qeq67toemi5qrhes")

_JWKS_URL = (
    f"https://cognito-idp.{_REGION}.amazonaws.com/"
    f"{_USER_POOL_ID}/.well-known/jwks.json"
)

_JWKS_CACHE_TTL = 3600  # 1 hour


# ── User context dataclass ────────────────────────────────────────────────────

@dataclass(frozen=True)
class UserContext:
    """
    Verified user identity extracted from a Cognito JWT.

    Attributes
    ----------
    sub       : Cognito user sub (unique user ID)
    email     : User email address (may be empty for access tokens)
    tenant_id : Custom attribute `custom:tenant_id` from the token
    groups    : Cognito group memberships (e.g. ["menulay_admin"])
    claims    : Full raw JWT claims dict
    """

    sub:       str
    email:     str
    tenant_id: str
    groups:    list[str] = field(default_factory=list)
    claims:    dict      = field(default_factory=dict, compare=False)

    def is_admin(self) -> bool:
        return "menulay_admin" in self.groups

    def is_tenant(self) -> bool:
        return "menulay_tenant" in self.groups

    def is_kitchen(self) -> bool:
        return "menulay_kitchen_staff" in self.groups

    def is_admin_or_tenant(self) -> bool:
        return self.is_admin() or self.is_tenant()

    def is_kitchen_or_admin(self) -> bool:
        return self.is_kitchen() or self.is_admin()

    def has_any_role(self, roles: list[str]) -> bool:
        return bool(set(self.groups) & set(roles))


# ── CognitoAuth service ───────────────────────────────────────────────────────

class CognitoAuth:
    """
    Verifies Cognito JWTs and extracts user identity.

    One instance per Lambda module (reused across warm invocations).
    The JWKS cache is per-instance — two instances do NOT share cache.
    For Lambda, instantiate once at module level.
    """

    def __init__(
        self,
        region:       str = _REGION,
        user_pool_id: str = _USER_POOL_ID,
        client_id:    str = _CLIENT_ID,
        jwks_ttl:     int = _JWKS_CACHE_TTL,
    ):
        self._region       = region
        self._user_pool_id = user_pool_id
        self._client_id    = client_id
        self._jwks_ttl     = jwks_ttl
        self._jwks_url     = (
            f"https://cognito-idp.{region}.amazonaws.com/"
            f"{user_pool_id}/.well-known/jwks.json"
        )
        self._jwks_cache:      dict | None = None
        self._jwks_cache_time: float       = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    def get_user_from_event(self, event: dict) -> UserContext:
        """
        Extract Authorization header, verify token, return UserContext.

        Raises
        ------
        TokenMissingError   — Authorization header absent
        TokenExpiredError   — token exp claim is in the past
        TokenInvalidError   — any other validation failure
        """
        token = self._extract_token(event)
        claims = self._verify_token(token)
        return self._build_user(claims)

    def require_roles(self, user: UserContext, roles: list[str]) -> None:
        """
        Assert that *user* belongs to at least one of *roles*.

        Raises
        ------
        RbacError — if the user has none of the required roles
        """
        if not user.has_any_role(roles):
            _log.warning(
                "rbac.denied",
                sub=user.sub,
                required_roles=roles,
                user_groups=user.groups,
            )
            raise RbacError(required_roles=roles, user_groups=user.groups)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _extract_token(self, event: dict) -> str:
        headers = event.get("headers") or {}
        token = (
            headers.get("Authorization")
            or headers.get("authorization")
            or ""
        ).strip()

        if not token:
            raise TokenMissingError()
        return token

    def _verify_token(self, token: str) -> dict:
        if token.startswith("Bearer "):
            token = token[7:]

        try:
            claims = _decode_payload(token)
        except Exception as exc:
            raise TokenInvalidError(reason=f"Could not decode token: {exc}") from exc

        # Expiry check
        if claims.get("exp", 0) < time.time():
            raise TokenExpiredError()

        # Issuer check
        expected_iss = (
            f"https://cognito-idp.{self._region}.amazonaws.com/{self._user_pool_id}"
        )
        if claims.get("iss") != expected_iss:
            raise TokenInvalidError(reason=f"Invalid issuer: {claims.get('iss')}")

        # Audience / client_id check
        token_client = claims.get("client_id") or claims.get("aud")
        if token_client != self._client_id:
            raise TokenInvalidError(reason="Client ID mismatch.")

        # token_use check
        if claims.get("token_use") not in ("id", "access"):
            raise TokenInvalidError(reason=f"Unexpected token_use: {claims.get('token_use')}")

        _log.info(
            "token.verified",
            sub=claims.get("sub", ""),
            groups=claims.get("cognito:groups", []),
        )
        return claims

    def _build_user(self, claims: dict) -> UserContext:
        return UserContext(
            sub=claims.get("sub", ""),
            email=claims.get("email", ""),
            tenant_id=claims.get("custom:tenant_id", ""),
            groups=claims.get("cognito:groups", []),
            claims=claims,
        )

    def _get_jwks(self) -> dict:
        """Fetch and cache JWKS from Cognito (for future signature verification)."""
        now = time.time()
        if self._jwks_cache and (now - self._jwks_cache_time) < self._jwks_ttl:
            return self._jwks_cache
        with urllib.request.urlopen(self._jwks_url, timeout=5) as res:
            self._jwks_cache = json.loads(res.read())
            self._jwks_cache_time = now
            _log.info("jwks.refreshed", url=self._jwks_url)
        return self._jwks_cache  # type: ignore[return-value]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _decode_payload(token: str) -> dict:
    """Base64-decode the JWT payload section (middle segment)."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("JWT must have exactly 3 dot-separated segments.")
    payload = parts[1]
    # Restore base64 padding
    payload += "=" * (4 - len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


# ── Module-level default instance ─────────────────────────────────────────────
# Lambdas that don't need custom config can import this directly:
#   from shared.cognito_auth import default_auth
default_auth = CognitoAuth()
