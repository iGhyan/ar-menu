"""tests/unit/test_ar_assets_service.py"""

import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError

from ar_assets.service import ArAssetsService, _sanitize, _parse_json_body
from shared.exceptions import (
    ResourceNotFoundError,
    NoValidFieldsError,
    InvalidJsonError,
    PresignError,
    S3ReadError,
    BadRequestError,
    CloudFrontError,
)


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


def _make_service(s3=None, ddb=None, cf=None) -> ArAssetsService:
    return ArAssetsService(
        s3_client=s3 or MagicMock(),
        ddb_table=ddb or MagicMock(),
        cf_client=cf or MagicMock(),
        bucket_name="test-bucket",
        cf_domain="d1234abcd.cloudfront.net",
    )


# ── GET tests ─────────────────────────────────────────────────────────────────

class TestGetArAsset:
    def test_success_returns_presigned_url(self):
        ddb = MagicMock()
        s3  = MagicMock()
        ddb.get_item.return_value = {"Item": {"arModelKey": "models/item.glb"}}
        s3.generate_presigned_url.return_value = "https://signed.url/item.glb"

        svc  = _make_service(s3=s3, ddb=ddb)
        result = svc.get_ar_asset("t-1", "r-1", "item-1")

        assert result["presignedUrl"] == "https://signed.url/item.glb"
        assert result["expiresIn"] == 900
        assert result["itemId"] == "item-1"
        assert result["restaurantId"] == "r-1"

    def test_item_not_found_raises(self):
        ddb = MagicMock()
        ddb.get_item.return_value = {}
        svc = _make_service(ddb=ddb)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            svc.get_ar_asset("t-1", "r-1", "item-1")
        assert exc_info.value.http_status == 404

    def test_ar_model_key_missing_raises(self):
        ddb = MagicMock()
        ddb.get_item.return_value = {"Item": {"someOtherAttr": "value"}}
        svc = _make_service(ddb=ddb)
        with pytest.raises(ResourceNotFoundError):
            svc.get_ar_asset("t-1", "r-1", "item-1")

    def test_s3_access_denied_raises_s3_read_error(self):
        ddb = MagicMock()
        s3  = MagicMock()
        ddb.get_item.return_value = {"Item": {"arModelKey": "k"}}
        s3.generate_presigned_url.side_effect = _client_error("AccessDenied")
        svc = _make_service(s3=s3, ddb=ddb)
        with pytest.raises(S3ReadError):
            svc.get_ar_asset("t-1", "r-1", "item-1")

    def test_s3_other_error_raises_presign_error(self):
        ddb = MagicMock()
        s3  = MagicMock()
        ddb.get_item.return_value = {"Item": {"arModelKey": "k"}}
        s3.generate_presigned_url.side_effect = _client_error("ServiceUnavailable")
        svc = _make_service(s3=s3, ddb=ddb)
        with pytest.raises(PresignError):
            svc.get_ar_asset("t-1", "r-1", "item-1")

    def test_ddb_error_raises_storage_error(self):
        ddb = MagicMock()
        ddb.get_item.side_effect = _client_error("InternalServerError")
        svc = _make_service(ddb=ddb)
        with pytest.raises(S3ReadError):
            svc.get_ar_asset("t-1", "r-1", "item-1")

    def test_correct_ddb_key_used(self):
        ddb = MagicMock()
        s3  = MagicMock()
        ddb.get_item.return_value = {"Item": {"arModelKey": "k"}}
        s3.generate_presigned_url.return_value = "url"
        svc = _make_service(s3=s3, ddb=ddb)
        svc.get_ar_asset("tenant-abc", "rest-xyz", "item-99")

        call_key = ddb.get_item.call_args.kwargs["Key"]
        assert call_key["PK"] == "TENANT#tenant-abc#RESTAURANT#rest-xyz"
        assert call_key["SK"] == "ITEM#item-99"


# ── PUT tests ─────────────────────────────────────────────────────────────────

