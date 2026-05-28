"""
src/cognito_auth.py
====================
Thin compatibility shim — delegates to shared.cognito_auth (Lambda Layer).

The router imports `get_user_from_event`, `is_admin_or_tenant`,
`r_unauthorized`, `r_forbidden` from here — same API as before,
now backed by the shared layer CognitoAuth class.
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, "/opt/python")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "layer", "python"))

import json
from typing import Any, Dict

from shared.cognito_auth import CognitoAuth, UserContext
from shared.exceptions import AuthError, ForbiddenError

_auth = CognitoAuth()

_CORS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}


def get_user_from_event(event: Dict) -> Dict[str, Any]:
    """
    Verify JWT from Authorization header and return a user info dict.
    Raises ValueError on auth failure (matches existing router contract).
    """
    try:
        user: UserContext = _auth.get_user_from_event(event)
        return {
            "sub":       user.sub,
            "email":     user.email,
            "tenant_id": user.tenant_id,
            "groups":    user.groups,
            "claims":    user.claims,
        }
    except AuthError as exc:
        raise ValueError(exc.message) from exc


def is_admin_or_tenant(user: Dict) -> bool:
    groups = user.get("groups", [])
    return "menulay_admin" in groups or "menulay_tenant" in groups


def r_unauthorized(message: str = "Unauthorized") -> Dict:
    return {
        "statusCode": 401,
        "headers": _CORS,
        "body": json.dumps({"error": "UNAUTHORIZED", "message": message}),
    }


def r_forbidden(message: str = "Forbidden") -> Dict:
    return {
        "statusCode": 403,
        "headers": _CORS,
        "body": json.dumps({"error": "FORBIDDEN", "message": message}),
    }
