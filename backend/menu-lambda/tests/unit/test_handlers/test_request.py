"""tests/unit/test_handlers/test_request.py"""
from __future__ import annotations

import json
import pytest

from handlers.request import parse_event, RequestContext


def _event(method="GET", path="/menus/restaurants", body=None, headers=None,
           path_params=None, query=None):
    return {
        "httpMethod":            method,
        "path":                  path,
        "pathParameters":        path_params or {},
        "queryStringParameters": query or {},
        "headers":               headers or {},
        "body":                  json.dumps(body) if isinstance(body, dict) else body,
        "isBase64Encoded":       False,
    }


class TestParseEvent:
    def test_basic_get(self):
        ctx = parse_event(_event("GET", "/menus/restaurants"))
        assert ctx.method == "GET"
        assert ctx.path   == "/menus/restaurants"

    def test_method_uppercased(self):
        ctx = parse_event(_event("post", "/menus/restaurants"))
        assert ctx.method == "POST"

    def test_json_body_parsed(self):
        ctx = parse_event(_event("POST", "/menus/restaurants", body={"name": "Fork"}))
        assert ctx.body["name"] == "Fork"

    def test_empty_body_gives_empty_dict(self):
        ctx = parse_event(_event("GET", "/menus/restaurants", body=None))
        assert ctx.body == {}

    def test_invalid_json_body_gives_empty_dict(self):
        event = _event()
        event["body"] = "not-json{{"
        ctx = parse_event(event)
        assert ctx.body == {}

    def test_tenant_id_from_header(self):
        ctx = parse_event(_event(headers={"X-Tenant-Id": "tenant-xyz"}))
        assert ctx.tenant_id == "tenant-xyz"

    def test_tenant_id_from_body(self):
        ctx = parse_event(_event(body={"tenantId": "body-tenant"}))
        assert ctx.tenant_id == "body-tenant"

    def test_header_takes_priority_over_body_tenant(self):
        ctx = parse_event(_event(
            headers={"X-Tenant-Id": "header-tenant"},
            body={"tenantId": "body-tenant"},
        ))
        assert ctx.tenant_id == "header-tenant"

    def test_path_params_extracted(self):
        ctx = parse_event(_event(path_params={"restaurantId": "r-123"}))
        assert ctx.path_params["restaurantId"] == "r-123"

    def test_query_params_extracted(self):
        ctx = parse_event(_event(query={"cursor": "abc", "limit": "10"}))
        assert ctx.query_params["cursor"] == "abc"

    def test_headers_lowercased(self):
        ctx = parse_event(_event(headers={"Content-Type": "application/json"}))
        assert "content-type" in ctx.headers

    def test_is_multipart_false_for_json(self):
        ctx = parse_event(_event(headers={"Content-Type": "application/json"}))
        assert ctx.is_multipart is False

    def test_is_multipart_true_for_formdata(self):
        ctx = parse_event(_event(headers={
            "Content-Type": "multipart/form-data; boundary=----WebKitFormBoundary"
        }))
        assert ctx.is_multipart is True
