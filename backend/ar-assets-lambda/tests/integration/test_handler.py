"""tests/integration/test_handler.py"""

import json
import pytest
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError

from tests.conftest import make_jwt, auth_event
import handler


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


def _mock_context():
    ctx = MagicMock()
    ctx.aws_request_id   = "test-req-id"
    ctx.function_name    = "ar-assets-lambda"
    ctx.function_version = "$LATEST"
    return ctx


def _get_event(tenant_id="tenant-abc", restaurant_id="rest-1", item_id="item-1"):
    return {
        "httpMethod":           "GET",
        "pathParameters":       {"restaurantId": restaurant_id, "itemId": item_id},
        "headers":              {"x-tenant-id": tenant_id},
        "queryStringParameters": None,
        "body":                 None,
    }


# ── OPTIONS ───────────────────────────────────────────────────────────────────

class TestOptions:
    def test_options_returns_200(self):
        event = {"httpMethod": "OPTIONS", "pathParameters": {"restaurantId": "r", "itemId": "i"}, "headers": {}}
        resp  = handler.lambda_handler(event, _mock_context())
        assert resp["statusCode"] == 200
        assert "Access-Control-Allow-Origin" in resp["headers"]


# ── GET ───────────────────────────────────────────────────────────────────────

class TestGet:
    @patch("handler._build_service")
    def test_get_success(self, mock_build):
        svc = MagicMock()
        svc.get_ar_asset.return_value = {
            "itemId": "item-1", "restaurantId": "rest-1",
            "presignedUrl": "https://signed.url", "expiresIn": 900,
            "cfDomain": "d1234.cloudfront.net",
        }
        mock_build.return_value = svc

        resp = handler.lambda_handler(_get_event(), _mock_context())
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["data"]["presignedUrl"] == "https://signed.url"

    @patch("handler._build_service")
    def test_get_missing_tenant_id(self, mock_build):
        mock_build.return_value = MagicMock()
        event = {
            "httpMethod": "GET",
            "pathParameters": {"restaurantId": "r", "itemId": "i"},
            "headers": {},
            "queryStringParameters": None,
            "body": None,
        }
        resp = handler.lambda_handler(event, _mock_context())
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body["error"]["code"] == "MISSING_TENANT_ID"

    @patch("handler._build_service")
    def test_get_tenant_id_from_query_string(self, mock_build):
        svc = MagicMock()
        svc.get_ar_asset.return_value = {
            "itemId": "i", "restaurantId": "r",
            "presignedUrl": "url", "expiresIn": 900, "cfDomain": "cf",
        }
        mock_build.return_value = svc

        event = {
            "httpMethod": "GET",
            "pathParameters": {"restaurantId": "r", "itemId": "i"},
            "headers": {},
            "queryStringParameters": {"tenantId": "tenant-abc"},
            "body": None,
        }
        resp = handler.lambda_handler(event, _mock_context())
        assert resp["statusCode"] == 200

    @patch("handler._build_service")
    def test_get_item_not_found(self, mock_build):
        from shared.exceptions import ResourceNotFoundError
        svc = MagicMock()
        svc.get_ar_asset.side_effect = ResourceNotFoundError("Menu item", "item-1")
        mock_build.return_value = svc

        resp = handler.lambda_handler(_get_event(), _mock_context())
        assert resp["statusCode"] == 404

    def test_get_missing_path_params(self):
        event = {"httpMethod": "GET", "pathParameters": None, "headers": {}, "body": None}
        resp  = handler.lambda_handler(event, _mock_context())
        assert resp["statusCode"] == 400


# ── PUT ───────────────────────────────────────────────────────────────────────

