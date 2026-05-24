"""
DynamoDB utility helpers.

- decimal_to_python : convert Decimal values returned by boto3 to int/float
- encode_lek / decode_lek : base64-encode DynamoDB LastEvaluatedKey for API clients
"""
from __future__ import annotations

import base64
import json
from decimal import Decimal
from typing import Any


def decimal_to_python(obj: Any) -> Any:
    """
    Recursively convert Decimal values in a DynamoDB item to int or float.
    boto3 returns all number attributes as Decimal.
    """
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    if isinstance(obj, dict):
        return {k: decimal_to_python(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decimal_to_python(v) for v in obj]
    return obj


def encode_lek(last_evaluated_key: dict | None) -> str | None:
    """Base64-encode a DynamoDB LastEvaluatedKey for API response."""
    if not last_evaluated_key:
        return None
    raw = json.dumps(last_evaluated_key, default=str)
    return base64.b64encode(raw.encode()).decode()


def decode_lek(encoded: str | None) -> dict | None:
    """Decode a base64 LastEvaluatedKey back to a DynamoDB exclusive start key."""
    if not encoded:
        return None
    try:
        raw = base64.b64decode(encoded.encode()).decode()
        return json.loads(raw)
    except Exception:
        return None


def build_update_expression(
    updates: dict[str, Any],
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """
    Build a DynamoDB UpdateExpression, ExpressionAttributeNames, and
    ExpressionAttributeValues from a flat dict of field→value.

    Returns:
        (update_expression, attr_names, attr_values)

    Example:
        updates = {"name": "New Name", "isActive": False}
        → "SET #n0 = :v0, #n1 = :v1"
    """
    parts: list[str] = []
    attr_names: dict[str, str] = {}
    attr_values: dict[str, Any] = {}

    for i, (field, value) in enumerate(updates.items()):
        name_key = f"#n{i}"
        val_key = f":v{i}"
        attr_names[name_key] = field
        attr_values[val_key] = value
        parts.append(f"{name_key} = {val_key}")

    return "SET " + ", ".join(parts), attr_names, attr_values