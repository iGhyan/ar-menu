"""
repository/s3.py — Generic S3 Repository.
Robust multipart parser that handles API Gateway base64 encoding.
"""
from __future__ import annotations

import base64
import email
import email.policy
import json
import os
import re
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

from utils.logger import get_logger

log = get_logger(__name__)

_BUCKET       = os.environ.get("S3_BUCKET", "menu-assets")
_READ_EXPIRY  = int(os.environ.get("S3_READ_URL_EXPIRY_SECONDS", "3600"))
_MAX_IMAGE_MB = int(os.environ.get("MAX_IMAGE_MB", "5"))
_MAX_AR_MB    = int(os.environ.get("MAX_AR_MB", "50"))

_IMAGE_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg":  ".jpg",
    "image/png":  ".png",
    "image/webp": ".webp",
}
_AR_CONTENT_TYPES = {
    "model/gltf-binary":        ".glb",
    "application/octet-stream": ".glb",
}


# ── Exceptions ────────────────────────────────────────────────────────────

class S3RepositoryError(Exception):
    pass

class FileTooLargeError(S3RepositoryError):
    pass

class InvalidContentTypeError(S3RepositoryError):
    pass

class MissingFileError(S3RepositoryError):
    pass

class MissingFieldError(S3RepositoryError):
    pass


# ── Parsed form result ────────────────────────────────────────────────────

class ParsedForm:
    def __init__(self):
        self.files:  dict[str, dict] = {}
        self.fields: dict[str, str]  = {}


# ── Multipart parser ──────────────────────────────────────────────────────

def _get_body_bytes(event: dict) -> bytes:
    body_raw = event.get("body") or b""
    is_b64   = event.get("isBase64Encoded", False)
    if is_b64:
        if isinstance(body_raw, str):
            return base64.b64decode(body_raw)
        return base64.b64decode(body_raw)
    if isinstance(body_raw, str):
        return body_raw.encode("latin-1")
    return body_raw


def _extract_boundary(ct_header: str) -> str:
    m = re.search(r'boundary=([^\s;,]+)', ct_header, re.IGNORECASE)
    if m:
        return m.group(1).strip('"\'')
    return ""


def _detect_content_type_from_filename(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".glb"):
        return "model/gltf-binary"
    return "application/octet-stream"


def parse_multipart(event: dict) -> ParsedForm:
    """
    Parse multipart/form-data from an API Gateway Lambda proxy event.
    Handles both base64-encoded and plain bodies.
    Uses Python's email library for robust MIME parsing.
    """
    result = ParsedForm()

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    ct      = headers.get("content-type", "")

    if "multipart/form-data" not in ct:
        log.warning("Not multipart", extra={"ct": ct})
        return result

    boundary = _extract_boundary(ct)
    if not boundary:
        log.warning("No boundary found", extra={"ct": ct})
        return result

    body_bytes = _get_body_bytes(event)

    log.info("Parsing multipart", extra={
        "boundary":  boundary,
        "body_size": len(body_bytes),
        "is_b64":    event.get("isBase64Encoded", False),
    })

    # Build a MIME message that email.parser can handle
    mime_header = f"MIME-Version: 1.0\r\nContent-Type: {ct}\r\n\r\n".encode()
    full_message = mime_header + body_bytes

    try:
        msg = email.message_from_bytes(
            full_message,
            policy=email.policy.compat32,
        )
    except Exception as exc:
        log.warning("email.message_from_bytes failed", extra={"error": str(exc)})
        return _parse_multipart_manual(body_bytes, boundary)

    if not msg.is_multipart():
        log.warning("Message is not multipart — trying manual parser")
        return _parse_multipart_manual(body_bytes, boundary)

    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue

        disposition = part.get("Content-Disposition", "")
        if not disposition:
            continue

        # Extract field name
        name_m = re.search(r'name=["\']?([^"\';\s]+)["\']?', disposition)
        if not name_m:
            continue
        field_name = name_m.group(1)

        # Check if file field
        fname_m = re.search(r'filename=["\']?([^"\';\s]*)["\']?', disposition)

        payload = part.get_payload(decode=True)
        if payload is None:
            payload = b""

        if fname_m:
            filename = fname_m.group(1)
            file_ct  = part.get_content_type() or ""
            if not file_ct or file_ct == "application/octet-stream":
                file_ct = _detect_content_type_from_filename(filename)

            result.files[field_name] = {
                "bytes":        payload,
                "file_name":     filename,
                "content_type": file_ct,
            }
            log.info("File field parsed", extra={
                "field":    field_name,
                "file_name": filename,
                "ct":       file_ct,
                "size":     len(payload),
            })
        else:
            try:
                result.fields[field_name] = payload.decode("utf-8")
            except Exception:
                result.fields[field_name] = payload.decode("latin-1", errors="replace")

    # Handle 'data' JSON blob
    if "data" in result.fields:
        try:
            parsed = json.loads(result.fields["data"])
            if isinstance(parsed, dict):
                merged = {**result.fields, **parsed}
                merged.pop("data", None)
                result.fields = merged
        except Exception:
            pass

    log.info("Multipart parsed", extra={
        "text_fields": list(result.fields.keys()),
        "file_fields": list(result.files.keys()),
    })
    return result


