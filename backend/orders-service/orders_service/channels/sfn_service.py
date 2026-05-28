"""
orders_service/channels/sfn_service.py
=======================================
StepFunctionsService — starts and manages order workflow executions.

The state machine receives the full order payload so every state has
access to orderId, tenantId, restaurantId, tableId, and choice flags.
"""

from __future__ import annotations

import json
from typing import Any

from botocore.exceptions import ClientError

from orders_service.models import OrderRequest, OrderStatusUpdate, clean_decimals
from shared.structured_logger import get_logger

_log = get_logger("orders.sfn")


class StepFunctionsService:
    """
    Starts Step Functions executions for order workflows.

    Parameters
    ----------
    sfn_client        : boto3 Step Functions client
    state_machine_arn : ARN of the order workflow state machine
    """

    def __init__(self, sfn_client: Any, state_machine_arn: str):
        self._sfn = sfn_client
        self._arn = state_machine_arn

    def start_new_order(self, order_id: str, request: OrderRequest) -> str:
        """
        Start a new order execution.

        Returns
        -------
        str — executionArn

        Raises
        ------
        ClientError — on SFN failure (caller handles rollback)
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
            "kitchenAccepted":       False,
            "foodReady":             False,
            "delivered":             False,
            "cancelled":             False,
        }

        resp = self._sfn.start_execution(
            stateMachineArn=self._arn,
            name=order_id,
            input=json.dumps(payload),
        )
        execution_arn = resp["executionArn"]
        _log.info(
            "sfn.execution.started",
            order_id=order_id,
            execution_arn=execution_arn,
        )
        return execution_arn

    def start_status_update(
        self,
        order_id: str,
        order:    dict,
        update:   OrderStatusUpdate,
        exec_name: str,
    ) -> str:
        """
        Start a new execution for a PATCH status update.

        Returns
        -------
        str — executionArn, or "" if SFN fails (non-fatal for PATCH)
        """
        sfn_input = {
            "orderId":               order_id,
            "tenantId":              update.tenantId,
            "restaurantId":          order.get("restaurantId", ""),
            "tableId":               order.get("tableId", ""),
            "totalAmountMinorUnits": int(order.get("totalAmountMinorUnits", 0)),
            "currencyCode":          order.get("currencyCode", "PKR"),
            "lineItems":             clean_decimals(order.get("lineItems", [])),
            "guestConnectionId":     order.get("guestConnectionId"),
            "kitchenAccepted":       update.kitchenAccepted,
            "foodReady":             update.foodReady,
            "delivered":             update.delivered,
            "cancelled":             update.cancelled,
        }

        try:
            resp = self._sfn.start_execution(
                stateMachineArn=self._arn,
                name=exec_name,
                input=json.dumps(sfn_input),
            )
            execution_arn = resp["executionArn"]
            _log.info(
                "sfn.status_update.started",
                order_id=order_id,
                execution_arn=execution_arn,
            )
            return execution_arn
        except ClientError as exc:
            _log.warning(
                "sfn.status_update.failed",
                order_id=order_id,
                error_code=exc.response["Error"]["Code"],
                exc_message=str(exc),
            )
            return ""