class TestUpdateArAsset:
    def test_success_returns_updated_fields(self):
        ddb = MagicMock()
        ddb.update_item.return_value = {}
        svc = _make_service(ddb=ddb)
        result = svc.update_ar_asset("t-1", "r-1", "item-1", '{"arModelKey": "k", "arScale": 1.0}')
        assert "arModelKey" in result["updated"]
        assert "arScale" in result["updated"]

    def test_unknown_fields_filtered_out(self):
        ddb = MagicMock()
        ddb.update_item.return_value = {}
        svc = _make_service(ddb=ddb)
        result = svc.update_ar_asset("t-1", "r-1", "item-1", '{"arModelKey": "k", "hackerField": "x"}')
        assert "hackerField" not in result["updated"]
        assert "arModelKey" in result["updated"]

    def test_no_valid_fields_raises(self):
        svc = _make_service()
        with pytest.raises(NoValidFieldsError):
            svc.update_ar_asset("t-1", "r-1", "item-1", '{"unknownField": "x"}')

    def test_invalid_json_raises(self):
        svc = _make_service()
        with pytest.raises(InvalidJsonError):
            svc.update_ar_asset("t-1", "r-1", "item-1", "not-json{{{")

    def test_item_not_found_raises(self):
        ddb = MagicMock()
        ddb.update_item.side_effect = _client_error("ConditionalCheckFailedException")
        svc = _make_service(ddb=ddb)
        with pytest.raises(ResourceNotFoundError):
            svc.update_ar_asset("t-1", "r-1", "item-1", '{"arScale": 0.5}')

    def test_float_converted_to_decimal(self):
        ddb = MagicMock()
        ddb.update_item.return_value = {}
        svc = _make_service(ddb=ddb)
        svc.update_ar_asset("t-1", "r-1", "item-1", '{"arScale": 1.5}')

        call_kwargs = ddb.update_item.call_args.kwargs
        val = call_kwargs["ExpressionAttributeValues"][":v_arScale"]
        assert isinstance(val, Decimal)

    def test_ddb_other_error_raises_bad_request(self):
        ddb = MagicMock()
        ddb.update_item.side_effect = _client_error("ProvisionedThroughputExceededException")
        svc = _make_service(ddb=ddb)
        with pytest.raises(BadRequestError):
            svc.update_ar_asset("t-1", "r-1", "item-1", '{"arScale": 1.0}')

    def test_none_body_treated_as_empty(self):
        svc = _make_service()
        with pytest.raises(NoValidFieldsError):
            svc.update_ar_asset("t-1", "r-1", "item-1", None)


# ── DELETE tests ──────────────────────────────────────────────────────────────

class TestDeleteArAsset:
    def _mock_cf_paginator(self, cf, domain="d1234abcd.cloudfront.net"):
        pager = MagicMock()
        pager.paginate.return_value = [
            {"DistributionList": {"Items": [{"DomainName": domain, "Id": "DIST1"}]}}
        ]
        cf.get_paginator.return_value = pager

    def test_success_returns_metadata_removed(self):
        ddb = MagicMock()
        cf  = MagicMock()
        ddb.update_item.return_value = {}
        self._mock_cf_paginator(cf)
        cf.create_invalidation.return_value = {}

        svc    = _make_service(ddb=ddb, cf=cf)
        result = svc.delete_ar_asset("t-1", "r-1", "item-1")

        assert result["arMetadataRemoved"] is True
        assert result["itemId"] == "item-1"

    def test_item_not_found_raises(self):
        ddb = MagicMock()
        ddb.update_item.side_effect = _client_error("ConditionalCheckFailedException")
        svc = _make_service(ddb=ddb)
        with pytest.raises(ResourceNotFoundError):
            svc.delete_ar_asset("t-1", "r-1", "item-1")

    def test_cf_failure_is_non_critical(self):
        """CF invalidation error must not propagate as an exception."""
        ddb = MagicMock()
        cf  = MagicMock()
        ddb.update_item.return_value = {}
        cf.get_paginator.side_effect = Exception("CF outage")

        svc    = _make_service(ddb=ddb, cf=cf)
        result = svc.delete_ar_asset("t-1", "r-1", "item-1")
        assert result["arMetadataRemoved"] is True  # Still succeeds

    def test_cf_dist_id_cached(self):
        """Second delete should not re-call list_distributions."""
        ddb = MagicMock()
        cf  = MagicMock()
        ddb.update_item.return_value = {}
        self._mock_cf_paginator(cf)
        cf.create_invalidation.return_value = {}

        svc = _make_service(ddb=ddb, cf=cf)
        svc.delete_ar_asset("t-1", "r-1", "item-1")
        svc.delete_ar_asset("t-1", "r-1", "item-2")

        assert cf.get_paginator.call_count == 1  # Cached after first call

    def test_cf_domain_not_found_raises(self):
        cf  = MagicMock()
        pager = MagicMock()
        pager.paginate.return_value = [{"DistributionList": {"Items": []}}]
        cf.get_paginator.return_value = pager

        svc = _make_service(cf=cf)
        with pytest.raises(CloudFrontError):
            svc._resolve_cf_dist_id()


# ── Helper unit tests ─────────────────────────────────────────────────────────

class TestSanitize:
    def test_float_to_decimal(self):
        assert isinstance(_sanitize(1.5), Decimal)

    def test_nested_dict(self):
        result = _sanitize({"a": 1.5, "b": "str"})
        assert isinstance(result["a"], Decimal)
        assert result["b"] == "str"

    def test_list(self):
        result = _sanitize([1.0, 2.0])
        assert all(isinstance(x, Decimal) for x in result)

    def test_non_float_passthrough(self):
        assert _sanitize("hello") == "hello"
        assert _sanitize(42) == 42
        assert _sanitize(None) is None


class TestParseJsonBody:
    def test_valid_json(self):
        result = _parse_json_body('{"arScale": 1.0}')
        assert result == {"arScale": 1.0}

    def test_none_returns_empty_dict(self):
        assert _parse_json_body(None) == {}

    def test_invalid_json_raises(self):
        with pytest.raises(InvalidJsonError):
            _parse_json_body("not-json")

    def test_empty_string_returns_empty_dict(self):
        assert _parse_json_body("") == {}