def _parse_multipart_manual(body_bytes: bytes, boundary: str) -> ParsedForm:
    """
    Fallback manual multipart parser when email library fails.
    """
    result = ParsedForm()
    boundary_bytes = ("--" + boundary).encode("latin-1")
    parts = body_bytes.split(boundary_bytes)

    log.info("Manual parser", extra={"parts": len(parts), "boundary": boundary})

    for part in parts:
        if not part:
            continue
        part = part.strip(b"\r\n")
        if part in (b"--", b""):
            continue

        sep = b"\r\n\r\n" if b"\r\n\r\n" in part else b"\n\n"
        if sep not in part:
            continue

        hdr_raw, _, body_part = part.partition(sep)
        body_part = body_part.rstrip(b"\r\n")

        part_headers: dict[str, str] = {}
        for line in hdr_raw.decode("latin-1", errors="replace").splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                part_headers[k.strip().lower()] = v.strip()

        disposition = part_headers.get("content-disposition", "")
        name_m  = re.search(r'name=["\']?([^"\';\r\n]+)["\']?', disposition)
        fname_m = re.search(r'filename=["\']?([^"\';\r\n]*)["\']?', disposition)

        if not name_m:
            continue

        field_name = name_m.group(1).strip()

        if fname_m:
            filename = fname_m.group(1).strip()
            file_ct  = part_headers.get("content-type", "").split(";")[0].strip()
            if not file_ct or file_ct == "application/octet-stream":
                file_ct = _detect_content_type_from_filename(filename)

            result.files[field_name] = {
                "bytes":        body_part,
                "file_name":     filename,
                "content_type": file_ct,
            }
            log.info("Manual: file field", extra={
                "field": field_name,
                "size": len(body_part),
            })
        else:
            try:
                result.fields[field_name] = body_part.decode("utf-8")
            except Exception:
                result.fields[field_name] = body_part.decode("latin-1", errors="replace")

    return result


def is_multipart(event: dict) -> bool:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    return "multipart/form-data" in headers.get("content-type", "")


# ── S3 Key builders ───────────────────────────────────────────────────────

def restaurant_logo_key(tenant_id: str, restaurant_id: str, ext: str) -> str:
    return f"TENANT#{tenant_id}/restaurants/{restaurant_id}/logo{ext}"

def category_image_key(tenant_id: str, restaurant_id: str, category_id: str, ext: str) -> str:
    return f"TENANT#{tenant_id}/restaurants/{restaurant_id}/categories/{category_id}{ext}"

def item_image_key(tenant_id: str, restaurant_id: str, item_id: str, ext: str) -> str:
    return f"TENANT#{tenant_id}/restaurants/{restaurant_id}/items/{item_id}{ext}"

def item_ar_key(tenant_id: str, restaurant_id: str, item_id: str) -> str:
    return f"TENANT#{tenant_id}/restaurants/{restaurant_id}/ar-models/{item_id}.glb"


# ── Validators ────────────────────────────────────────────────────────────

def validate_image(file_bytes: bytes, content_type: str) -> str:
    ct = content_type.lower().split(";")[0].strip()
    if ct not in _IMAGE_CONTENT_TYPES:
        raise InvalidContentTypeError(
            f"Invalid image type '{ct}'. Allowed: jpeg, png, webp"
        )
    if len(file_bytes) > _MAX_IMAGE_MB * 1024 * 1024:
        raise FileTooLargeError(f"Image exceeds {_MAX_IMAGE_MB}MB limit")
    return _IMAGE_CONTENT_TYPES[ct]


