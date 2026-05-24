from __future__ import annotations

import logging
from decimal import Decimal

from botocore.exceptions import ClientError

from models import OrderRecord

logger = logging.getLogger(__name__)


class DuplicateOrderError(Exception):
    pass


def _to_dynamo_types(obj: dict) -> dict:
    result = {}
    for k, v in obj.items():
        if isinstance(v, float):
            result[k] = Decimal(str(v))
        elif isinstance(v, list):
            result[k] = [_to_dynamo_types(i) if isinstance(i, dict) else i for i in v]
        elif isinstance(v, dict):
            result[k] = _to_dynamo_types(v)
        else:
            result[k] = v
    return result


def write_order(dynamodb_resource, table_name: str, record: OrderRecord) -> None:
    table = dynamodb_resource.Table(table_name)
    item = _to_dynamo_types(record.to_dynamo_item())
    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
        logger.info("Order written: orderId=%s", record.orderId)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise DuplicateOrderError(record.orderId) from exc
        raise


def rollback_order(dynamodb_resource, table_name: str, record: OrderRecord) -> None:
    try:
        dynamodb_resource.Table(table_name).delete_item(Key={"PK": record.PK, "SK": record.SK})
        logger.info("Rollback succeeded: orderId=%s", record.orderId)
    except Exception:
        logger.exception("Rollback failed for orderId=%s", record.orderId)