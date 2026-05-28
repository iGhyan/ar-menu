"""tests/unit/test_request_parser.py"""

import pytest
from shared.request_parser import parse_event, S3Record


def _native_event(key="uploads/model.glb", size=1000, bucket="test-bucket"):
    return {
        "Records": [{
            "eventTime": "2026-05-27T10:00:00Z",
            "s3": {
                "bucket": {"name": bucket},
                "object": {"key": key, "size": size, "eTag": "abc123"},
            },
        }]
    }


def _eventbridge_event(key="uploads/model.glb", size=2000):
    return {
        "detail-type": "Object Created",
        "time": "2026-05-27T10:00:00Z",
        "detail": {
            "bucket": {"name": "test-bucket"},
            "object": {"key": key, "size": size, "etag": "def456"},
        },
    }


class TestNativeS3Parser:
    def test_returns_list_of_records(self):
        records = parse_event(_native_event())
        assert isinstance(records, list)
        assert len(records) == 1

    def test_record_fields(self):
        rec = parse_event(_native_event(key="uploads/model.glb", size=555))[0]
        assert isinstance(rec, S3Record)
        assert rec.bucket  == "test-bucket"
        assert rec.key     == "uploads/model.glb"
        assert rec.size    == 555
        assert rec.source  == "native_s3"

    def test_url_decodes_key(self):
        encoded = "uploads/model+with+spaces.glb"
        rec = parse_event(_native_event(key=encoded))[0]
        assert rec.key == "uploads/model with spaces.glb"

    def test_multiple_records(self):
        event = {
            "Records": [
                {"eventTime": "", "s3": {"bucket": {"name": "b"}, "object": {"key": "uploads/a.glb", "size": 10}}},
                {"eventTime": "", "s3": {"bucket": {"name": "b"}, "object": {"key": "uploads/b.glb", "size": 20}}},
            ]
        }
        records = parse_event(event)
        assert len(records) == 2
        assert records[0].key == "uploads/a.glb"
        assert records[1].key == "uploads/b.glb"

    def test_missing_size_defaults_to_zero(self):
        event = {
            "Records": [{"eventTime": "", "s3": {
                "bucket": {"name": "b"}, "object": {"key": "uploads/x.glb"}
            }}]
        }
        rec = parse_event(event)[0]
        assert rec.size == 0


class TestEventBridgeParser:
    def test_returns_single_record(self):
        records = parse_event(_eventbridge_event())
        assert len(records) == 1

    def test_record_fields(self):
        rec = parse_event(_eventbridge_event(key="uploads/model.glb", size=999))[0]
        assert rec.bucket == "test-bucket"
        assert rec.key    == "uploads/model.glb"
        assert rec.size   == 999
        assert rec.source == "eventbridge"

    def test_url_decodes_key(self):
        rec = parse_event(_eventbridge_event(key="uploads/my%20model.glb"))[0]
        assert rec.key == "uploads/my model.glb"


class TestParseEventErrors:
    def test_unknown_event_raises_value_error(self):
        with pytest.raises(ValueError, match="Unrecognised"):
            parse_event({"totally": "wrong"})

    def test_empty_records_returns_empty_list(self):
        records = parse_event({"Records": []})
        assert records == []
