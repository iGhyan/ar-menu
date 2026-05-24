"""
step_functions.py — Starts the order-processing Step Functions state machine.

Passes the full order payload as input so every state has access
to orderId, tenantId, restaurantId, tableId etc.

The state machine uses these fields to:
  - Update DynamoDB at each transition (orderId + tenantId)
  - Publish EventBridge events (restaurantId + tableId)
  - Route through Choice states (kitchenAccepted, foodReady, delivered, cancelled)
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def start_execution(sfn_client, state_machine_arn: str, order_id: str, request) -> str:
    """
    Starts a Step Functions execution.
    Returns the executionArn string.
    Raises botocore.exceptions.ClientError on failure (caller handles rollback).
    """
    payload = {
        "orderId":               order_id,
        "tenantId":              request.tenantId,
        "restaurantId":          request.restaurantId,
        "tableId":               request.tableId,
        "totalAmountMinorUnits": request.totalAmountMinorUnits,
        "currencyCode":          request.currencyCode,
        "lineItems":             [item.dict() for item in request.lineItems],
        "guestConnectionId":     request.guestConnectionId,

        # Choice state flags — updated by kitchen/delivery apps via SendTaskSuccess
        "kitchenAccepted": False,
        "foodReady":       False,
        "delivered":       False,
        "cancelled":       False,
    }

    response = sfn_client.start_execution(
        stateMachineArn=state_machine_arn,
        name=order_id,
        input=json.dumps(payload),
    )

    arn = response["executionArn"]
    logger.info("SFN execution started: executionArn=%s orderId=%s", arn, order_id)
    return arn


