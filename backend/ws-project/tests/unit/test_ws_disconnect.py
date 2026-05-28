"""tests/unit/test_ws_disconnect.py"""

import importlib
import pytest
from unittest.mock import MagicMock, patch

from tests.conftest import ws_event


def _load_handler(redis_data: dict = None):
    """Load disconnect handler with mocked Redis and DynamoDB."""
    fake_redis = MagicMock()
    fake_redis.ping.return_value = True

    # Simulate hdel behavior
    store = dict(redis_data or {"connections": {"conn-abc": "user-123"}})

    def _hdel(name, *keys):
        deleted = 0
        for k in keys:
            if k in store.get(name, {}):
                del store[name][k]
                deleted += 1
        return deleted

    fake_redis.hdel.side_effect = _hdel

    redis_mod = MagicMock()
    redis_mod.Redis.from_url.return_value = fake_redis

    mock_table = MagicMock()
    mock_table.delete_item.return_value = {}

    with patch("shared.aws_clients.get_dynamodb_resource") as mock_ddb_res:
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table
        mock_ddb_res.return_value = mock_resource

        with patch.dict("sys.modules", {"redis": redis_mod}):
            import functions.ws_disconnect.handler as mod
            importlib.reload(mod)
            return mod, fake_redis, store, mock_table


class TestWsDisconnect:
    def test_returns_200(self):
        mod, _, _, _ = _load_handler()
        resp = mod.lambda_handler(ws_event(connection_id="conn-abc"), {})
        assert resp["statusCode"] == 200
        assert "Disconnected" in resp["body"]

    def test_removes_from_redis(self):
        mod, _, store, _ = _load_handler(
            redis_data={"connections": {"conn-abc": "user-123"}}
        )
        mod.lambda_handler(ws_event(connection_id="conn-abc"), {})
        assert "conn-abc" not in store.get("connections", {})

    def test_removes_from_dynamodb(self):
        mod, _, _, mock_table = _load_handler()
        mod.lambda_handler(ws_event(connection_id="conn-abc"), {})
        mock_table.delete_item.assert_called_once_with(
            Key={"connectionId": "conn-abc"}
        )

    def test_redis_failure_still_returns_200(self):
        mod, fake_redis, _, _ = _load_handler()
        fake_redis.hdel.side_effect = Exception("Redis timeout")
        resp = mod.lambda_handler(ws_event(connection_id="conn-abc"), {})
        assert resp["statusCode"] == 200

    def test_missing_connection_id_handled_gracefully(self):
        mod, _, _, _ = _load_handler()
        event = {"requestContext": {}, "headers": {}, "body": None}
        resp  = mod.lambda_handler(event, {})
        assert resp["statusCode"] == 200

    def test_non_existent_connection_returns_200(self):
        mod, _, store, _ = _load_handler(
            redis_data={"connections": {}}  # empty — conn-ghost not present
        )
        resp = mod.lambda_handler(ws_event(connection_id="conn-ghost"), {})
        assert resp["statusCode"] == 200

    def test_ddb_failure_still_returns_200(self):
        mod, _, _, mock_table = _load_handler()
        mock_table.delete_item.side_effect = Exception("DDB unavailable")
        resp = mod.lambda_handler(ws_event(connection_id="conn-abc"), {})
        assert resp["statusCode"] == 200
