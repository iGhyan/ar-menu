"""tests/unit/test_ws_message.py"""

import importlib
import json
import pytest
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError

from tests.conftest import ws_event, message_event


def _ce(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


def _load_handler():
    """Load ws_message handler with mocked AWS clients."""
    mock_table = MagicMock()
    mock_table.put_item.return_value = {}

    mock_sfn = MagicMock()
    mock_sfn.send_task_success.return_value = {}

    mock_sqs = MagicMock()
    mock_sqs.send_message.return_value = {"MessageId": "dlq-msg-1"}

    with patch("shared.aws_clients.get_dynamodb_resource") as mock_ddb_res, \
         patch("shared.aws_clients.get_sqs_client", return_value=mock_sqs), \
         patch("boto3.client", return_value=mock_sfn):

        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table
        mock_ddb_res.return_value = mock_resource

        import functions.ws_message.handler as mod
        importlib.reload(mod)
        return mod, mock_table, mock_sfn, mock_sqs


class TestWsMessage:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.mod, self.mock_table, self.mock_sfn, self.mock_sqs = _load_handler()

    # ── Happy path ────────────────────────────────────────────────────────────

    def test_valid_status_returns_200(self):
        resp = self.mod.lambda_handler(
            message_event({"status": "confirmed", "taskToken": "tok-1"}), {}
        )
        assert resp["statusCode"] == 200

    def test_response_contains_order_id_and_status(self):
        resp = self.mod.lambda_handler(
            message_event({"status": "pending", "orderId": "ord-99"}), {}
        )
        body = json.loads(resp["body"])
        assert body["orderId"] == "ord-99"
        assert body["status"]  == "pending"

    def test_order_saved_to_dynamodb(self):
        self.mod.lambda_handler(
            message_event({"status": "pending", "orderId": "ord-ddb"}), {}
        )
        self.mock_table.put_item.assert_called_once()
        item = self.mock_table.put_item.call_args[1]["Item"]
        assert item["status"]  == "pending"
        assert item["orderId"] == "ord-ddb"

    def test_ddb_item_has_connection_id(self):
        self.mod.lambda_handler(
            message_event({"status": "processing"}, connection_id="conn-xyz"), {}
        )
        item = self.mock_table.put_item.call_args[1]["Item"]
        assert item["connectionId"] == "conn-xyz"

    def test_sfn_called_when_task_token_present(self):
        self.mod.lambda_handler(
            message_event({"status": "confirmed", "taskToken": "my-tok"}), {}
        )
        self.mock_sfn.send_task_success.assert_called_once()
        args = self.mock_sfn.send_task_success.call_args[1]
        assert args["taskToken"] == "my-tok"

    def test_sfn_output_contains_correct_fields(self):
        self.mod.lambda_handler(
            message_event({"status": "delivered", "taskToken": "tok-x", "orderId": "ord-sfn"}), {}
        )
        output = json.loads(
            self.mock_sfn.send_task_success.call_args[1]["output"]
        )
        assert output["orderId"] == "ord-sfn"
        assert output["status"]  == "delivered"

    def test_sfn_not_called_without_task_token(self):
        self.mod.lambda_handler(
            message_event({"status": "pending"}), {}
        )
        self.mock_sfn.send_task_success.assert_not_called()

    def test_auto_generates_order_id_when_missing(self):
        resp = self.mod.lambda_handler(
            message_event({"status": "processing"}), {}
        )
        body = json.loads(resp["body"])
        assert len(body["orderId"]) == 36   # UUID v4

    def test_all_valid_statuses_accepted(self):
        for status in ["pending", "confirmed", "processing", "cancelled", "delivered"]:
            self.mock_table.reset_mock()
            resp = self.mod.lambda_handler(message_event({"status": status}), {})
            assert resp["statusCode"] == 200, f"Failed for status: {status}"

    # ── Error paths ───────────────────────────────────────────────────────────

    def test_invalid_status_returns_400(self):
        resp = self.mod.lambda_handler(message_event({"status": "flying"}), {})
        assert resp["statusCode"] == 400
        self.mock_table.put_item.assert_not_called()

    def test_empty_status_returns_400(self):
        resp = self.mod.lambda_handler(message_event({"status": ""}), {})
        assert resp["statusCode"] == 400

    def test_invalid_json_returns_400(self):
        event = ws_event(body="not-json{{{")
        resp  = self.mod.lambda_handler(event, {})
        assert resp["statusCode"] == 400

    def test_none_body_treated_as_empty_status(self):
        event = ws_event(body=None)
        resp  = self.mod.lambda_handler(event, {})
        assert resp["statusCode"] == 400  # empty status is invalid

    # ── SFN failure → DLQ ────────────────────────────────────────────────────

    def test_sfn_failure_sends_to_dlq(self):
        self.mock_sfn.send_task_success.side_effect = _ce("TaskTimedOut")
        resp = self.mod.lambda_handler(
            message_event({"status": "confirmed", "taskToken": "tok-fail"}), {}
        )
        assert resp["statusCode"] == 200   # client still gets 200
        self.mock_sqs.send_message.assert_called_once()

    def test_sfn_failure_dlq_contains_connection_id(self):
        self.mock_sfn.send_task_success.side_effect = _ce("InvalidToken")
        self.mod.lambda_handler(
            message_event(
                {"status": "confirmed", "taskToken": "t"},
                connection_id="conn-xyz",
            ), {}
        )
        dlq_body = json.loads(self.mock_sqs.send_message.call_args[1]["MessageBody"])
        assert dlq_body["connectionId"] == "conn-xyz"

    def test_sfn_failure_dlq_message_group_is_connection_id(self):
        self.mock_sfn.send_task_success.side_effect = _ce("InternalError")
        self.mod.lambda_handler(
            message_event(
                {"status": "confirmed", "taskToken": "t"},
                connection_id="conn-grp",
            ), {}
        )
        assert self.mock_sqs.send_message.call_args[1]["MessageGroupId"] == "conn-grp"

    def test_dlq_failure_does_not_crash_handler(self):
        self.mock_sfn.send_task_success.side_effect = _ce("TaskTimedOut")
        self.mock_sqs.send_message.side_effect = _ce("QueueDoesNotExist")
        resp = self.mod.lambda_handler(
            message_event({"status": "confirmed", "taskToken": "t"}), {}
        )
        assert resp["statusCode"] == 200   # still succeeds

    def test_no_dlq_when_sfn_succeeds(self):
        resp = self.mod.lambda_handler(
            message_event({"status": "confirmed", "taskToken": "good-tok"}), {}
        )
        assert resp["statusCode"] == 200
        self.mock_sqs.send_message.assert_not_called()
