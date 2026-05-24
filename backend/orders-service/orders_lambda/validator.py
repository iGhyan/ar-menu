from __future__ import annotations

import logging
from typing import List

from boto3.dynamodb.types import TypeDeserializer

from models import LineItem

logger = logging.getLogger(__name__)
_deserializer = TypeDeserializer()


class ValidationError(Exception):
    pass


def _deserialize(raw: dict) -> dict:
    return {k: _deserializer.deserialize(v) for k, v in raw.items()}


def validate_menu_items(dynamodb_client, menu_table: str, restaurant_id: str, line_items: List[LineItem]) -> None:
    keys = [
        {"PK": {"S": f"RESTAURANT#{restaurant_id}"}, "SK": {"S": f"ITEM#{item.itemId}"}}
        for item in line_items
    ]

    response = dynamodb_client.batch_get_item(RequestItems={menu_table: {"Keys": keys}})

    fetched = {
        _deserialize(raw)["SK"].replace("ITEM#", ""): _deserialize(raw)
        for raw in response.get("Responses", {}).get(menu_table, [])
    }

    errors = []
    for item in line_items:
        menu_item = fetched.get(item.itemId)
        if not menu_item:
            errors.append(f"Item {item.itemId} not found in menu")
            continue
        if not menu_item.get("available", False):
            errors.append(f"Item {item.itemId} is currently unavailable")
            continue
        menu_price = int(menu_item.get("priceMinorUnits", 0))
        if menu_price != item.unitPriceMinorUnits:
            errors.append(f"Item {item.itemId} price mismatch: expected {menu_price}, got {item.unitPriceMinorUnits}")

    if errors:
        raise ValidationError("; ".join(errors))