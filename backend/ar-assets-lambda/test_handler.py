"""
Unit tests for ar-assets-lambda  —  coverage target ≥ 75 %
Run:  pytest test_handler.py -v --cov=handler --cov-report=term-missing
"""

import importlib
import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ── inject env vars before importing handler ──────────────────────────────────
import os
os.environ.update({
    "BUCKET_AR":  "test-bucket",
    "CF_DOMAIN":  "d1234abcd.cloudfront.net",
    "TABLE_MENU": "MenuTable",
})

# ── stub boto3 so no real AWS calls happen ────────────────────────────────────
boto3_mock = MagicMock()

# DDB table mock (module-level, shared across tests)
_ddb_table = MagicMock()
boto3_mock.resource.return_value.Table.return_value = _ddb_table

# S3 client mock
_s3_client = MagicMock()

# CF client mock
_cf_client = MagicMock()

def _boto3_client(service, **_kw):
    if service == "s3":
        return _s3_client
    if service == "cloudfront":
        return _cf_client
    return MagicMock()

boto3_mock.client.side_effect = _boto3_client

sys.modules["boto3"] = boto3_mock

from botocore.exceptions import ClientError  # noqa: E402 – real botocore is fine

import handler  # noqa: E402


# ── fixtures ──────────────────────────────────────────────────────────────────

def _event(method: str, item_id: str = "item-1", body: dict | None = None) -> dict:
    return {
        "httpMethod": method,
        "pathParameters": {"itemId": item_id},
        "body": json.dumps(body) if body else None,
    }


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


# ── GET tests ─────────────────────────────────────────────────────────────────

class TestGet:
    def setup_method(self):
        _ddb_table.reset_mock()
        _s3_client.reset_mock()

    def test_get_success(self):
        _ddb_table.get_item.return_value = {"Item": {"itemId": "item-1", "arModelKey": "ar-models/item-1.glb"}}
        _s3_client.generate_presigned_url.return_value = "https://s3.example.com/signed"

        resp = handler.lambda_handler(_event("GET"), None)

        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["presignedUrl"] == "https://s3.example.com/signed"
        assert body["expiresIn"] == 900

    def test_get_item_not_found(self):
        _ddb_table.get_item.return_value = {}
        resp = handler.lambda_handler(_event("GET"), None)
        assert resp["statusCode"] == 404
        assert "item_not_found" in resp["body"]

    def test_get_no_ar_attr(self):
        _ddb_table.get_item.return_value = {"Item": {"itemId": "item-1"}}
        resp = handler.lambda_handler(_event("GET"), None)
        assert resp["statusCode"] == 404
        assert "ar_model_not_configured" in resp["body"]

    def test_get_s3_access_denied(self):
        _ddb_table.get_item.return_value = {"Item": {"itemId": "item-1", "arModelKey": "k"}}
        _s3_client.generate_presigned_url.side_effect = _client_error("AccessDenied")
        resp = handler.lambda_handler(_event("GET"), None)
        assert resp["statusCode"] == 403

    def test_get_ddb_error(self):
        _ddb_table.get_item.side_effect = _client_error("InternalServerError")
        resp = handler.lambda_handler(_event("GET"), None)
        assert resp["statusCode"] == 500
        _ddb_table.get_item.side_effect = None  # reset

    def test_get_missing_item_id(self):
        event = {"httpMethod": "GET", "pathParameters": None, "body": None}
        resp = handler.lambda_handler(event, None)
        assert resp["statusCode"] == 400


# ── PUT tests ─────────────────────────────────────────────────────────────────