def validate_ar(file_bytes: bytes, content_type: str) -> None:
    ct = content_type.lower().split(";")[0].strip()
    if ct not in _AR_CONTENT_TYPES:
        raise InvalidContentTypeError(
            f"Invalid AR type '{ct}'. Only .glb files allowed"
        )
    if len(file_bytes) > _MAX_AR_MB * 1024 * 1024:
        raise FileTooLargeError(f"AR model exceeds {_MAX_AR_MB}MB limit")


# ── S3Repository ──────────────────────────────────────────────────────────

class S3Repository:

    def __init__(self, s3_client=None) -> None:
        self._s3 = s3_client or boto3.client("s3")

    def upload(self, file_bytes: bytes, s3_key: str, content_type: str) -> str:
        try:
            self._s3.put_object(
                Bucket=_BUCKET, Key=s3_key,
                Body=file_bytes, ContentType=content_type,
            )
            log.info("S3 upload OK", extra={"key": s3_key, "size": len(file_bytes)})
            return s3_key
        except ClientError as exc:
            log.error("S3 upload failed", extra={"key": s3_key, "error": str(exc)})
            raise

    def get_read_url(self, s3_key: Optional[str]) -> Optional[str]:
        if not s3_key:
            return None
        try:
            return self._s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": _BUCKET, "Key": s3_key},
                ExpiresIn=_READ_EXPIRY,
            )
        except ClientError:
            return None

    def upload_restaurant_logo(
        self, event: dict, restaurant_id: str, tenant_id: str
    ) -> tuple[Optional[str], Optional[str]]:
        form      = parse_multipart(event)
        file_info = form.files.get("file")
        if not file_info or not file_info["bytes"]:
            log.info("No logo file in request")
            return None, None
        ext    = validate_image(file_info["bytes"], file_info["content_type"])
        s3_key = restaurant_logo_key(tenant_id, restaurant_id, ext)
        self.upload(file_info["bytes"], s3_key, file_info["content_type"])
        return s3_key, self.get_read_url(s3_key)

    def upload_category_image(
        self, event: dict, restaurant_id: str, category_id: str, tenant_id: str
    ) -> tuple[Optional[str], Optional[str]]:
        form      = parse_multipart(event)
        file_info = form.files.get("file")
        if not file_info or not file_info["bytes"]:
            log.info("No category image in request")
            return None, None
        ext    = validate_image(file_info["bytes"], file_info["content_type"])
        s3_key = category_image_key(tenant_id, restaurant_id, category_id, ext)
        self.upload(file_info["bytes"], s3_key, file_info["content_type"])
        return s3_key, self.get_read_url(s3_key)

    def upload_item_assets(
        self, event: dict, restaurant_id: str, item_id: str, tenant_id: str
    ) -> dict[str, Any]:
        """
        Upload item image (file) and/or AR model (arFile) from one request.
        Returns dict with imageKey, imageUrl, arModelKey, arModelUrl.
        """
        form   = parse_multipart(event)
        result = {
            "imageKey":   None,
            "imageUrl":   None,
            "arModelKey": None,
            "arModelUrl": None,
        }

        # Upload image
        file_info = form.files.get("file")
        if file_info and file_info["bytes"]:
            try:
                ext    = validate_image(file_info["bytes"], file_info["content_type"])
                s3_key = item_image_key(tenant_id, restaurant_id, item_id, ext)
                self.upload(file_info["bytes"], s3_key, file_info["content_type"])
                result["imageKey"] = s3_key
                result["imageUrl"] = self.get_read_url(s3_key)
            except Exception as exc:
                log.warning("Item image upload failed", extra={"error": str(exc)})
        else:
            log.info("No image file field found", extra={"fields": list(form.files.keys())})

        # Upload AR model
        ar_info = form.files.get("arFile")
        if ar_info and ar_info["bytes"]:
            try:
                ct = ar_info["content_type"] or "model/gltf-binary"
                if ct == "application/octet-stream":
                    ct = "model/gltf-binary"
                validate_ar(ar_info["bytes"], ct)
                s3_key = item_ar_key(tenant_id, restaurant_id, item_id)
                self.upload(ar_info["bytes"], s3_key, ct)
                result["arModelKey"] = s3_key
                result["arModelUrl"] = self.get_read_url(s3_key)
            except Exception as exc:
                log.warning("AR model upload failed", extra={"error": str(exc)})
        else:
            log.info("No arFile field found", extra={"fields": list(form.files.keys())})

        return result