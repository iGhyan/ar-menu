from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)
_SOURCE = "orders-lambda"


def _put(events_client, bus_name: str, detail_type: str, detail: dict) -> None:
    response = events_client.put_events(
        Entries=[{"Source": _SOURCE, "DetailType": detail_type, "Detail": json.dumps(detail), "EventBusName": bus_name}]
    )
    if response.get("FailedEntryCount", 0):
        logger.error("EventBridge partial failure: %s", response["Entries"])


def publish_order_placed(events_client, bus_name, order_id, tenant_id, restaurant_id, table_id, execution_arn) -> None:
    try:
        _put(events_client, bus_name, "ORDER_PLACED", {
            "orderId": order_id, "tenantId": tenant_id,
            "restaurantId": restaurant_id, "tableId": table_id,
            "stepFunctionsExecutionArn": execution_arn,
        })
        logger.info("ORDER_PLACED event published: orderId=%s", order_id)
    except Exception:
        logger.warning("ORDER_PLACED event failed", exc_info=True)


def publish_order_failed(events_client, bus_name, order_id, tenant_id, error) -> None:
    try:
        _put(events_client, bus_name, "ORDER_FAILED", {
            "orderId": order_id, "tenantId": tenant_id, "error": error
        })
    except Exception:
        logger.warning("ORDER_FAILED event failed", exc_info=True)