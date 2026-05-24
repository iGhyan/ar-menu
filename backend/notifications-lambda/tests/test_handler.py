"""
tests/test_notifications_lambda.py
≥ 75% coverage target
Run: pytest tests/ -v --cov=handler --cov-report=term-missing
"""

import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


# ── Fixtures ──────────────────────────────────────────────────────────────────

BASE_ENV = {
    "SNS_TOPIC":              "arn:aws:sns:us-east-1:123:orders-topic",
    "REDIS_URL":              "redis://localhost:6379",
    "WS_ENDPOINT":            "https://abc.execute-api.us-east-1.amazonaws.com/prod",
    "WS_CONNECTIONS_TABLE":   "WsConnectionsTable",
    "PINPOINT_APP_ID":        "pinpoint-app-id",
    "PINPOINT_FROM_NUMBER":   "+12345678900",
    "TABLE_ORDER":            "OrderTable",
    "AWS_REGION":             "us-east-1",
}

def _sqs_event(payload: dict) -> dict:
    return {
        "Records": [
            {
                "messageId": "msg-001",
                "body": json.dumps(payload),
            }
        ]
    }

def _eb_event(detail: dict) -> dict:
    return _sqs_event({"detail-type": "ORDER_STATUS_CHANGED", "detail": detail})


ORDER_PAYLOAD = {
    "orderId":  "ORD-123",
    "tenantId": "TENANT-A",
    "status":   "READY",
    "userId":   "USER-42",
    "phone":    "+923001234567",
    "email":    "user@example.com",
    "message":  "Your order is ready!",
}


# ══════════════════════════════════════════════════════════════════════════════
# TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestHandlerHappyPath:

    @patch.dict("os.environ", BASE_ENV)
    @patch("handler.sns")
    @patch("handler.apigw_mgmt")
    @patch("handler.pinpoint")
    @patch("handler.connections_table")
    @patch("handler.get_redis")
    def test_full_success(self, mock_get_redis, mock_conns, mock_pp,
                          mock_ws, mock_sns):
        import handler

        mock_get_redis.return_value = None   # Redis unavailable path
        mock_sns.publish.return_value = {"MessageId": "sns-111"}
        mock_conns.query.return_value = {
            "Items": [{"userId": "USER-42", "connectionId": "conn-abc"}]
        }
        mock_ws.post_to_connection.return_value = {}
        mock_pp.send_messages.return_value = {"MessageResponse": {}}

        result = handler.lambda_handler(_sqs_event(ORDER_PAYLOAD), {})

        assert result["batchItemFailures"] == []
        mock_sns.publish.assert_called_once()
        mock_ws.post_to_connection.assert_called_once()
        mock_pp.send_messages.assert_called_once()

    @patch.dict("os.environ", BASE_ENV)
    @patch("handler.sns")
    @patch("handler.apigw_mgmt")
    @patch("handler.pinpoint")
    @patch("handler.connections_table")
    @patch("handler.get_redis")
    def test_eventbridge_envelope(self, mock_get_redis, mock_conns, mock_pp,
                                  mock_ws, mock_sns):
        import handler

        mock_get_redis.return_value = None
        mock_sns.publish.return_value = {"MessageId": "sns-222"}
        mock_conns.query.return_value = {"Items": []}
        mock_pp.send_messages.return_value = {"MessageResponse": {}}

        result = handler.lambda_handler(_eb_event(ORDER_PAYLOAD), {})

        assert result["batchItemFailures"] == []
        mock_sns.publish.assert_called_once()


class TestSNSFailure:

    @patch.dict("os.environ", BASE_ENV)
    @patch("handler.sns")
    @patch("handler.get_redis")
    def test_sns_failure_goes_to_dlq(self, mock_get_redis, mock_sns):
        from botocore.exceptions import ClientError
        import handler

        mock_get_redis.return_value = None
        mock_sns.publish.side_effect = ClientError(
            {"Error": {"Code": "InternalError", "Message": "fail"}}, "Publish"
        )

        result = handler.lambda_handler(_sqs_event(ORDER_PAYLOAD), {})

        # Record should appear in batchItemFailures → SQS retries → DLQ
        assert len(result["batchItemFailures"]) == 1
        assert result["batchItemFailures"][0]["itemIdentifier"] == "msg-001"


