"""tests/unit/test_utils/test_utils.py"""
from __future__ import annotations

import base64
import json
import time
from decimal import Decimal

import pytest

from utils.dynamo_helpers import decimal_to_python, encode_lek, decode_lek, build_update_expression
from utils.ids import new_id, utc_now
from utils.response import ok, created, bad_request, not_found, conflict, internal_error
from utils.exceptions import (
    NotFoundError, ConflictError, AuthorisationError, QuotaExceededError,
    DependencyError, http_status_for, RestaurantNotFoundError, MenuItemConflictError,
)
from utils.retry import retry


class TestDecimalToPython:
    def test_decimal_to_int(self):
        assert decimal_to_python(Decimal("42")) == 42

    def test_decimal_to_float(self):
        assert decimal_to_python(Decimal("3.14")) == 3.14

    def test_nested_dict(self):
        result = decimal_to_python({"price": Decimal("100"), "name": "item"})
        assert result["price"] == 100
        assert result["name"] == "item"

    def test_nested_list(self):
        result = decimal_to_python([Decimal("1"), Decimal("2.5")])
        assert result == [1, 2.5]

    def test_passthrough_non_decimal(self):
        assert decimal_to_python("hello") == "hello"
        assert decimal_to_python(42)      == 42
        assert decimal_to_python(None)    is None


class TestLekEncoding:
    def test_encode_and_decode_roundtrip(self):
        lek = {"PK": "TENANT#t1", "SK": "RESTAURANT#r1"}
        encoded = encode_lek(lek)
        assert isinstance(encoded, str)
        decoded = decode_lek(encoded)
        assert decoded == lek

    def test_encode_none_returns_none(self):
        assert encode_lek(None) is None
        assert encode_lek({})   is None

    def test_decode_none_returns_none(self):
        assert decode_lek(None) is None
        assert decode_lek("")   is None

    def test_decode_invalid_base64_returns_none(self):
        assert decode_lek("not-valid-base64!!!") is None


class TestBuildUpdateExpression:
    def test_single_field(self):
        expr, names, values = build_update_expression({"name": "Test"})
        assert "SET" in expr
        assert "Test" in values.values()

    def test_multiple_fields(self):
        expr, names, values = build_update_expression({"name": "A", "isActive": True})
        assert expr.count("=") == 2
        assert len(names) == 2
        assert len(values) == 2

    def test_reserved_words_aliased(self):
        expr, names, values = build_update_expression({"name": "x", "status": "ok"})
        # Field names should be in expression attribute names (with # prefix)
        for k in names.keys():
            assert k.startswith("#")


class TestIds:
    def test_new_id_is_uuid_format(self):
        id1 = new_id()
        assert len(id1) == 36
        assert id1.count("-") == 4

    def test_new_id_is_unique(self):
        assert new_id() != new_id()

    def test_utc_now_format(self):
        ts = utc_now()
        assert ts.endswith("Z")
        assert "T" in ts
        assert len(ts) == 20


class TestResponseHelpers:
    def test_ok_returns_200(self):
        resp = ok({"key": "value"})
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["key"] == "value"

    def test_created_returns_201(self):
        resp = created({"id": "123"})
        assert resp["statusCode"] == 201

    def test_bad_request_returns_400(self):
        resp = bad_request("Missing field")
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body["error"] == "BAD_REQUEST"

    def test_bad_request_with_errors(self):
        resp = bad_request("Validation failed", {"name": "required"})
        body = json.loads(resp["body"])
        assert "errors" in body
        assert body["errors"]["name"] == "required"

    def test_not_found_returns_404(self):
        resp = not_found("Restaurant")
        assert resp["statusCode"] == 404

    def test_conflict_returns_409(self):
        resp = conflict("Version mismatch")
        assert resp["statusCode"] == 409

    def test_internal_error_returns_500(self):
        resp = internal_error()
        assert resp["statusCode"] == 500

    def test_all_responses_have_cors_header(self):
        for resp in [ok({}), created({}), bad_request("x"), not_found("x")]:
            assert "Access-Control-Allow-Origin" in resp["headers"]

    def test_decimal_serialized_correctly(self):
        resp = ok({"price": Decimal("12.50")})
        body = json.loads(resp["body"])
        assert body["price"] == 12.5


class TestExceptions:
    def test_http_status_not_found(self):
        assert http_status_for(RestaurantNotFoundError("x")) == 404

    def test_http_status_conflict(self):
        assert http_status_for(MenuItemConflictError("x")) == 409

    def test_http_status_authorisation(self):
        assert http_status_for(AuthorisationError("x")) == 403

    def test_http_status_quota(self):
        assert http_status_for(QuotaExceededError("x")) == 429

    def test_http_status_dependency(self):
        assert http_status_for(DependencyError("x")) == 503

    def test_exception_message(self):
        exc = NotFoundError("Restaurant not found", code="RESTAURANT_NOT_FOUND")
        assert exc.message == "Restaurant not found"
        assert exc.code == "RESTAURANT_NOT_FOUND"


class TestRetryDecorator:
    def test_succeeds_on_first_try(self):
        calls = []

        @retry(retries=3, base_delay=0)
        def fn():
            calls.append(1)
            return "ok"

        result = fn()
        assert result == "ok"
        assert len(calls) == 1

    def test_retries_on_failure_then_succeeds(self):
        calls = []

        @retry(retries=3, base_delay=0, exceptions=(ValueError,))
        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise ValueError("transient")
            return "ok"

        result = fn()
        assert result == "ok"
        assert len(calls) == 3

    def test_raises_after_max_retries(self):
        @retry(retries=2, base_delay=0, exceptions=(ValueError,))
        def fn():
            raise ValueError("always fails")

        with pytest.raises(ValueError):
            fn()

    def test_non_retryable_raises_immediately(self):
        """ClientError with non-retryable code should not be retried."""
        from botocore.exceptions import ClientError
        calls = []

        @retry(retries=3, base_delay=0, exceptions=(ClientError,))
        def fn():
            calls.append(1)
            raise ClientError(
                {"Error": {"Code": "ResourceNotFoundException", "Message": "not found"}},
                "GetItem"
            )

        with pytest.raises(ClientError):
            fn()
        assert len(calls) == 1  # No retry for non-retryable code
