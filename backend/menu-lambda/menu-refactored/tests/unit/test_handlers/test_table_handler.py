"""tests/unit/test_handlers/test_table_handler.py"""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError

from tests.conftest import TENANT_ID, RESTAURANT_ID, api_event


def _ce(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


def _make_ctx(method: str, body: dict = None, table_id: str = "",
              tenant_id: str = TENANT_ID):
    ctx = MagicMock()
    ctx.method        = method
    ctx.tenant_id     = tenant_id
    ctx.body          = body or {}
    ctx.path_params   = {"restaurantId": RESTAURANT_ID}
    if table_id:
        ctx.path_params["tableId"] = table_id
    return ctx


TABLE_BODY = {
    "tableNumber": "T-01",
    "zone":        "Main Hall",
    "outlet":      "Main Hall",
    "capacity":    4,
}

SAMPLE_ITEM = {
    "PK":           f"TENANT#{TENANT_ID}#RESTAURANT#{RESTAURANT_ID}",
    "SK":           "TABLE#table-uuid-1",
    "tableId":      "table-uuid-1",
    "tableNumber":  "T-01",
    "zone":         "Main Hall",
    "outlet":       "Main Hall",
    "capacity":     4,
    "isActive":     True,
    "tenantId":     TENANT_ID,
    "restaurantId": RESTAURANT_ID,
    "createdAt":    "2026-05-01T10:00:00Z",
    "updatedAt":    "2026-05-01T10:00:00Z",
}


class TestHandleTableGet:
    def test_list_returns_200(self):
        mock_table = MagicMock()
        mock_table.query.return_value = {"Items": [SAMPLE_ITEM]}

        with patch("handlers.table_handler._table", mock_table):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx("GET"))

        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["count"] == 1
        assert body["tables"][0]["tableId"] == "table-uuid-1"

    def test_list_strips_pk_sk(self):
        mock_table = MagicMock()
        mock_table.query.return_value = {"Items": [SAMPLE_ITEM]}

        with patch("handlers.table_handler._table", mock_table):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx("GET"))

        body = json.loads(resp["body"])
        for t in body["tables"]:
            assert "PK" not in t
            assert "SK" not in t

    def test_list_empty_returns_200(self):
        mock_table = MagicMock()
        mock_table.query.return_value = {"Items": []}

        with patch("handlers.table_handler._table", mock_table):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx("GET"))

        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["count"] == 0

    def test_list_missing_restaurant_id_returns_400(self):
        ctx = _make_ctx("GET")
        ctx.path_params = {}

        with patch("handlers.table_handler._table", MagicMock()):
            from handlers.table_handler import handle_table
            resp = handle_table(ctx)

        assert resp["statusCode"] == 400

    def test_list_missing_tenant_id_returns_400(self):
        with patch("handlers.table_handler._table", MagicMock()):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx("GET", tenant_id=""))

        assert resp["statusCode"] == 400

    def test_list_ddb_error_returns_500(self):
        mock_table = MagicMock()
        mock_table.query.side_effect = _ce("InternalServerError")

        with patch("handlers.table_handler._table", mock_table):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx("GET"))

        assert resp["statusCode"] == 500


class TestHandleTablePost:
    def test_create_returns_201(self):
        mock_table = MagicMock()
        mock_table.put_item.return_value = {}

        with patch("handlers.table_handler._table", mock_table), \
             patch("handlers.table_handler.new_id", return_value="new-table-uuid"), \
             patch("handlers.table_handler.utc_now", return_value="2026-05-01T10:00:00Z"):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx("POST", body=TABLE_BODY))

        assert resp["statusCode"] == 201
        body = json.loads(resp["body"])
        assert body["tableId"] == "new-table-uuid"
        assert body["tableNumber"] == "T-01"

    def test_create_missing_table_number_returns_400(self):
        with patch("handlers.table_handler._table", MagicMock()):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx("POST", body={"zone": "Main Hall"}))

        assert resp["statusCode"] == 400

    def test_create_invalid_capacity_returns_400(self):
        body = {**TABLE_BODY, "capacity": "not-a-number"}
        with patch("handlers.table_handler._table", MagicMock()):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx("POST", body=body))

        assert resp["statusCode"] == 400

    def test_create_outlet_defaults_to_zone(self):
        mock_table = MagicMock()
        mock_table.put_item.return_value = {}
        body = {"tableNumber": "T-02", "zone": "Rooftop", "capacity": 2}

        with patch("handlers.table_handler._table", mock_table), \
             patch("handlers.table_handler.new_id", return_value="uuid-2"), \
             patch("handlers.table_handler.utc_now", return_value="2026-05-01T00:00:00Z"):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx("POST", body=body))

        assert resp["statusCode"] == 201
        result = json.loads(resp["body"])
        assert result["outlet"] == "Rooftop"  # defaults to zone

    def test_create_response_has_no_pk_sk(self):
        mock_table = MagicMock()
        mock_table.put_item.return_value = {}

        with patch("handlers.table_handler._table", mock_table), \
             patch("handlers.table_handler.new_id", return_value="uuid-3"), \
             patch("handlers.table_handler.utc_now", return_value="2026-05-01T00:00:00Z"):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx("POST", body=TABLE_BODY))

        body = json.loads(resp["body"])
        assert "PK" not in body
        assert "SK" not in body

    def test_create_ddb_error_returns_500(self):
        mock_table = MagicMock()
        mock_table.put_item.side_effect = _ce("InternalServerError")

        with patch("handlers.table_handler._table", mock_table):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx("POST", body=TABLE_BODY))

        assert resp["statusCode"] == 500


