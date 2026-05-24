"""
Tests for all 3 WebSocket Lambda functions
Run: pytest tests/ -v --cov=functions --cov-report=term-missing

Coverage targets:
  ws-connect    >= 80%
  ws-disconnect >= 70%
  ws-message    >= 75%
"""

import json
import sys
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ─── Fake redis so tests don't need a real server ─────────────
fake_redis_mod = types.ModuleType("redis")

class FakeRedis:
    def __init__(self):
        self._data = {}
    def hset(self, name, key, value):
        self._data.setdefault(name, {})[key] = value
    def hdel(self, name, *keys):
        deleted = 0
        for k in keys:
            if k in self._data.get(name, {}):
                del self._data[name][k]
                deleted += 1
        return deleted
    def delete(self, key):
        return self._data.pop(key, None) is not None

class FakeRedisError(Exception):
    pass

fake_redis_mod.Redis = MagicMock()
fake_redis_mod.RedisError = FakeRedisError
sys.modules["redis"] = fake_redis_mod

# Fake jwt module
fake_jwt = types.ModuleType("jwt")
fake_jwt.ExpiredSignatureError = Exception
fake_jwt.InvalidTokenError     = Exception

def _jwt_decode(token, secret, algorithms):
    if token == "valid.jwt.token":
        return {"sub": "user-123"}
    raise fake_jwt.InvalidTokenError("bad token")

fake_jwt.decode = _jwt_decode
sys.modules["jwt"] = fake_jwt

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _ws_event(route="$connect", connection_id="conn-abc", headers=None, body=None):
    return {
        "requestContext": {
            "routeKey":     route,
            "connectionId": connection_id,
        },
        "headers": headers or {},
        "body":    body,
    }


# ═════════════════════════════════════════════════════════════
# TESTS — ws-connect-lambda  (target >= 80%)
# ═════════════════════════════════════════════════════════════

class TestWsConnect:

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL",   "redis://localhost:6379")
        monkeypatch.setenv("TABLE_CONN",  "ConnectionTable-dev")
        monkeypatch.setenv("JWT_SECRET",  "test-secret")

        self.fake_redis = FakeRedis()
        fake_redis_mod.Redis.from_url = MagicMock(return_value=self.fake_redis)

        self.mock_table = MagicMock()
        mock_dynamodb = MagicMock()
        mock_dynamodb.Table.return_value = self.mock_table

        with patch("boto3.resource", return_value=mock_dynamodb):
            import importlib
            import functions.ws_connect.handler as mod
            importlib.reload(mod)
            self.handler = mod.lambda_handler

    # ── Happy path ────────────────────────────────────────
    def test_valid_jwt_returns_200(self):
        event = _ws_event(headers={"Authorization": "Bearer valid.jwt.token"})
        resp  = self.handler(event, {})
        assert resp["statusCode"] == 200
        assert "Connected" in resp["body"]

    def test_valid_jwt_stores_in_dynamodb(self):
        event = _ws_event(connection_id="conn-001",
                          headers={"Authorization": "Bearer valid.jwt.token"})
        self.handler(event, {})
        self.mock_table.put_item.assert_called_once()
        item = self.mock_table.put_item.call_args[1]["Item"]
        assert item["connectionId"] == "conn-001"
        assert item["userId"]       == "user-123"

    def test_valid_jwt_stores_in_redis(self):
        event = _ws_event(connection_id="conn-001",
                          headers={"Authorization": "Bearer valid.jwt.token"})
        self.handler(event, {})
        assert self.fake_redis._data["connections"]["conn-001"] == "user-123"

    # ── Error paths ───────────────────────────────────────
    def test_missing_token_returns_401(self):
        event = _ws_event(headers={})
        resp  = self.handler(event, {})
        assert resp["statusCode"] == 401

    def test_invalid_jwt_returns_401(self):
        event = _ws_event(headers={"Authorization": "Bearer bad.token"})
        resp  = self.handler(event, {})
        assert resp["statusCode"] == 401

    def test_bearer_prefix_stripped(self):
        event = _ws_event(headers={"Authorization": "Bearer valid.jwt.token"})
        resp  = self.handler(event, {})
        assert resp["statusCode"] == 200

    def test_lowercase_authorization_header(self):
        event = _ws_event(headers={"authorization": "Bearer valid.jwt.token"})
        resp  = self.handler(event, {})
        assert resp["statusCode"] == 200

    def test_empty_body_ignored(self):
        event = _ws_event(headers={"Authorization": "Bearer valid.jwt.token"}, body=None)
        resp  = self.handler(event, {})
        assert resp["statusCode"] == 200

    def test_dynamodb_not_called_on_401(self):
        event = _ws_event(headers={"Authorization": "Bearer bad.token"})
        self.handler(event, {})
        self.mock_table.put_item.assert_not_called()


# ═════════════════════════════════════════════════════════════
# TESTS — ws-disconnect-lambda  (target >= 70%)
# ═════════════════════════════════════════════════════════════