class TestPut:
    def setup_method(self):
        _ddb_table.reset_mock()

    def test_put_success(self):
        _ddb_table.update_item.return_value = {}
        resp = handler.lambda_handler(_event("PUT", body={"arModelKey": "ar-models/item-1.glb", "arScale": 1.0}), None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert "arModelKey" in body["updated"]

    def test_put_no_valid_fields(self):
        resp = handler.lambda_handler(_event("PUT", body={"unknownField": "x"}), None)
        assert resp["statusCode"] == 400

    def test_put_item_not_found(self):
        _ddb_table.update_item.side_effect = _client_error("ConditionalCheckFailedException")
        resp = handler.lambda_handler(_event("PUT", body={"arScale": 0.5}), None)
        assert resp["statusCode"] == 404
        _ddb_table.update_item.side_effect = None

    def test_put_invalid_json(self):
        event = _event("PUT")
        event["body"] = "not-json"
        resp = handler.lambda_handler(event, None)
        assert resp["statusCode"] == 400

    def test_put_ddb_error(self):
        _ddb_table.update_item.side_effect = _client_error("ProvisionedThroughputExceededException")
        resp = handler.lambda_handler(_event("PUT", body={"arScale": 1.0}), None)
        assert resp["statusCode"] == 500
        _ddb_table.update_item.side_effect = None


# ── DELETE tests ──────────────────────────────────────────────────────────────

class TestDelete:
    def setup_method(self):
        _ddb_table.reset_mock()
        _cf_client.reset_mock()
        # Clear cached CF dist ID between tests
        if hasattr(handler._resolve_cf_dist_id, "_cached"):
            del handler._resolve_cf_dist_id._cached

    def _mock_cf_paginator(self, domain: str = "d1234abcd.cloudfront.net"):
        pager = MagicMock()
        pager.paginate.return_value = [
            {"DistributionList": {"Items": [{"DomainName": domain, "Id": "EDFDVBD6EXAMPLE"}]}}
        ]
        _cf_client.get_paginator.return_value = pager

    def test_delete_success(self):
        _ddb_table.update_item.return_value = {}
        self._mock_cf_paginator()
        _cf_client.create_invalidation.return_value = {}

        resp = handler.lambda_handler(_event("DELETE"), None)
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["arMetadataRemoved"] is True

    def test_delete_item_not_found(self):
        _ddb_table.update_item.side_effect = _client_error("ConditionalCheckFailedException")
        resp = handler.lambda_handler(_event("DELETE"), None)
        assert resp["statusCode"] == 404
        _ddb_table.update_item.side_effect = None

    def test_delete_cf_failure_non_critical(self):
        """CF invalidation failure must NOT bubble up as 5xx."""
        _ddb_table.update_item.return_value = {}
        _cf_client.get_paginator.side_effect = Exception("CF outage")

        resp = handler.lambda_handler(_event("DELETE"), None)
        assert resp["statusCode"] == 200          # still succeeds
        _cf_client.get_paginator.side_effect = None

    def test_delete_ddb_error(self):
        _ddb_table.update_item.side_effect = _client_error("InternalServerError")
        resp = handler.lambda_handler(_event("DELETE"), None)
        assert resp["statusCode"] == 500
        _ddb_table.update_item.side_effect = None


# ── misc ──────────────────────────────────────────────────────────────────────

def test_method_not_allowed():
    resp = handler.lambda_handler(_event("PATCH"), None)
    assert resp["statusCode"] == 405


def test_cf_dist_id_caching():
    """Second call must NOT hit get_paginator again."""
    if hasattr(handler._resolve_cf_dist_id, "_cached"):
        del handler._resolve_cf_dist_id._cached

    pager = MagicMock()
    pager.paginate.return_value = [
        {"DistributionList": {"Items": [{"DomainName": "d1234abcd.cloudfront.net", "Id": "DIST1"}]}}
    ]
    _cf_client.get_paginator.return_value = pager

    id1 = handler._resolve_cf_dist_id()
    id2 = handler._resolve_cf_dist_id()
    assert id1 == id2 == "DIST1"
    assert _cf_client.get_paginator.call_count == 1


def test_cf_dist_id_not_found():
    if hasattr(handler._resolve_cf_dist_id, "_cached"):
        del handler._resolve_cf_dist_id._cached

    pager = MagicMock()
    pager.paginate.return_value = [{"DistributionList": {"Items": []}}]
    _cf_client.get_paginator.return_value = pager

    with pytest.raises(ValueError, match="No CloudFront distribution"):
        handler._resolve_cf_dist_id()