class TestWebSocketPush:

    @patch.dict("os.environ", BASE_ENV)
    @patch("handler.sns")
    @patch("handler.apigw_mgmt")
    @patch("handler.pinpoint")
    @patch("handler.connections_table")
    @patch("handler.get_redis")
    def test_stale_ws_connection_pruned(self, mock_get_redis, mock_conns,
                                        mock_pp, mock_ws, mock_sns):
        import handler
        from botocore.exceptions import ClientError

        mock_get_redis.return_value = None
        mock_sns.publish.return_value = {"MessageId": "sns-333"}
        mock_conns.query.return_value = {
            "Items": [{"userId": "USER-42", "connectionId": "stale-conn"}]
        }
        gone_exc = mock_ws.exceptions.GoneException
        mock_ws.post_to_connection.side_effect = gone_exc(
            {"Error": {"Code": "GoneException", "Message": "gone"}}, "PostToConnection"
        )
        mock_pp.send_messages.return_value = {"MessageResponse": {}}

        # Should NOT raise — stale connections are skipped (ephemeral)
        result = handler.lambda_handler(_sqs_event(ORDER_PAYLOAD), {})
        assert result["batchItemFailures"] == []
        mock_conns.delete_item.assert_called_once()

    @patch.dict("os.environ", BASE_ENV)
    @patch("handler.sns")
    @patch("handler.apigw_mgmt")
    @patch("handler.pinpoint")
    @patch("handler.connections_table")
    @patch("handler.get_redis")
    def test_ws_push_generic_error_skipped(self, mock_get_redis, mock_conns,
                                            mock_pp, mock_ws, mock_sns):
        import handler

        mock_get_redis.return_value = None
        mock_sns.publish.return_value = {"MessageId": "sns-444"}
        mock_conns.query.return_value = {
            "Items": [{"userId": "USER-42", "connectionId": "conn-xyz"}]
        }
        mock_ws.post_to_connection.side_effect = Exception("network blip")
        mock_pp.send_messages.return_value = {"MessageResponse": {}}

        result = handler.lambda_handler(_sqs_event(ORDER_PAYLOAD), {})
        assert result["batchItemFailures"] == []  # ephemeral — skip

    @patch.dict("os.environ", BASE_ENV)
    @patch("handler.sns")
    @patch("handler.pinpoint")
    @patch("handler.connections_table")
    @patch("handler.get_redis")
    def test_no_connections_skips_ws(self, mock_get_redis, mock_conns,
                                     mock_pp, mock_sns):
        import handler

        mock_get_redis.return_value = None
        mock_sns.publish.return_value = {"MessageId": "sns-555"}
        mock_conns.query.return_value = {"Items": []}
        mock_pp.send_messages.return_value = {"MessageResponse": {}}

        payload = {**ORDER_PAYLOAD, "userId": "NO-CONN-USER"}
        result = handler.lambda_handler(_sqs_event(payload), {})
        assert result["batchItemFailures"] == []


class TestRedisIntegration:

    @patch.dict("os.environ", BASE_ENV)
    @patch("handler.sns")
    @patch("handler.apigw_mgmt")
    @patch("handler.pinpoint")
    @patch("handler.get_redis")
    def test_redis_cache_hit(self, mock_get_redis, mock_pp, mock_ws, mock_sns):
        import handler

        mock_redis = MagicMock()
        mock_redis.smembers.return_value = {"conn-from-redis"}
        mock_get_redis.return_value = mock_redis

        mock_sns.publish.return_value = {"MessageId": "sns-666"}
        mock_ws.post_to_connection.return_value = {}
        mock_pp.send_messages.return_value = {"MessageResponse": {}}

        result = handler.lambda_handler(_sqs_event(ORDER_PAYLOAD), {})
        assert result["batchItemFailures"] == []
        mock_ws.post_to_connection.assert_called_once()

    @patch.dict("os.environ", BASE_ENV)
    @patch("handler.sns")
    @patch("handler.apigw_mgmt")
    @patch("handler.pinpoint")
    @patch("handler.connections_table")
    @patch("handler.get_redis")
    def test_redis_timeout_fallback_to_ddb(self, mock_get_redis, mock_conns,
                                            mock_pp, mock_ws, mock_sns):
        import handler

        mock_redis = MagicMock()
        mock_redis.smembers.side_effect = Exception("redis timeout")
        mock_get_redis.return_value = mock_redis

        mock_sns.publish.return_value = {"MessageId": "sns-777"}
        mock_conns.query.return_value = {
            "Items": [{"userId": "USER-42", "connectionId": "conn-ddb"}]
        }
        mock_ws.post_to_connection.return_value = {}
        mock_pp.send_messages.return_value = {"MessageResponse": {}}

        result = handler.lambda_handler(_sqs_event(ORDER_PAYLOAD), {})
        assert result["batchItemFailures"] == []
        mock_conns.query.assert_called_once()


