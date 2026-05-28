"""
tests/integration/test_lambda_handler.py
=========================================
End-to-end tests for `lambda_handler` with all AWS clients mocked.

These tests exercise the full flow:
  event → parse_event → extract_tenant_context → GlbValidator → move → notify → response
"""

import json
import struct
import pytest
from unittest.mock import MagicMock, patch, call

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "layer", "python"))

import lambda_function as lf

_TENANT     = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_RESTAURANT = "b2c3d4e5-f6a7-8901-bcde-f12345678901"
_VALID_KEY  = f"uploads/TENANT#{_TENANT}/restaurants/{_RESTAURANT}/ar-models/model.glb"


def _s3_event(key=_VALID_KEY, size=1000, bucket="test-bucket"):
    return {
        "Records": [{
            "eventTime": "2026-05-27T10:00:00Z",
            "s3": {
                "bucket": {"name": bucket},
                "object": {"key": key, "size": size, "eTag": "abc"},
            },
        }]
    }


def _mock_context():
    ctx = MagicMock()
    ctx.aws_request_id   = "integration-test-request-id"
    ctx.function_name    = "glb-validator-lambda"
    ctx.function_version = "$LATEST"
    return ctx


# ── Approved flow ──────────────────────────────────────────────────────────────

class TestApprovedFlow:
    @patch("lambda_function.GlbValidator")
    @patch("lambda_function.get_s3_client")
    @patch("lambda_function.get_sns_client")
    def test_approved_response(self, mock_sns_factory, mock_s3_factory, mock_validator_cls):
        mock_s3  = MagicMock()
        mock_sns = MagicMock()
        mock_s3_factory.return_value  = mock_s3
        mock_sns_factory.return_value = mock_sns

        validator_instance = MagicMock()
        validator_instance.validate.return_value = None  # No exception = pass
        mock_validator_cls.return_value = validator_instance

        result = lf.lambda_handler(_s3_event(), _mock_context())

        assert result["statusCode"] == 207
        body = json.loads(result["body"])
        assert body["success"] is True
        assert body["data"][0]["result"] == "approved"
        assert body["summary"]["approved"] == 1
        assert body["summary"]["rejected"] == 0

    @patch("lambda_function.GlbValidator")
    @patch("lambda_function.get_s3_client")
    @patch("lambda_function.get_sns_client")
    def test_approved_file_is_moved(self, mock_sns_factory, mock_s3_factory, mock_validator_cls):
        mock_s3 = MagicMock()
        mock_s3_factory.return_value  = mock_s3
        mock_sns_factory.return_value = MagicMock()

        mock_validator_cls.return_value.validate.return_value = None

        lf.lambda_handler(_s3_event(), _mock_context())

        mock_s3.copy_object.assert_called_once()
        mock_s3.delete_object.assert_called_once()
        # Verify destination contains "approved"
        copy_call = mock_s3.copy_object.call_args
        assert "approved" in copy_call.kwargs.get("Key", copy_call[1].get("Key", ""))

    @patch("lambda_function.GlbValidator")
    @patch("lambda_function.get_s3_client")
    @patch("lambda_function.get_sns_client")
    def test_approved_does_not_notify_sns(self, mock_sns_factory, mock_s3_factory, mock_validator_cls):
        mock_s3 = MagicMock()
        mock_sns = MagicMock()
        mock_s3_factory.return_value  = mock_s3
        mock_sns_factory.return_value = mock_sns
        mock_validator_cls.return_value.validate.return_value = None

        lf.lambda_handler(_s3_event(), _mock_context())

        mock_sns.publish.assert_not_called()


# ── Rejected flow ──────────────────────────────────────────────────────────────

class TestRejectedFlow:
    @patch("lambda_function.GlbValidator")
    @patch("lambda_function.get_s3_client")
    @patch("lambda_function.get_sns_client")
    def test_rejected_response(self, mock_sns_factory, mock_s3_factory, mock_validator_cls):
        from shared.exceptions import MagicBytesError

        mock_s3_factory.return_value  = MagicMock()
        mock_sns_factory.return_value = MagicMock()

        validator_instance = MagicMock()
        validator_instance.validate.side_effect = MagicBytesError(0x46546C67, 0xDEADBEEF)
        mock_validator_cls.return_value = validator_instance

        result = lf.lambda_handler(_s3_event(), _mock_context())

        assert result["statusCode"] == 207
        body = json.loads(result["body"])
        assert body["data"][0]["result"] == "rejected"
        assert body["summary"]["rejected"] == 1

    @patch("lambda_function.GlbValidator")
    @patch("lambda_function.get_s3_client")
    @patch("lambda_function.get_sns_client")
    def test_rejected_notifies_admin(self, mock_sns_factory, mock_s3_factory, mock_validator_cls):
        from shared.exceptions import FileSizeError

        mock_s3  = MagicMock()
        mock_sns = MagicMock()
        mock_s3_factory.return_value  = mock_s3
        mock_sns_factory.return_value = mock_sns

        validator_instance = MagicMock()
        validator_instance.validate.side_effect = FileSizeError(50, 60 * 1024 * 1024)
        mock_validator_cls.return_value = validator_instance

        lf.lambda_handler(_s3_event(), _mock_context())

        mock_sns.publish.assert_called_once()
        call_kwargs = mock_sns.publish.call_args.kwargs
        assert "model.glb" in call_kwargs["Subject"]

    @patch("lambda_function.GlbValidator")
    @patch("lambda_function.get_s3_client")
    @patch("lambda_function.get_sns_client")
    def test_rejected_file_moved_to_rejected_prefix(
        self, mock_sns_factory, mock_s3_factory, mock_validator_cls
    ):
        from shared.exceptions import GlbVersionError

        mock_s3 = MagicMock()
        mock_s3_factory.return_value  = mock_s3
        mock_sns_factory.return_value = MagicMock()
        mock_validator_cls.return_value.validate.side_effect = GlbVersionError(2, 1)

        lf.lambda_handler(_s3_event(), _mock_context())

        copy_call = mock_s3.copy_object.call_args
        dest = copy_call.kwargs.get("Key", "")
        assert dest.startswith("rejected/")


