"""tests/unit/test_exceptions.py"""

import pytest
from shared.exceptions import (
    FileSizeError,
    MagicBytesError,
    GlbVersionError,
    PolygonCountError,
    TenantKeyError,
    S3ReadError,
    NotificationError,
    FileExtensionError,
    MalformedHeaderError,
)


class TestFileSizeError:
    def test_message_contains_mb_values(self):
        exc = FileSizeError(max_mb=50, actual_bytes=60 * 1024 * 1024)
        assert "50" in exc.message
        assert "60" in exc.message

    def test_error_code(self):
        assert FileSizeError(50, 100).error_code == "FILE_SIZE_EXCEEDED"

    def test_http_status(self):
        assert FileSizeError(50, 100).http_status == 422

    def test_to_dict_has_required_keys(self):
        d = FileSizeError(50, 100).to_dict()
        assert "error_code" in d
        assert "message" in d
        assert "http_status" in d


class TestMagicBytesError:
    def test_hex_in_message(self):
        exc = MagicBytesError(expected=0x46546C67, actual=0xDEADBEEF)
        assert "46546C67" in exc.message.upper()
        assert "DEADBEEF" in exc.message.upper()

    def test_error_code(self):
        assert MagicBytesError(0x1, 0x2).error_code == "INVALID_MAGIC_BYTES"


class TestGlbVersionError:
    def test_message_contains_versions(self):
        exc = GlbVersionError(supported=2, actual=1)
        assert "1" in exc.message
        assert "2" in exc.message

    def test_error_code(self):
        assert GlbVersionError(2, 1).error_code == "UNSUPPORTED_GLB_VERSION"


class TestPolygonCountError:
    def test_message_contains_counts(self):
        exc = PolygonCountError(limit=500_000, actual=600_000)
        assert "500,000" in exc.message
        assert "600,000" in exc.message

    def test_context_fields(self):
        exc = PolygonCountError(limit=500_000, actual=600_000)
        assert exc.context["polygon_limit"] == 500_000
        assert exc.context["actual_polygon_count"] == 600_000


class TestTenantKeyError:
    def test_stores_key_and_reason(self):
        exc = TenantKeyError(key="bad/key", reason="missing prefix")
        assert exc.s3_key == "bad/key"
        assert "missing prefix" in exc.message
        assert exc.http_status == 400


class TestS3ReadError:
    def test_stores_bucket_and_key(self):
        exc = S3ReadError(bucket="my-bucket", key="my/key")
        assert exc.bucket == "my-bucket"
        assert exc.key    == "my/key"
        assert "my-bucket" in exc.message
        assert exc.error_code == "S3_READ_ERROR"


class TestNotificationError:
    def test_topic_arn_in_message(self):
        exc = NotificationError(topic_arn="arn:aws:sns:us-east-1:123:topic")
        assert "arn:aws:sns" in exc.message
