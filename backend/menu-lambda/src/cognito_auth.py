"""
cognito_auth.py — same file as orders/ar lambda, copy to src/ folder
Place at: menu-lambda/src/cognito_auth.py
"""
import json
import time
import base64
import urllib.request
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger()

REGION       = 'ap-south-1'
USER_POOL_ID = 'ap-south-1_SCyQ50etN'
CLIENT_ID    = '7903hkujl9qeq67toemi5qrhes'

JWKS_URL = (
    f'https://cognito-idp.{REGION}.amazonaws.com/'
    f'{USER_POOL_ID}/.well-known/jwks.json'
)

_jwks_cache: Optional[Dict] = None
_jwks_cache_time: float = 0
JWKS_CACHE_TTL = 3600


def _get_jwks() -> Dict:
    global _jwks_cache, _jwks_cache_time
    now = time.time()
    if _jwks_cache and (now - _jwks_cache_time) < JWKS_CACHE_TTL:
        return _jwks_cache
    with urllib.request.urlopen(JWKS_URL, timeout=5) as res:
        _jwks_cache = json.loads(res.read())
        _jwks_cache_time = now
    return _jwks_cache


def _decode_payload(token: str) -> Dict:
    parts = token.split('.')
    if len(parts) != 3:
        raise ValueError('Invalid JWT format')
    payload = parts[1]
    payload += '=' * (4 - len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def verify_token(token: str) -> Dict[str, Any]:
    if not token:
        raise ValueError('No token provided')
    if token.startswith('Bearer '):
        token = token[7:]

    claims = _decode_payload(token)

    if claims.get('exp', 0) < time.time():
        raise ValueError('Token has expired')

    expected_iss = f'https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}'
    if claims.get('iss') != expected_iss:
        raise ValueError(f'Invalid issuer')

    token_client = claims.get('client_id') or claims.get('aud')
    if token_client != CLIENT_ID:
        raise ValueError('Invalid token client')

    if claims.get('token_use') not in ('id', 'access'):
        raise ValueError('Invalid token_use')

    return claims


def get_user_from_event(event: Dict) -> Dict[str, Any]:
    headers = event.get('headers') or {}
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


def is_admin(user: Dict) -> bool:
    return 'menulay_admin' in user.get('groups', [])

def is_tenant(user: Dict) -> bool:
    return 'menulay_tenant' in user.get('groups', [])

def is_admin_or_tenant(user: Dict) -> bool:
    return is_admin(user) or is_tenant(user)

def r_unauthorized(message: str = 'Unauthorized') -> Dict:
    return {
        'statusCode': 401,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
        },
        'body': json.dumps({'error': 'UNAUTHORIZED', 'message': message}),
    }

def r_forbidden(message: str = 'Forbidden') -> Dict:
    return {
        'statusCode': 403,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
        },
        'body': json.dumps({'error': 'FORBIDDEN', 'message': message}),
    }