class TestPut:
    @patch("handler._build_service")
    def test_put_success_admin(self, mock_build):
        svc = MagicMock()
        svc.update_ar_asset.return_value = {"itemId": "item-1", "updated": ["arModelKey"]}
        mock_build.return_value = svc

        token = make_jwt(groups=["menulay_admin"], tenant_id="tenant-abc")
        event = auth_event("PUT", body={"arModelKey": "k.glb"}, token=token)
        resp  = handler.lambda_handler(event, _mock_context())

        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert "arModelKey" in body["data"]["updated"]

    @patch("handler._build_service")
    def test_put_success_tenant(self, mock_build):
        svc = MagicMock()
        svc.update_ar_asset.return_value = {"itemId": "i", "updated": ["arScale"]}
        mock_build.return_value = svc

        token = make_jwt(groups=["menulay_tenant"], tenant_id="tenant-abc")
        event = auth_event("PUT", body={"arScale": 1.0}, token=token)
        resp  = handler.lambda_handler(event, _mock_context())
        assert resp["statusCode"] == 200

    def test_put_no_token_returns_401(self):
        event = auth_event("PUT", body={"arScale": 1.0})  # no token
        resp  = handler.lambda_handler(event, _mock_context())
        assert resp["statusCode"] == 401

    def test_put_kitchen_role_returns_403(self):
        token = make_jwt(groups=["menulay_kitchen_staff"])
        event = auth_event("PUT", body={"arScale": 1.0}, token=token)
        resp  = handler.lambda_handler(event, _mock_context())
        assert resp["statusCode"] == 403
        body  = json.loads(resp["body"])
        assert body["error"]["code"] == "RBAC_INSUFFICIENT_ROLE"

    def test_put_expired_token_returns_401(self):
        token = make_jwt(expired=True, groups=["menulay_admin"])
        event = auth_event("PUT", body={"arScale": 1.0}, token=token)
        resp  = handler.lambda_handler(event, _mock_context())
        assert resp["statusCode"] == 401
        body  = json.loads(resp["body"])
        assert body["error"]["code"] == "TOKEN_EXPIRED"

    @patch("handler._build_service")
    def test_put_no_valid_fields_returns_400(self, mock_build):
        from shared.exceptions import NoValidFieldsError
        svc = MagicMock()
        svc.update_ar_asset.side_effect = NoValidFieldsError(["arModelKey", "arScale"])
        mock_build.return_value = svc

        token = make_jwt(groups=["menulay_admin"])
        event = auth_event("PUT", body={"badField": "x"}, token=token)
        resp  = handler.lambda_handler(event, _mock_context())
        assert resp["statusCode"] == 400

    @patch("handler._build_service")
    def test_put_item_not_found_returns_404(self, mock_build):
        from shared.exceptions import ResourceNotFoundError
        svc = MagicMock()
        svc.update_ar_asset.side_effect = ResourceNotFoundError("Menu item", "x")
        mock_build.return_value = svc

        token = make_jwt(groups=["menulay_admin"])
        event = auth_event("PUT", body={"arScale": 1.0}, token=token)
        resp  = handler.lambda_handler(event, _mock_context())
        assert resp["statusCode"] == 404


# ── DELETE ────────────────────────────────────────────────────────────────────

class TestDelete:
    @patch("handler._build_service")
    def test_delete_success_admin(self, mock_build):
        svc = MagicMock()
        svc.delete_ar_asset.return_value = {"itemId": "item-1", "arMetadataRemoved": True}
        mock_build.return_value = svc

        token = make_jwt(groups=["menulay_admin"], tenant_id="tenant-abc")
        event = auth_event("DELETE", token=token)
        resp  = handler.lambda_handler(event, _mock_context())

        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["data"]["arMetadataRemoved"] is True

    def test_delete_no_token_returns_401(self):
        event = auth_event("DELETE")
        resp  = handler.lambda_handler(event, _mock_context())
        assert resp["statusCode"] == 401

    def test_delete_kitchen_role_returns_403(self):
        token = make_jwt(groups=["menulay_kitchen_staff"])
        event = auth_event("DELETE", token=token)
        resp  = handler.lambda_handler(event, _mock_context())
        assert resp["statusCode"] == 403

    @patch("handler._build_service")
    def test_delete_item_not_found_returns_404(self, mock_build):
        from shared.exceptions import ResourceNotFoundError
        svc = MagicMock()
        svc.delete_ar_asset.side_effect = ResourceNotFoundError("Menu item", "x")
        mock_build.return_value = svc

        token = make_jwt(groups=["menulay_admin"])
        event = auth_event("DELETE", token=token)
        resp  = handler.lambda_handler(event, _mock_context())
        assert resp["statusCode"] == 404


# ── Misc ──────────────────────────────────────────────────────────────────────

class TestMisc:
    def test_method_not_allowed(self):
        event = {"httpMethod": "PATCH", "pathParameters": {"restaurantId": "r", "itemId": "i"}, "headers": {}, "body": None}
        resp  = handler.lambda_handler(event, _mock_context())
        assert resp["statusCode"] == 405

    def test_all_responses_have_cors_header(self):
        event = {"httpMethod": "OPTIONS", "pathParameters": {"restaurantId": "r", "itemId": "i"}, "headers": {}}
        resp  = handler.lambda_handler(event, _mock_context())
        assert resp["headers"].get("Access-Control-Allow-Origin") == "*"