# ── Skipped flow (invalid tenant key) ─────────────────────────────────────────

class TestSkippedFlow:
    @patch("lambda_function.GlbValidator")
    @patch("lambda_function.get_s3_client")
    @patch("lambda_function.get_sns_client")
    def test_skips_invalid_key(self, mock_sns_factory, mock_s3_factory, mock_validator_cls):
        mock_s3_factory.return_value  = MagicMock()
        mock_sns_factory.return_value = MagicMock()
        validator_instance = MagicMock()
        mock_validator_cls.return_value = validator_instance

        event = _s3_event(key="approved/model.glb")
        result = lf.lambda_handler(event, _mock_context())

        body = json.loads(result["body"])
        assert body["data"][0]["result"] == "skipped"
        # Validator should NOT have been called
        validator_instance.validate.assert_not_called()

    @patch("lambda_function.GlbValidator")
    @patch("lambda_function.get_s3_client")
    @patch("lambda_function.get_sns_client")
    def test_skips_does_not_move_file(self, mock_sns_factory, mock_s3_factory, mock_validator_cls):
        mock_s3 = MagicMock()
        mock_s3_factory.return_value  = mock_s3
        mock_sns_factory.return_value = MagicMock()
        mock_validator_cls.return_value.validate.return_value = None

        lf.lambda_handler(_s3_event(key="bad/key.glb"), _mock_context())

        mock_s3.copy_object.assert_not_called()
        mock_s3.delete_object.assert_not_called()


# ── Multiple records ───────────────────────────────────────────────────────────

class TestMultipleRecords:
    @patch("lambda_function.GlbValidator")
    @patch("lambda_function.get_s3_client")
    @patch("lambda_function.get_sns_client")
    def test_processes_all_records(self, mock_sns_factory, mock_s3_factory, mock_validator_cls):
        tenant_a = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        tenant_b = "b2c3d4e5-f6a7-8901-bcde-f12345678901"
        rest     = "c3d4e5f6-a7b8-9012-cdef-012345678901"

        key_a = f"uploads/TENANT#{tenant_a}/restaurants/{rest}/ar-models/a.glb"
        key_b = f"uploads/TENANT#{tenant_b}/restaurants/{rest}/ar-models/b.glb"

        event = {
            "Records": [
                {"eventTime": "", "s3": {"bucket": {"name": "b"}, "object": {"key": key_a, "size": 10}}},
                {"eventTime": "", "s3": {"bucket": {"name": "b"}, "object": {"key": key_b, "size": 20}}},
            ]
        }

        mock_s3_factory.return_value  = MagicMock()
        mock_sns_factory.return_value = MagicMock()
        mock_validator_cls.return_value.validate.return_value = None

        result = lf.lambda_handler(event, _mock_context())
        body = json.loads(result["body"])
        assert body["summary"]["total"]    == 2
        assert body["summary"]["approved"] == 2


# ── EventBridge source ─────────────────────────────────────────────────────────

class TestEventBridgeSource:
    @patch("lambda_function.GlbValidator")
    @patch("lambda_function.get_s3_client")
    @patch("lambda_function.get_sns_client")
    def test_eventbridge_event_processed(self, mock_sns_factory, mock_s3_factory, mock_validator_cls):
        mock_s3_factory.return_value  = MagicMock()
        mock_sns_factory.return_value = MagicMock()
        mock_validator_cls.return_value.validate.return_value = None

        event = {
            "detail-type": "Object Created",
            "time": "2026-05-27T10:00:00Z",
            "detail": {
                "bucket": {"name": "test-bucket"},
                "object": {"key": _VALID_KEY, "size": 500},
            },
        }
        result = lf.lambda_handler(event, _mock_context())
        body = json.loads(result["body"])
        assert body["summary"]["approved"] == 1


# ── SNS failure is non-fatal ───────────────────────────────────────────────────

class TestSNSFailureNonFatal:
    @patch("lambda_function.GlbValidator")
    @patch("lambda_function.get_s3_client")
    @patch("lambda_function.get_sns_client")
    def test_sns_error_does_not_crash_handler(
        self, mock_sns_factory, mock_s3_factory, mock_validator_cls
    ):
        from shared.exceptions import MagicBytesError
        from botocore.exceptions import ClientError

        mock_s3_factory.return_value  = MagicMock()
        mock_sns = MagicMock()
        mock_sns.publish.side_effect = ClientError(
            {"Error": {"Code": "500", "Message": "SNS boom"}}, "Publish"
        )
        mock_sns_factory.return_value = mock_sns
        mock_validator_cls.return_value.validate.side_effect = MagicBytesError(
            0x46546C67, 0x0
        )

        # Must not raise
        result = lf.lambda_handler(_s3_event(), _mock_context())
        body = json.loads(result["body"])
        assert body["data"][0]["result"] == "rejected"
