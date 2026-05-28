"""
glb_validator/validator.py
===========================
`GlbValidator` — orchestrates all validation checks for a single GLB file.

Flow
----
1. check_file_size      (uses S3 event size — no download needed)
2. _fetch_header_bytes  (byte-range GET: bytes 0 … GLB_HEADER_SIZE+CHUNK_HEADER_SIZE-1)
3. check_magic_and_version
4. _fetch_full_bytes    (full GET — only if header passes)
5. check_polygon_count

Any check can raise a `ValidationError` subclass, which `GlbValidationService`
(in lambda_function.py) catches to produce a rejection result.
S3 failures raise `S3ReadError` (a `StorageError`).

Usage
-----
    from glb_validator.validator import GlbValidator

    validator = GlbValidator(s3_client=get_s3_client())
    validator.validate(bucket="my-bucket", key="uploads/…/model.glb", size=12345)
    # Raises a ValidationError subclass on failure, returns None on success.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from glb_validator.checks import (
    GLB_HEADER_SIZE,
    CHUNK_HEADER_SIZE,
    check_file_size,
    check_magic_and_version,
    check_polygon_count,
)
from shared.exceptions import S3ReadError
from shared.structured_logger import get_logger

_log = get_logger("glb-validator.validator")

# Number of bytes to fetch for the header + first chunk header
_HEADER_RANGE_END = GLB_HEADER_SIZE + CHUNK_HEADER_SIZE - 1


class GlbValidator:
    """
    Validates a GLB asset stored in S3.

    Parameters
    ----------
    s3_client : boto3 S3 client
        Injected to allow easy mocking in tests.
    """

    def __init__(self, s3_client: Any):
        self._s3 = s3_client

    # ── Public ────────────────────────────────────────────────────────────────

    def validate(self, bucket: str, key: str, size: int) -> None:
        """
        Run all GLB validation checks.

        Returns
        -------
        None — if all checks pass.

        Raises
        ------
        FileSizeError         — size exceeds limit
        MalformedHeaderError  — GLB header < 12 bytes
        MagicBytesError       — not a GLB binary
        GlbVersionError       — unsupported GLB version
        PolygonCountError     — too many triangles
        S3ReadError           — S3 could not be read
        """
        # ── Check 1: file size (no S3 call needed) ────────────────────────────
        check_file_size(size)
        _log.debug("check.size.passed", key=key, size=size)

        # ── Check 2: fetch header bytes ───────────────────────────────────────
        header_bytes = self._get_bytes(bucket, key, 0, _HEADER_RANGE_END)

        # ── Check 3: magic + version ──────────────────────────────────────────
        check_magic_and_version(header_bytes)
        _log.debug("check.magic.passed", key=key)

        # ── Check 4: fetch full file for polygon count ─────────────────────────
        # Only reached if header is valid — avoids downloading junk files.
        full_bytes = self._get_bytes(bucket, key, 0, size - 1)

        # ── Check 5: polygon count ────────────────────────────────────────────
        check_polygon_count(full_bytes)
        _log.debug("check.polygon.passed", key=key)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_bytes(self, bucket: str, key: str, start: int, end: int) -> bytes:
        """
        Fetch *key* from *bucket* using a Range request.

        Raises
        ------
        S3ReadError — on any ClientError
        """
        try:
            response = self._s3.get_object(
                Bucket=bucket,
                Key=key,
                Range=f"bytes={start}-{end}",
            )
            return response["Body"].read()
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "Unknown")
            raise S3ReadError(
                bucket=bucket,
                key=key,
                cause=f"AWS error {error_code}: {exc}",
            ) from exc
