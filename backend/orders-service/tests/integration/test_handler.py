"""tests/integration/test_handler.py"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws


def _ce(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


def _post_event(body: dict) -> dict:
    return {
        "httpMethod": "POST",
        "path": "/orders",
        "body": json.dumps(body),
        "pathParameters": None,
        "queryStringParameters": None,
        "headers": {},
    }


def _get_event(order_id: str, tenant_id: str) -> dict:
    return {
        "httpMethod": "GET",
        "path": f"/orders/{order_id}",
        "body": None,
        "pathParameters": {"id": order_id},
        "queryStringParameters": {"tenantId": tenant_id},
        "headers": {},
    }


def _list_event(tenant_id: str, restaurant_id: str) -> dict:
    return {
        "httpMethod": "GET",
        "path": "/orders",
        "body": None,
        "pathParameters": None,
        "queryStringParameters": {"tenantId": tenant_id, "restaurantId": restaurant_id},
        "headers": {},
    }


def _patch_event(order_id: str, body: dict) -> dict:
    return {
        "httpMethod": "PATCH",
        "path": f"/orders/{order_id}",
        "body": json.dumps(body),
        "pathParameters": {"id": order_id},
        "queryStringParameters": None,
        "headers": {},
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST /orders
# ══════════════════════════════════════════════════════════════════════════════

@mock_aws
class TestPostOrder:
    def test_happy_path_returns_201(
        self, dynamodb, seed_menu, fake_redis, valid_payload, lambda_context, mocker
    ):
        resource, client = dynamodb
        mocker.patch("handler._dynamodb",        resource)
        mocker.patch("handler._dynamodb_client", client)
        mocker.patch("handler._sfn").start_execution.return_value = {
            "executionArn": "arn:aws:states:us-east-1:123:execution:SM:ord"
        }

        from handler import lambda_handler
        resp = lambda_handler(_post_event(valid_payload), lambda_context)

        assert resp["statusCode"] == 201
        body = json.loads(resp["body"])
        assert body["data"]["status"] == "RECEIVED"
        assert "orderId" in body["data"]
        assert "stepFunctionsExecutionArn" in body["data"]

    def test_invalid_json_returns_400(self, lambda_context):
        from handler import lambda_handler
        resp = lambda_handler(
            {"httpMethod": "POST", "path": "/orders", "body": "NOT JSON",
             "pathParameters": None, "queryStringParameters": None, "headers": {}},
            lambda_context
        )
        assert resp["statusCode"] == 400

    def test_missing_field_returns_400(
        self, lambda_context, valid_payload
    ):
        payload = {**valid_payload}
        del payload["tenantId"]
        from handler import lambda_handler
        resp = lambda_handler(_post_event(payload), lambda_context)
        assert resp["statusCode"] == 400

    def test_total_mismatch_returns_400(
        self, lambda_context, valid_payload
    ):
        payload = {**valid_payload, "totalAmountMinorUnits": 9999}
        from handler import lambda_handler
        resp = lambda_handler(_post_event(payload), lambda_context)
        assert resp["statusCode"] == 400

    def test_menu_item_not_found_returns_422(
        self, dynamodb, fake_redis, valid_payload, lambda_context, mocker
    ):
        resource, client = dynamodb
        mocker.patch("handler._dynamodb",        resource)
        mocker.patch("handler._dynamodb_client", client)
        # No menu seeded → item not found

        import os
        with patch.dict("os.environ", {"SKIP_MENU": "false"}):
            from handler import lambda_handler
            resp = lambda_handler(_post_event(valid_payload), lambda_context)
        assert resp["statusCode"] == 422

    def test_dynamo_write_failure_returns_503(
        self, dynamodb, seed_menu, fake_redis, valid_payload, lambda_context, mocker
    ):
        resource, client = dynamodb
        mocker.patch("handler._dynamodb_client", client)

        mock_table = MagicMock()
        mock_table.put_item.side_effect = _ce("InternalServerError")
        mock_res = MagicMock()
        mock_res.Table.return_value = mock_table
        mocker.patch("handler._dynamodb", mock_res)

        from handler import lambda_handler
        resp = lambda_handler(_post_event(valid_payload), lambda_context)
        assert resp["statusCode"] == 503

    def test_sfn_failure_triggers_rollback_returns_503(
        self, dynamodb, seed_menu, fake_redis, valid_payload, lambda_context, mocker
    ):
        resource, client = dynamodb
        mocker.patch("handler._dynamodb",        resource)
        mocker.patch("handler._dynamodb_client", client)
        mocker.patch("handler._sfn").start_execution.side_effect = _ce("StateMachineDoesNotExist")

        from handler import lambda_handler
        resp = lambda_handler(_post_event(valid_payload), lambda_context)

        assert resp["statusCode"] == 503
        # Verify rollback: table should be empty
        table = resource.Table("OrderTable-test")
        assert table.scan()["Count"] == 0

    def test_duplicate_order_returns_409(
        self, dynamodb, seed_menu, fake_redis, valid_payload, lambda_context, mocker
    ):
        resource, client = dynamodb
        mocker.patch("handler._dynamodb",        resource)
        mocker.patch("handler._dynamodb_client", client)
        mocker.patch("handler._sfn").start_execution.return_value = {
            "executionArn": "arn:aws:states:us-east-1:123:execution:SM:ord"
        }
        mocker.patch("orders_service.routes.order_routes.uuid.uuid4", return_value="fixed-uuid")

        from handler import lambda_handler
        lambda_handler(_post_event(valid_payload), lambda_context)
        resp = lambda_handler(_post_event(valid_payload), lambda_context)
        assert resp["statusCode"] == 409


# ══════════════════════════════════════════════════════════════════════════════
# GET /orders/{id}
# ══════════════════════════════════════════════════════════════════════════════

@mock_aws
class TestGetOrder:
    def _seed_order(self, dynamodb, mocker, valid_payload, lambda_context):
        resource, client = dynamodb
        mocker.patch("handler._dynamodb",        resource)
        mocker.patch("handler._dynamodb_client", client)
        mocker.patch("handler._sfn").start_execution.return_value = {
            "executionArn": "arn:exec"
        }
        mocker.patch("orders_service.routes.order_routes.uuid.uuid4", return_value="order-get-1")
        from handler import lambda_handler
        lambda_handler(_post_event(valid_payload), lambda_context)
        return resource, client

    def test_get_existing_order(
        self, dynamodb, seed_menu, fake_redis, valid_payload, lambda_context, mocker
    ):
        resource, client = self._seed_order(dynamodb, mocker, valid_payload, lambda_context)
        from handler import lambda_handler
        resp = lambda_handler(_get_event("order-get-1", "t123"), lambda_context)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["data"]["order"]["orderId"] == "order-get-1"

    def test_get_nonexistent_returns_404(
        self, dynamodb, lambda_context, mocker
    ):
        resource, client = dynamodb
        mocker.patch("handler._dynamodb",        resource)
        mocker.patch("handler._dynamodb_client", client)
        from handler import lambda_handler
        resp = lambda_handler(_get_event("no-such-order", "t123"), lambda_context)
        assert resp["statusCode"] == 404

    def test_missing_tenant_id_returns_400(self, lambda_context):
        from handler import lambda_handler
        event = {
            "httpMethod": "GET", "path": "/orders/order-1",
            "body": None, "pathParameters": {"id": "order-1"},
            "queryStringParameters": {}, "headers": {},
        }
        resp = lambda_handler(event, lambda_context)
        assert resp["statusCode"] == 400


# ══════════════════════════════════════════════════════════════════════════════
# PATCH /orders/{id}
# ══════════════════════════════════════════════════════════════════════════════

@mock_aws
class TestPatchOrder:
    def _seed_order(self, dynamodb, mocker, valid_payload, lambda_context, fake_redis):
        resource, client = dynamodb
        mocker.patch("handler._dynamodb",        resource)
        mocker.patch("handler._dynamodb_client", client)
        mocker.patch("handler._sfn").start_execution.return_value = {"executionArn": "arn:exec"}
        mocker.patch("orders_service.routes.order_routes.uuid.uuid4", return_value="order-patch-1")
        from handler import lambda_handler
        lambda_handler(_post_event(valid_payload), lambda_context)
        return resource, client

    def test_patch_kitchen_accepted(
        self, dynamodb, seed_menu, fake_redis, valid_payload, lambda_context, mocker
    ):
        self._seed_order(dynamodb, mocker, valid_payload, lambda_context, fake_redis)
        mock_sfn = mocker.patch("handler._sfn")
        mock_sfn.start_execution.return_value = {"executionArn": "arn:patch-exec"}

        from handler import lambda_handler
        resp = lambda_handler(
            _patch_event("order-patch-1", {"tenantId": "t123", "kitchenAccepted": True}),
            lambda_context,
        )
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["data"]["status"] == "PREPARING"

    def test_patch_cancelled(
        self, dynamodb, seed_menu, fake_redis, valid_payload, lambda_context, mocker
    ):
        self._seed_order(dynamodb, mocker, valid_payload, lambda_context, fake_redis)
        mocker.patch("handler._sfn").start_execution.return_value = {"executionArn": "arn:x"}

        from handler import lambda_handler
        resp = lambda_handler(
            _patch_event("order-patch-1", {"tenantId": "t123", "cancelled": True}),
            lambda_context,
        )
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["data"]["status"] == "CANCELLED"

    def test_patch_nonexistent_order_returns_404(
        self, dynamodb, lambda_context, mocker
    ):
        resource, client = dynamodb
        mocker.patch("handler._dynamodb",        resource)
        mocker.patch("handler._dynamodb_client", client)

        from handler import lambda_handler
        resp = lambda_handler(
            _patch_event("ghost-order", {"tenantId": "t123"}),
            lambda_context,
        )
        assert resp["statusCode"] == 404

    def test_patch_sfn_failure_still_returns_200(
        self, dynamodb, seed_menu, fake_redis, valid_payload, lambda_context, mocker
    ):
        """DDB update succeeded — SFN failure is non-fatal for PATCH."""
        self._seed_order(dynamodb, mocker, valid_payload, lambda_context, fake_redis)
        mocker.patch("handler._sfn").start_execution.side_effect = _ce("InternalError")

        from handler import lambda_handler
        resp = lambda_handler(
            _patch_event("order-patch-1", {"tenantId": "t123", "foodReady": True}),
            lambda_context,
        )
        assert resp["statusCode"] == 200


# ══════════════════════════════════════════════════════════════════════════════
# Step Functions callback
# ══════════════════════════════════════════════════════════════════════════════

@mock_aws
class TestSfnCallback:
    def test_sfn_callback_updates_status(
        self, dynamodb, seed_menu, fake_redis, valid_payload, lambda_context, mocker
    ):
        resource, client = dynamodb
        mocker.patch("handler._dynamodb",        resource)
        mocker.patch("handler._dynamodb_client", client)
        mocker.patch("handler._sfn").start_execution.return_value = {"executionArn": "arn:exec"}
        mocker.patch("orders_service.routes.order_routes.uuid.uuid4", return_value="sfn-order-1")

        from handler import lambda_handler
        lambda_handler(_post_event(valid_payload), lambda_context)

        sfn_event = {
            "source":   "stepfunctions",
            "orderId":  "sfn-order-1",
            "tenantId": "t123",
            "status":   "PREPARING",
        }
        result = lambda_handler(sfn_event, lambda_context)
        assert result["orderId"] == "sfn-order-1"

        order = resource.Table("OrderTable-test").query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": "TENANT#t123#ORDER#sfn-order-1"},
        )["Items"][0]
        assert order["status"] == "PREPARING"


# ══════════════════════════════════════════════════════════════════════════════
# Misc
# ══════════════════════════════════════════════════════════════════════════════

def test_method_not_allowed(lambda_context):
    from handler import lambda_handler
    resp = lambda_handler(
        {"httpMethod": "DELETE", "path": "/orders", "body": None,
         "pathParameters": None, "queryStringParameters": None, "headers": {}},
        lambda_context,
    )
    assert resp["statusCode"] == 405


def test_options_returns_200(lambda_context):
    from handler import lambda_handler
    resp = lambda_handler(
        {"httpMethod": "OPTIONS", "path": "/orders", "body": None,
         "pathParameters": None, "queryStringParameters": None, "headers": {}},
        lambda_context,
    )
    assert resp["statusCode"] == 200
