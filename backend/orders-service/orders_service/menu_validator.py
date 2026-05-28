"""
orders_service/menu_validator.py
=================================
Validates order line items against the MenuTable in DynamoDB.

Uses BatchGetItem for efficiency — one request for all items in the order.
"""

from __future__ import annotations

from typing import List

from boto3.dynamodb.types import TypeDeserializer

from orders_service.models import LineItem
from shared.exceptions import ValidationError
from shared.structured_logger import get_logger

_log          = get_logger("orders.menu-validator")
_deserializer = TypeDeserializer()


class MenuValidationError(ValidationError):
    """Raised when one or more line items fail menu validation."""
    error_code  = "MENU_VALIDATION_FAILED"
    http_status = 422


def _deserialize(raw: dict) -> dict:
    return {k: _deserializer.deserialize(v) for k, v in raw.items()}


def validate_menu_items(
    dynamodb_client,
    menu_table:    str,
    restaurant_id: str,
    line_items:    List[LineItem],
) -> None:
    """
    Validate all line items against the menu table.

    Raises
    ------
    MenuValidationError  — one or more items are invalid (price mismatch,
                           not found, or unavailable)
    botocore ClientError — DynamoDB unavailable (caller maps to 503)
    """
    keys = [
        {
            "PK": {"S": f"RESTAURANT#{restaurant_id}"},
            "SK": {"S": f"ITEM#{item.itemId}"},
        }
        for item in line_items
    ]

    response = dynamodb_client.batch_get_item(
        RequestItems={menu_table: {"Keys": keys}}
    )

    fetched = {
        _deserialize(raw)["SK"].replace("ITEM#", ""): _deserialize(raw)
        for raw in response.get("Responses", {}).get(menu_table, [])
    }

    errors: list[str] = []
    for item in line_items:
        menu_item = fetched.get(item.itemId)

        if not menu_item:
            errors.append(f"Item {item.itemId!r} not found in menu")
            continue

        if not menu_item.get("available", False):
            errors.append(f"Item {item.itemId!r} is currently unavailable")
            continue

        menu_price = int(menu_item.get("priceMinorUnits", 0))
        if menu_price != item.unitPriceMinorUnits:
            errors.append(
                f"Item {item.itemId!r} price mismatch: "
                f"expected {menu_price}, got {item.unitPriceMinorUnits}"
            )

    if errors:
        _log.warning(
            "menu.validation.failed",
            restaurant_id=restaurant_id,
            errors=errors,
        )
        raise MenuValidationError("; ".join(errors))

    _log.info(
        "menu.validation.passed",
        restaurant_id=restaurant_id,
        item_count=len(line_items),
    )
