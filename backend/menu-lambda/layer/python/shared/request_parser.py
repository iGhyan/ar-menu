"""
shared/request_parser.py
=========================
Normalises Lambda event payloads from different S3 trigger sources into a
uniform list of `S3Record` dataclasses.

Supported sources
-----------------
* Native S3 notifications  (event["Records"][N]["s3"])
* EventBridge (CloudTrail) (event["detail-type"] + event["detail"])

Usage
-----
    from shared.request_parser import parse_event, S3Record

    records: list[S3Record] = parse_event(event)
    for rec in records:
        print(rec.bucket, rec.key, rec.size)
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class S3Record:
    """
    Normalised representation of a single S3 object-created event.

    Attributes
    ----------
    bucket     : S3 bucket name
    key        : URL-decoded S3 object key
    size       : Object size in bytes (0 if not provided by the event source)
    event_time : UTC datetime of the event (now() if not provided)
    source     : "native_s3" | "eventbridge"
    etag       : Object ETag when available (empty string otherwise)
    """

    bucket:     str
    key:        str
    size:       int
    event_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source:     Literal["native_s3", "eventbridge"] = "native_s3"
    etag:       str = ""


# ── Public entry point ────────────────────────────────────────────────────────

def parse_event(event: dict) -> list[S3Record]:
    """
    Auto-detect event source and return a list of normalised S3Records.

    Raises
    ------
    ValueError
        If the event structure is unrecognised.
    """
    if _is_eventbridge(event):
        return _parse_eventbridge(event)

    if _is_native_s3(event):
        return _parse_native_s3(event)

    raise ValueError(
        f"Unrecognised event structure — missing 'Records' or 'detail-type'. "
        f"Top-level keys: {list(event.keys())}"
    )


# ── EventBridge parser ────────────────────────────────────────────────────────

def _is_eventbridge(event: dict) -> bool:
    return "detail-type" in event and "detail" in event


def _parse_eventbridge(event: dict) -> list[S3Record]:
    detail = event.get("detail", {})
    bucket = detail.get("bucket", {}).get("name", "")
    obj    = detail.get("object", {})

    raw_key    = obj.get("key", "")
    key        = urllib.parse.unquote_plus(raw_key)
    size       = int(obj.get("size", 0))
    etag       = obj.get("etag", "").strip('"')
    event_time = _parse_time(event.get("time", ""))

    return [S3Record(
        bucket=bucket,
        key=key,
        size=size,
        event_time=event_time,
        source="eventbridge",
        etag=etag,
    )]


# ── Native S3 parser ──────────────────────────────────────────────────────────

def _is_native_s3(event: dict) -> bool:
    return "Records" in event


def _parse_native_s3(event: dict) -> list[S3Record]:
    records = []
    for raw in event.get("Records", []):
        s3_data = raw.get("s3", {})
        bucket  = s3_data.get("bucket", {}).get("name", "")
        obj     = s3_data.get("object", {})

        raw_key    = obj.get("key", "")
        key        = urllib.parse.unquote_plus(raw_key)
        size       = int(obj.get("size", 0))
        etag       = obj.get("eTag", "").strip('"')
        event_time = _parse_time(raw.get("eventTime", ""))

        records.append(S3Record(
            bucket=bucket,
            key=key,
            size=size,
            event_time=event_time,
            source="native_s3",
            etag=etag,
        ))
    return records


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_time(raw: str) -> datetime:
    """Parse ISO-8601 string; return utcnow() if blank or malformed."""
    if not raw:
        return datetime.now(timezone.utc)
    try:
        # Python 3.11+ handles Z; replace for 3.9/3.10 compatibility
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