class TestHandleTablePut:
    def test_update_returns_200(self):
        mock_table = MagicMock()
        mock_table.update_item.return_value = {}

        with patch("handlers.table_handler._table", mock_table), \
             patch("handlers.table_handler.utc_now", return_value="2026-05-01T12:00:00Z"):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx(
                "PUT", body={"capacity": 6}, table_id="table-1"
            ))

        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["tableId"] == "table-1"
        assert "capacity" in body["updated"]

    def test_update_no_valid_fields_returns_400(self):
        with patch("handlers.table_handler._table", MagicMock()):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx(
                "PUT", body={"unknownField": "x"}, table_id="table-1"
            ))

        assert resp["statusCode"] == 400

    def test_update_missing_table_id_returns_400(self):
        with patch("handlers.table_handler._table", MagicMock()):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx("PUT", body={"capacity": 4}))

        assert resp["statusCode"] == 400

    def test_update_table_not_found_returns_404(self):
        mock_table = MagicMock()
        mock_table.update_item.side_effect = _ce("ConditionalCheckFailedException")

        with patch("handlers.table_handler._table", mock_table):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx(
                "PUT", body={"capacity": 6}, table_id="ghost-table"
            ))

        assert resp["statusCode"] == 404

    def test_update_ddb_error_returns_500(self):
        mock_table = MagicMock()
        mock_table.update_item.side_effect = _ce("InternalServerError")

        with patch("handlers.table_handler._table", mock_table):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx(
                "PUT", body={"isActive": False}, table_id="table-1"
            ))

        assert resp["statusCode"] == 500

    def test_all_allowed_fields_accepted(self):
        mock_table = MagicMock()
        mock_table.update_item.return_value = {}
        body = {
            "tableNumber": "T-99",
            "zone":        "VIP",
            "outlet":      "VIP Lounge",
            "capacity":    10,
            "isActive":    False,
        }

        with patch("handlers.table_handler._table", mock_table), \
             patch("handlers.table_handler.utc_now", return_value="2026-05-01T12:00:00Z"):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx("PUT", body=body, table_id="table-1"))

        assert resp["statusCode"] == 200
        result = json.loads(resp["body"])
        assert len(result["updated"]) >= 5   # all 5 + updatedAt


class TestHandleTableDelete:
    def test_delete_returns_200(self):
        mock_table = MagicMock()
        mock_table.delete_item.return_value = {}

        with patch("handlers.table_handler._table", mock_table):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx("DELETE", table_id="table-1"))

        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["tableId"]  == "table-1"
        assert body["deleted"]  is True

    def test_delete_missing_table_id_returns_400(self):
        with patch("handlers.table_handler._table", MagicMock()):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx("DELETE"))

        assert resp["statusCode"] == 400

    def test_delete_table_not_found_returns_404(self):
        mock_table = MagicMock()
        mock_table.delete_item.side_effect = _ce("ConditionalCheckFailedException")

        with patch("handlers.table_handler._table", mock_table):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx("DELETE", table_id="ghost-table"))

        assert resp["statusCode"] == 404

    def test_delete_ddb_error_returns_500(self):
        mock_table = MagicMock()
        mock_table.delete_item.side_effect = _ce("InternalServerError")

        with patch("handlers.table_handler._table", mock_table):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx("DELETE", table_id="table-1"))

        assert resp["statusCode"] == 500


class TestHandleTableMethodNotAllowed:
    def test_patch_returns_400(self):
        with patch("handlers.table_handler._table", MagicMock()):
            from handlers.table_handler import handle_table
            resp = handle_table(_make_ctx("PATCH", body={"capacity": 4}))

        assert resp["statusCode"] == 400
