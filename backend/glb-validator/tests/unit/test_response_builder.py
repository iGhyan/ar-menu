"""tests/unit/test_response_builder.py"""

import json
import pytest
from shared.response_builder import ResponseBuilder, bind_request_id
from shared.exceptions import FileSizeError


class TestSuccessResponse:
    def test_status_code(self):
        r = ResponseBuilder.success()
        assert r["statusCode"] == 200

    def test_body_success_flag(self):
        body = json.loads(ResponseBuilder.success()["body"])
        assert body["success"] is True

    def test_data_included(self):
        body = json.loads(ResponseBuilder.success(data={"foo": "bar"})["body"])
        assert body["data"] == {"foo": "bar"}

    def test_custom_status(self):
        r = ResponseBuilder.success(status=201)
        assert r["statusCode"] == 201

    def test_has_timestamp(self):
        body = json.loads(ResponseBuilder.success()["body"])
        assert "timestamp" in body

    def test_has_request_id(self):
        bind_request_id("req-abc")
        body = json.loads(ResponseBuilder.success()["body"])
        assert body["request_id"] == "req-abc"


class TestErrorResponse:
    def test_status_code(self):
        r = ResponseBuilder.error("SOME_ERROR", "oops", status=422)
        assert r["statusCode"] == 422

    def test_body_success_false(self):
        body = json.loads(ResponseBuilder.error("ERR", "bad")["body"])
        assert body["success"] is False

    def test_error_code_and_message(self):
        body = json.loads(ResponseBuilder.error("MY_CODE", "my message")["body"])
        assert body["error"]["code"]    == "MY_CODE"
        assert body["error"]["message"] == "my message"

    def test_detail_included(self):
        body = json.loads(
            ResponseBuilder.error("ERR", "msg", detail={"field": "value"})["body"]
        )
        assert body["error"]["detail"]["field"] == "value"


class TestFromException:
    def test_app_exception_maps_fields(self):
        exc = FileSizeError(max_mb=50, actual_bytes=60 * 1024 * 1024)
        r   = ResponseBuilder.from_exception(exc)
        body = json.loads(r["body"])
        assert r["statusCode"] == 422
        assert body["error"]["code"] == "FILE_SIZE_EXCEEDED"

    def test_generic_exception_returns_500(self):
        r = ResponseBuilder.from_exception(RuntimeError("boom"))
        assert r["statusCode"] == 500


class TestPartialResponse:
    def _results(self):
        return [
            {"result": "approved", "key": "uploads/a.glb"},
            {"result": "rejected", "key": "uploads/b.glb"},
            {"result": "skipped",  "key": "uploads/c.glb"},
        ]

    def test_status_207(self):
        r = ResponseBuilder.partial(self._results())
        assert r["statusCode"] == 207

    def test_summary_counts(self):
        body = json.loads(ResponseBuilder.partial(self._results())["body"])
        assert body["summary"]["total"]    == 3
        assert body["summary"]["approved"] == 1
        assert body["summary"]["rejected"] == 1
        assert body["summary"]["skipped"]  == 1

    def test_data_preserved(self):
        body = json.loads(ResponseBuilder.partial(self._results())["body"])
        assert len(body["data"]) == 3
