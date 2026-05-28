"""
orders_service/repositories/order_repository.py
================================================
OrderRepository — all DynamoDB operations for orders.

Encapsulates:
  - write_order    (PUT with conditional)
  - rollback_order (DELETE on SFN failure)
  - get_order      (query by PK)
  - update_status  (SET status + updatedAt)
  - list_orders    (GSI query by restaurantId + placedAt range)
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from orders_service.models import OrderRecord, clean_decimals, to_dynamo_types
from shared.exceptions import ResourceNotFoundError, S3WriteError
from shared.structured_logger import get_logger

_log = get_logger("orders.repository")


class DuplicateOrderError(Exception):
    """Raised when an order with the same PK already exists."""
    pass


class OrderRepository:
    """
    DynamoDB repository for order records.

    Parameters
    ----------
    dynamodb_resource : boto3 DynamoDB resource
    dynamodb_client   : boto3 DynamoDB client (for batch_get_item)
    table_name        : OrderTable name
    """

    def __init__(self, dynamodb_resource, dynamodb_client, table_name: str):
        self._resource   = dynamodb_resource
        self._client     = dynamodb_client
        self._table_name = table_name

    @property
    def _table(self):
        return self._resource.Table(self._table_name)

    # ── Write ─────────────────────────────────────────────────────────────────

    def write_order(self, record: OrderRecord) -> None:
        """
        Write a new order record to DynamoDB.

        Raises
        ------
        DuplicateOrderError — PK already exists
        ClientError         — any other DynamoDB error
        """
        item = to_dynamo_types(record.to_dynamo_item())
        try:
            self._table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(PK)",
            )
            _log.info("order.written", order_id=record.orderId)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise DuplicateOrderError(record.orderId) from exc
            _log.error(
                "order.write.failed",
                order_id=record.orderId,
                error_code=exc.response["Error"]["Code"],
            )
            raise

    def rollback_order(self, record: OrderRecord) -> None:
        """Delete an order record — used to rollback after SFN failure."""
        try:
            self._table.delete_item(Key={"PK": record.PK, "SK": record.SK})
            _log.info("order.rollback.success", order_id=record.orderId)
        except Exception as exc:  # noqa: BLE001
            _log.error("order.rollback.failed", order_id=record.orderId, exc_message=str(exc))

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_order(self, order_id: str, tenant_id: str) -> Optional[dict]:
        """
        Fetch a single order by orderId + tenantId.

        Returns the item dict or None if not found.
        """
        pk  = f"TENANT#{tenant_id}#ORDER#{order_id}"
        res = self._table.query(
            KeyConditionExpression=Key("PK").eq(pk),
            Limit=1,
        )
        items = res.get("Items", [])
        return items[0] if items else None

    def list_orders(
        self,
        restaurant_id: str,
        tenant_id:     str,
        hours:         int = 4,
    ) -> list[dict]:
        """
        List recent orders for a restaurant via GSI-1.

        Filters by tenantId in memory (GSI doesn't include tenantId as key).
        """
        from_time = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        res = self._table.query(
            IndexName="GSI-1-restaurant-orders",
            KeyConditionExpression=(
                Key("restaurantId").eq(restaurant_id)
                & Key("placedAt").gte(from_time)
            ),
        )
        return [
            o for o in res.get("Items", [])
            if o.get("tenantId") == tenant_id
        ]

    # ── Update ────────────────────────────────────────────────────────────────

    def update_status(self, order_id: str, tenant_id: str, status: str) -> None:
        """
        Update the status and updatedAt fields for an order.

        Raises
        ------
        ResourceNotFoundError — order does not exist
        ClientError           — DynamoDB error
        """
        order = self.get_order(order_id, tenant_id)
        if not order:
            raise ResourceNotFoundError(resource="Order", identifier=order_id)

        updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            self._table.update_item(
                Key={"PK": order["PK"], "SK": order["SK"]},
                UpdateExpression="SET #st = :s, updatedAt = :u",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={":s": status, ":u": updated_at},
            )
            _log.info("order.status.updated", order_id=order_id, status=status)
        except ClientError as exc:
            _log.error(
                "order.status.update.failed",
                order_id=order_id,
                status=status,
                error_code=exc.response["Error"]["Code"],
            )
            raise