class TestPinpoint:

    @patch.dict("os.environ", BASE_ENV)
    @patch("handler.sns")
    @patch("handler.apigw_mgmt")
    @patch("handler.pinpoint")
    @patch("handler.connections_table")
    @patch("handler.get_redis")
    def test_pinpoint_failure_is_non_fatal(self, mock_get_redis, mock_conns,
                                            mock_pp, mock_ws, mock_sns):
        from botocore.exceptions import ClientError
        import handler

        mock_get_redis.return_value = None
        mock_sns.publish.return_value = {"MessageId": "sns-888"}
        mock_conns.query.return_value = {"Items": []}
        mock_pp.send_messages.side_effect = ClientError(
            {"Error": {"Code": "BadRequestException", "Message": "bad"}},
            "SendMessages"
        )

        result = handler.lambda_handler(_sqs_event(ORDER_PAYLOAD), {})
        assert result["batchItemFailures"] == []  # Pinpoint failure = best-effort

    @patch.dict("os.environ", {**BASE_ENV, "PINPOINT_APP_ID": ""})
    @patch("handler.sns")
    @patch("handler.connections_table")
    @patch("handler.pinpoint")
    @patch("handler.get_redis")
    def test_pinpoint_skipped_when_no_app_id(self, mock_get_redis, mock_pp,
                                              mock_conns, mock_sns):
        import handler

        mock_get_redis.return_value = None
        mock_sns.publish.return_value = {"MessageId": "sns-999"}
        mock_conns.query.return_value = {"Items": []}

        result = handler.lambda_handler(_sqs_event(ORDER_PAYLOAD), {})
        assert result["batchItemFailures"] == []
        mock_pp.send_messages.assert_not_called()


class TestDefaultMessage:

    @patch.dict("os.environ", BASE_ENV)
    def test_default_messages(self):
        import handler
        assert "ready" in handler._default_message("READY",     "ORD-1").lower()
        assert "placed" in handler._default_message("PLACED",   "ORD-1").lower()
        assert "cancelled" in handler._default_message("CANCELLED", "ORD-1").lower()
        assert "ORD-1" in handler._default_message("UNKNOWN",   "ORD-1")


class TestBatchPartialFailure:

    @patch.dict("os.environ", BASE_ENV)
    @patch("handler.sns")
    @patch("handler.get_redis")
    def test_partial_batch_failure(self, mock_get_redis, mock_sns):
        from botocore.exceptions import ClientError
        import handler

        mock_get_redis.return_value = None

        def sns_side_effect(**kwargs):
            msg = json.loads(kwargs["Message"])
            if msg["orderId"] == "ORD-BAD":
                raise ClientError(
                    {"Error": {"Code": "InternalError", "Message": "fail"}}, "Publish"
                )
            return {"MessageId": "ok"}

        mock_sns.publish.side_effect = sns_side_effect

        event = {
            "Records": [
                {"messageId": "msg-good", "body": json.dumps({**ORDER_PAYLOAD, "orderId": "ORD-GOOD", "phone": None, "email": None, "userId": None})},
                {"messageId": "msg-bad",  "body": json.dumps({**ORDER_PAYLOAD, "orderId": "ORD-BAD",  "phone": None, "email": None, "userId": None})},
            ]
        }

        result = handler.lambda_handler(event, {})
        failed_ids = [f["itemIdentifier"] for f in result["batchItemFailures"]]
        assert "msg-bad" in failed_ids
        assert "msg-good" not in failed_ids