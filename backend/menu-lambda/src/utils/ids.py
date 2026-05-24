"""
Utility helpers for ID and timestamp generation.
Centralised so tests can easily mock them.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone


def new_id() -> str:
    """Return a new lowercase UUID-4 string."""
    return str(uuid.uuid4())


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string ending in Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")