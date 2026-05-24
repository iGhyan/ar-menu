"""
test_handler.py — Handler integration tests.

AWS calls use moto. Redis uses fakeredis.
Step Functions and EventBridge are mocked via pytest-mock
since moto's SFN support is limited.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from moto import mock_aws


def _apigw_event(body: dict) -> dict:
    return {"body": json.dumps(body)}


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


# ── Happy path ─────────────────────────────────────────────────────────────────

@mock_aws
def test_happy_path_returns_201(dynamodb, seed_menu, fake_redis, valid_payload, lambda_context, mocker):
    resource, _ = dynamodb

    mocker.patch("orders_lambda.handler._dynamodb", resource)
    mocker.patch("orders_lambda.handler._dynamodb_client", dynamodb[1])
    mocker.patch(
        "orders_lambda.handler._sfn"
    ).start_execution.return_value = {
        "executionArn": "arn:aws:states:us-east-1:123:execution:SM:ord"
    }
    mocker.patch("orders_lambda.handler._events")
    mocker.patch("orders_lambda.handler._sqs")

    from orders_lambda.handler import lambda_handler

    response = lambda_handler(_apigw_event(valid_payload), lambda_context)

    assert response["statusCode"] == 201
    body = json.loads(response["body"])
    assert body["status"] == "RECEIVED"
    assert "orderId" in body
    assert "stepFunctionsExecutionArn" in body


# ── Validation errors ──────────────────────────────────────────────────────────

@mock_aws
def test_invalid_json_returns_400(lambda_context):
    from orders_lambda.handler import lambda_handler

    response = lambda_handler({"body": "NOT JSON"}, lambda_context)
    assert response["statusCode"] == 400


@mock_aws
def test_missing_field_returns_400(lambda_context, valid_payload):
    payload = {**valid_payload}
    del payload["tenantId"]

    from orders_lambda.handler import lambda_handler

    response = lambda_handler(_apigw_event(payload), lambda_context)
    assert response["statusCode"] == 400


@mock_aws
def test_total_mismatch_returns_400(lambda_context, valid_payload):
    payload = {**valid_payload, "totalAmountMinorUnits": 9999}

    from orders_lambda.handler import lambda_handler

    response = lambda_handler(_apigw_event(payload), lambda_context)
    assert response["statusCode"] == 400


@mock_aws
def test_menu_item_not_found_returns_422(dynamodb, fake_redis, valid_payload, lambda_context, mocker):
    resource, client = dynamodb
    mocker.patch("orders_lambda.handler._dynamodb", resource)
    mocker.patch("orders_lambda.handler._dynamodb_client", client)
    # No menu seeded → item not found

    from orders_lambda.handler import lambda_handler

    response = lambda_handler(_apigw_event(valid_payload), lambda_context)
    assert response["statusCode"] == 422


# ── DynamoDB failures ──────────────────────────────────────────────────────────

@mock_aws
def test_dynamo_write_failure_returns_503(dynamodb, seed_menu, fake_redis, valid_payload, lambda_context, mocker):
    resource, client = dynamodb
    mocker.patch("orders_lambda.handler._dynamodb_client", client)

    mock_table = MagicMock()
    mock_table.put_item.side_effect = _client_error("InternalServerError")
    mock_resource = MagicMock()
    mock_resource.Table.return_value = mock_table
    mocker.patch("orders_lambda.handler._dynamodb", mock_resource)

    from orders_lambda.handler import lambda_handler

    response = lambda_handler(_apigw_event(valid_payload), lambda_context)
    assert response["statusCode"] == 503


# ── Step Functions failure → rollback ──────────────────────────────────────────

@mock_aws
def test_sfn_failure_triggers_rollback_and_returns_503(
    dynamodb, seed_menu, fake_redis, valid_payload, lambda_context, mocker
):
    resource, client = dynamodb

    mocker.patch("orders_lambda.handler._dynamodb", resource)
    mocker.patch("orders_lambda.handler._dynamodb_client", client)
    mocker.patch("orders_lambda.handler._sfn").start_execution.side_effect = _client_error("StateMachineDoesNotExist")
    mocker.patch("orders_lambda.handler._events")
    mocker.patch("orders_lambda.handler._sqs")

    from orders_lambda.handler import lambda_handler

    response = lambda_handler(_apigw_event(valid_payload), lambda_context)

    assert response["statusCode"] == 503

    # Verify the order was rolled back (table should be empty)
    table = resource.Table("OrderTable-test")
    scan = table.scan()
    assert scan["Count"] == 0


# ── Duplicate order ────────────────────────────────────────────────────────────

@mock_aws
def test_duplicate_order_returns_409(dynamodb, seed_menu, fake_redis, valid_payload, lambda_context, mocker):
    resource, client = dynamodb

    mocker.patch("orders_lambda.handler._dynamodb", resource)
    mocker.patch("orders_lambda.handler._dynamodb_client", client)
    mocker.patch(
        "orders_lambda.handler._sfn"
    ).start_execution.return_value = {
        "executionArn": "arn:aws:states:us-east-1:123:execution:SM:ord"
    }
    mocker.patch("orders_lambda.handler._events")
    mocker.patch("orders_lambda.handler._sqs")

    # Patch uuid to return same ID both calls
    mocker.patch("orders_lambda.handler.uuid.uuid4", return_value="fixed-uuid")

    from orders_lambda.handler import lambda_handler

    lambda_handler(_apigw_event(valid_payload), lambda_context)
    response = lambda_handler(_apigw_event(valid_payload), lambda_context)

    assert response["statusCode"] == 409
