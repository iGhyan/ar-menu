"""tests/unit/test_ws_connect.py"""

import pytest
from unittest.mock import MagicMock, patch

from tests.conftest import make_jwt, connect_event, ws_event


def _load_handler(mock_table, redis_ok=True, monkeypatch=None):
    """Import handler with mocked DynamoDB table and optional Redis."""
    import importlib

    fake_redis = MagicMock()
    fake_redis.ping.return_value = True
    fake_redis.hset.return_value = 1

    redis_mod = MagicMock()
    redis_mod.Redis.from_url.return_value = fake_redis

    if not redis_ok:
        fake_redis.ping.side_effect = Exception("Redis unavailable")

    with patch("shared.aws_clients.get_dynamodb_resource") as mock_ddb_res:
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table
        mock_ddb_res.return_value = mock_resource

        with patch.dict("sys.modules", {"redis": redis_mod}):
            import functions.ws_connect.handler as mod
            importlib.reload(mod)
            return mod, fake_redis


class TestWsConnect:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.mock_table = MagicMock()
        self.mock_table.put_item.return_value = {}

    def test_valid_token_returns_200(self):
        mod, _ = _load_handler(self.mock_table)
        token  = make_jwt(groups=["menulay_tenant"])
        resp   = mod.lambda_handler(connect_event(token), {})
        assert resp["statusCode"] == 200
        assert "Connected" in resp["body"]

    def test_valid_token_stores_in_dynamodb(self):
        mod, _ = _load_handler(self.mock_table)
        token  = make_jwt(sub="user-xyz", tenant_id="t-abc", groups=["menulay_admin"])
        resp   = mod.lambda_handler(
            connect_event(token, connection_id="conn-001"), {}
        )
        assert resp["statusCode"] == 200
        self.mock_table.put_item.assert_called_once()
        item = self.mock_table.put_item.call_args[1]["Item"]
        assert item["connectionId"] == "conn-001"
        assert item["userId"]       == "user-xyz"
        assert item["tenantId"]     == "t-abc"
        assert "ttl" in item

    def test_valid_token_stores_in_redis(self):
        mod, fake_redis = _load_handler(self.mock_table)
        token = make_jwt(sub="user-redis-test")
        mod.lambda_handler(connect_event(token, connection_id="conn-r1"), {})
        fake_redis.hset.assert_called_once_with("connections", "conn-r1", "user-redis-test")

    def test_missing_token_returns_401(self):
        mod, _ = _load_handler(self.mock_table)
        resp   = mod.lambda_handler(ws_event(headers={}), {})
        assert resp["statusCode"] == 401
        self.mock_table.put_item.assert_not_called()

    def test_expired_token_returns_401(self):
        mod, _ = _load_handler(self.mock_table)
        token  = make_jwt(expired=True)
        resp   = mod.lambda_handler(connect_event(token), {})
        assert resp["statusCode"] == 401
        self.mock_table.put_item.assert_not_called()

    def test_invalid_issuer_returns_401(self):
        mod, _ = _load_handler(self.mock_table)
        token  = make_jwt(issuer="https://evil.com/fake")
        resp   = mod.lambda_handler(connect_event(token), {})
        assert resp["statusCode"] == 401

    def test_invalid_client_id_returns_401(self):
        mod, _ = _load_handler(self.mock_table)
        token  = make_jwt(client_id="wrong-client")
        resp   = mod.lambda_handler(connect_event(token), {})
        assert resp["statusCode"] == 401

    def test_bearer_prefix_stripped(self):
        mod, _ = _load_handler(self.mock_table)
        token  = make_jwt(groups=["menulay_admin"])
        event  = ws_event(headers={"Authorization": f"Bearer {token}"})
        resp   = mod.lambda_handler(event, {})
        assert resp["statusCode"] == 200

    def test_lowercase_authorization_header(self):
        mod, _ = _load_handler(self.mock_table)
        token  = make_jwt(groups=["menulay_kitchen_staff"])
        event  = ws_event(headers={"authorization": f"Bearer {token}"})
        resp   = mod.lambda_handler(event, {})
        assert resp["statusCode"] == 200

    def test_token_from_query_string(self):
        mod, _ = _load_handler(self.mock_table)
        token  = make_jwt(groups=["menulay_tenant"])
        event  = ws_event(headers={}, query_params={"token": f"Bearer {token}"})
        resp   = mod.lambda_handler(event, {})
        assert resp["statusCode"] == 200

    def test_redis_failure_does_not_prevent_connect(self):
        mod, fake_redis = _load_handler(self.mock_table)
        fake_redis.hset.side_effect = Exception("Redis timeout")
        token = make_jwt(groups=["menulay_tenant"])
        resp  = mod.lambda_handler(connect_event(token), {})
        # DynamoDB should still be called
        assert resp["statusCode"] == 200
        self.mock_table.put_item.assert_called_once()

    def test_all_cognito_groups_allowed(self):
        mod, _ = _load_handler(self.mock_table)
        for group in ["menulay_admin", "menulay_tenant", "menulay_kitchen_staff"]:
            self.mock_table.reset_mock()
            token = make_jwt(groups=[group])
            resp  = mod.lambda_handler(connect_event(token), {})
            assert resp["statusCode"] == 200, f"Failed for group: {group}"

    def test_empty_body_ignored(self):
        mod, _ = _load_handler(self.mock_table)
        token  = make_jwt(groups=["menulay_tenant"])
        event  = connect_event(token)
        event["body"] = None
        resp   = mod.lambda_handler(event, {})
        assert resp["statusCode"] == 200
