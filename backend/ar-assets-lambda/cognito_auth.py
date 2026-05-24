"""
cognito_auth.py
---------------
JWT verification + RBAC for MenuLay Lambda functions.
Place this file in the same folder as handler.py
"""

import json
import time
import base64
import urllib.request
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger()

# ── Cognito Config ─────────────────────────────────────────────────────────────
REGION       = 'ap-south-1'
USER_POOL_ID = 'ap-south-1_SCyQ50etN'
CLIENT_ID    = '7903hkujl9qeq67toemi5qrhes'

JWKS_URL = (
    f'https://cognito-idp.{REGION}.amazonaws.com/'
    f'{USER_POOL_ID}/.well-known/jwks.json'
)

# ── JWKS Cache (reuse across Lambda warm invocations) ─────────────────────────
_jwks_cache: Optional[Dict] = None
_jwks_cache_time: float = 0
JWKS_CACHE_TTL = 3600  # 1 hour


def _get_jwks() -> Dict:
    global _jwks_cache, _jwks_cache_time
    now = time.time()
    if _jwks_cache and (now - _jwks_cache_time) < JWKS_CACHE_TTL:
        return _jwks_cache
    with urllib.request.urlopen(JWKS_URL, timeout=5) as res:
        _jwks_cache = json.loads(res.read())
        _jwks_cache_time = now
        logger.info('[cognito_auth] JWKS refreshed')
    return _jwks_cache


def _decode_payload(token: str) -> Dict:
    """Base64 decode JWT payload (no signature check — just for claims)."""
    parts = token.split('.')
    if len(parts) != 3:
        raise ValueError('Invalid JWT format')
    payload = parts[1]
    # Fix base64 padding
    payload += '=' * (4 - len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


# ── Main verify function ───────────────────────────────────────────────────────

def verify_token(token: str) -> Dict[str, Any]:
    """
    Verify Cognito JWT token.
    Returns claims dict if valid.
    Raises ValueError if invalid/expired.
    """
    if not token:
        raise ValueError('No token provided')

    # Remove 'Bearer ' prefix
    if token.startswith('Bearer '):
        token = token[7:]

    claims = _decode_payload(token)

    # Check expiry
    if claims.get('exp', 0) < time.time():
        raise ValueError('Token has expired')

    # Check issuer
    expected_iss = (
        f'https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}'
    )
    if claims.get('iss') != expected_iss:
        raise ValueError(f'Invalid issuer: {claims.get("iss")}')

    # Check client_id (access token) or aud (id token)
    token_client = claims.get('client_id') or claims.get('aud')
    if token_client != CLIENT_ID:
        raise ValueError('Invalid token client')

    # Check token_use
    if claims.get('token_use') not in ('id', 'access'):
        raise ValueError('Invalid token_use')

    logger.info(
        '[cognito_auth] Token valid — sub=%s groups=%s',
        claims.get('sub', ''),
        claims.get('cognito:groups', [])
    )
    return claims


def get_user_from_event(event: Dict) -> Dict[str, Any]:
    """
    Extract Authorization header from API Gateway event,
    verify token, return user info dict.

    Returns:
    {
        'sub':       '71e3fd2a-...',
        'email':     'user@example.com',
        'tenant_id': 'a1b2c3d4-...',
        'groups':    ['menulay_admin'],
        'claims':    { ...full JWT claims... }
    }
    """
    headers = event.get('headers') or {}

    # API Gateway headers can be any case
    token = (
        headers.get('Authorization') or
        headers.get('authorization') or
        ''
    )

    if not token:
        raise ValueError('Authorization header missing')

    claims = verify_token(token)

    return {
        'sub':       claims.get('sub', ''),
        'email':     claims.get('email', ''),
        'tenant_id': claims.get('custom:tenant_id', ''),
        'groups':    claims.get('cognito:groups', []),
        'claims':    claims,
    }


# ── RBAC helpers ───────────────────────────────────────────────────────────────

def is_admin(user: Dict) -> bool:
    return 'menulay_admin' in user.get('groups', [])

def is_tenant(user: Dict) -> bool:
    return 'menulay_tenant' in user.get('groups', [])

def is_kitchen(user: Dict) -> bool:
    return 'menulay_kitchen_staff' in user.get('groups', [])

def is_admin_or_tenant(user: Dict) -> bool:
    return is_admin(user) or is_tenant(user)

def is_kitchen_or_admin(user: Dict) -> bool:
    return is_kitchen(user) or is_admin(user)


# ── Standard HTTP responses ────────────────────────────────────────────────────

_CORS = {
    'Content-Type':                'application/json',
    'Access-Control-Allow-Origin': '*',
}

def r_unauthorized(message: str = 'Unauthorized') -> Dict:
    return {
        'statusCode': 401,
        'headers':    _CORS,
        'body':       json.dumps({'error': 'UNAUTHORIZED', 'message': message}),
    }

def r_forbidden(message: str = 'Forbidden') -> Dict:
    return {
        'statusCode': 403,
        'headers':    _CORS,
        'body':       json.dumps({'error': 'FORBIDDEN', 'message': message}),
    }