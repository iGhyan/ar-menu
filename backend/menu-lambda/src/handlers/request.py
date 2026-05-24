"""
Request context helpers — supports JSON and multipart/form-data.
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any

from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class RequestContext:
    method:       str
    path:         str
    path_params:  dict[str, str]
    query_params: dict[str, str]
    headers:      dict[str, str]
    body:         dict[str, Any]
    tenant_id:    str
    raw_event:    dict
    is_multipart: bool = False


def parse_event(event: dict) -> RequestContext:
    method = (event.get("httpMethod") or "GET").upper()
    path   = event.get("path") or "/"

    path_params:  dict[str, str] = event.get("pathParameters") or {}
    query_params: dict[str, str] = event.get("queryStringParameters") or {}

    raw_headers: dict[str, str] = event.get("headers") or {}
    headers = {k.lower(): v for k, v in raw_headers.items()}

    ct           = headers.get("content-type", "")
    is_multipart = "multipart/form-data" in ct

    log.info("Parsing request", extra={
        "method": method,
        "path": path,
        "content_type": ct,
        "is_multipart": is_multipart,
        "is_base64": event.get("isBase64Encoded", False),
    })

    body: dict[str, Any] = {}

    if is_multipart:
        body = _parse_form_text_fields(event, ct)
        log.info("Parsed form fields", extra={"fields": list(body.keys()), "body": body})
    else:
        raw_body = event.get("body") or ""
        if raw_body:
            try:
                parsed = json.loads(raw_body)
                if isinstance(parsed, dict):
                    body = parsed
            except (json.JSONDecodeError, ValueError):
                body = {}

    tenant_id = (
        headers.get("x-tenant-id")
        or body.get("tenantId")
        or ""
    )

    return RequestContext(
        method=method,
        path=path,
        path_params=path_params,
        query_params=query_params,
        headers=headers,
        body=body,
        tenant_id=tenant_id,
        raw_event=event,
        is_multipart=is_multipart,
    )


def _get_body_bytes(event: dict) -> bytes:
    body_raw = event.get("body") or ""
    is_b64   = event.get("isBase64Encoded", False)

    if is_b64:
        return base64.b64decode(body_raw)
    if isinstance(body_raw, str):
        return body_raw.encode("latin-1")
    return body_raw


def _parse_form_text_fields(event: dict, ct_header: str) -> dict[str, Any]:
    """
    Extract text fields from multipart/form-data.
    Skips file fields. Returns flat dict of field_name -> value.
    """
    body_bytes = _get_body_bytes(event)

    log.info("Multipart body size", extra={"size": len(body_bytes)})

    # Extract boundary — try multiple formats
    boundary = _extract_boundary(ct_header)
    if not boundary:
        log.warning("No boundary found in content-type", extra={"ct": ct_header})
        return {}

    log.info("Boundary found", extra={"boundary": boundary})

    boundary_bytes = ("--" + boundary).encode("latin-1")
    parts          = body_bytes.split(boundary_bytes)

    log.info("Parts count", extra={"count": len(parts)})

    fields: dict[str, str] = {}

    for i, part in enumerate(parts):
        if not part:
            continue
        # Remove leading/trailing CRLF
        part = part.strip(b"\r\n")
        # Skip terminator
        if part == b"--" or part == b"":
            continue

        # Split headers from body
        separator = b"\r\n\r\n" if b"\r\n\r\n" in part else b"\n\n"
        if separator not in part:
            continue

        hdr_raw, _, body_part = part.partition(separator)
        body_part = body_part.rstrip(b"\r\n")

        # Parse part headers
        part_headers: dict[str, str] = {}
        try:
            for line in hdr_raw.decode("utf-8", errors="replace").splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    part_headers[k.strip().lower()] = v.strip()
        except Exception:
            continue

        disposition = part_headers.get("content-disposition", "")
        name_m      = re.search(r'name=["\']?([^"\';\r\n]+)["\']?', disposition)
        fname_m     = re.search(r'filename=["\']?([^"\';\r\n]*)["\']?', disposition)

        if not name_m:
            continue

        field_name = name_m.group(1).strip()

        # Skip file fields
        if fname_m or "content-type" in part_headers:
            log.info("Skipping file field", extra={"field": field_name})
            continue

        try:
            value = body_part.decode("utf-8")
        except UnicodeDecodeError:
            value = body_part.decode("latin-1")

        fields[field_name] = value
        log.info("Parsed text field", extra={"field": field_name, "value": value})

    # Support 'data' field with full JSON
    if "data" in fields:
        try:
            parsed = json.loads(fields["data"])
            if isinstance(parsed, dict):
                merged = {**fields, **parsed}
                merged.pop("data", None)
                return merged
        except (json.JSONDecodeError, ValueError):
            pass

    return fields


def _extract_boundary(ct_header: str) -> str:
    """Extract boundary from Content-Type header, handling various formats."""
    # Standard: boundary=abc123
    m = re.search(r'boundary=([^\s;,]+)', ct_header, re.IGNORECASE)
    if m:
        return m.group(1).strip('"\'')

    # Quoted: boundary="abc123"
    m = re.search(r'boundary="([^"]+)"', ct_header, re.IGNORECASE)
    if m:
        return m.group(1)

    return ""