class TestWsDisconnect:

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")

        self.fake_redis = FakeRedis()
        self.fake_redis._data["connections"] = {"conn-abc": "user-123"}
        fake_redis_mod.Redis.from_url = MagicMock(return_value=self.fake_redis)

        import importlib
        import functions.ws_disconnect.handler as mod
        importlib.reload(mod)
        self.handler = mod.lambda_handler

    def test_returns_200(self):
        event = _ws_event(connection_id="conn-abc")
        resp  = self.handler(event, {})
        assert resp["statusCode"] == 200

    def test_removes_from_redis(self):
        event = _ws_event(connection_id="conn-abc")
        self.handler(event, {})
        assert "conn-abc" not in self.fake_redis._data.get("connections", {})

    def test_redis_fail_still_returns_200(self):
        """Redis DEL fail → non-critical, 200 return hona chahiye."""
        self.fake_redis.hdel = MagicMock(side_effect=FakeRedisError("timeout"))
        event = _ws_event(connection_id="conn-abc")
        resp  = self.handler(event, {})
        assert resp["statusCode"] == 200

    def test_missing_connection_id_handled(self):
        event = {"requestContext": {}, "headers": {}}
        resp  = self.handler(event, {})
        assert resp["statusCode"] == 200

    def test_non_existent_connection_still_200(self):
        event = _ws_event(connection_id="ghost-conn")
        resp  = self.handler(event, {})
        assert resp["statusCode"] == 200


# ═════════════════════════════════════════════════════════════
# TESTS — ws-message-lambda  (target >= 75%)
# ═════════════════════════════════════════════════════════════

class TestWsMessage:

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        monkeypatch.setenv("TABLE_ORDER", "OrderTable-dev")
        monkeypatch.setenv("STEP_ARN",    "arn:aws:states:us-east-1:123:stateMachine:test")
        monkeypatch.setenv("DLQ_URL",     "https://sqs.us-east-1.amazonaws.com/123/test-dlq")

        self.mock_table = MagicMock()
        self.mock_sfn   = MagicMock()
        self.mock_sqs   = MagicMock()

        mock_dynamodb = MagicMock()
        mock_dynamodb.Table.return_value = self.mock_table

        with patch("boto3.resource", return_value=mock_dynamodb), \
             patch("boto3.client", side_effect=lambda svc: {
                 "stepfunctions": self.mock_sfn,
                 "sqs":           self.mock_sqs,
             }[svc]):
            import importlib
            import functions.ws_message.handler as mod
            importlib.reload(mod)
            self.handler = mod.lambda_handler

    def _msg_event(self, body: dict, connection_id="conn-abc"):
        return _ws_event(
            route="$default",
            connection_id=connection_id,
            body=json.dumps(body),
        )

    # ── Happy path ────────────────────────────────────────
    def test_valid_status_returns_200(self):
        event = self._msg_event({"status": "confirmed", "taskToken": "tok-1"})
        resp  = self.handler(event, {})
        assert resp["statusCode"] == 200

    def test_order_saved_to_dynamodb(self):
        event = self._msg_event({"status": "pending", "orderId": "ord-99"})
        self.handler(event, {})
        self.mock_table.put_item.assert_called_once()
        item = self.mock_table.put_item.call_args[1]["Item"]
        assert item["status"]  == "pending"
        assert item["orderId"] == "ord-99"

    def test_sfn_called_when_task_token_present(self):
        event = self._msg_event({"status": "confirmed", "taskToken": "my-tok"})
        self.handler(event, {})
        self.mock_sfn.send_task_success.assert_called_once()
        args = self.mock_sfn.send_task_success.call_args[1]
        assert args["taskToken"] == "my-tok"

    def test_sfn_not_called_without_task_token(self):
        event = self._msg_event({"status": "pending"})
        self.handler(event, {})
        self.mock_sfn.send_task_success.assert_not_called()

    def test_auto_generates_order_id_when_missing(self):
        event = self._msg_event({"status": "processing"})
        resp  = self.handler(event, {})
        body  = json.loads(resp["body"])
        assert len(body["orderId"]) == 36   # UUID length

    # ── Error paths ───────────────────────────────────────
    def test_invalid_status_returns_400(self):
        event = self._msg_event({"status": "flying"})
        resp  = self.handler(event, {})
        assert resp["statusCode"] == 400

    def test_empty_status_returns_400(self):
        event = self._msg_event({"status": ""})
        resp  = self.handler(event, {})
        assert resp["statusCode"] == 400

    def test_invalid_json_returns_400(self):
        bad_event = _ws_event(body="not-json")
        resp = self.handler(bad_event, {})
        assert resp["statusCode"] == 400

    def test_sfn_failure_sends_to_dlq(self):
        from botocore.exceptions import ClientError
        self.mock_sfn.send_task_success.side_effect = ClientError(
            {"Error": {"Code": "TaskTimedOut", "Message": "timeout"}},
            "SendTaskSuccess",
        )
        event = self._msg_event({"status": "confirmed", "taskToken": "tok-fail"})
        resp  = self.handler(event, {})
        # Still returns 200 (client-side operation succeeded)
        assert resp["statusCode"] == 200
        self.mock_sqs.send_message.assert_called_once()

    def test_sfn_failure_dlq_contains_connection_id(self):
        from botocore.exceptions import ClientError
        self.mock_sfn.send_task_success.side_effect = ClientError(
            {"Error": {"Code": "InvalidToken", "Message": "bad"}},
            "SendTaskSuccess",
        )
        event = self._msg_event({"status": "confirmed", "taskToken": "t"}, connection_id="conn-xyz")
        self.handler(event, {})
        dlq_body = json.loads(self.mock_sqs.send_message.call_args[1]["MessageBody"])
        assert dlq_body["connectionId"] == "conn-xyz"

    def test_all_valid_statuses_accepted(self):
        for status in ["pending", "confirmed", "processing", "cancelled", "delivered"]:
            event = self._msg_event({"status": status})
            resp  = self.handler(event, {})
            assert resp["statusCode"] == 200, f"Failed for status: {status}"
