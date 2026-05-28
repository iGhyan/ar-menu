"""
shared/exceptions.py
====================
Typed exception hierarchy for the GLB Validator service.

All exceptions carry:
  - error_code   : machine-readable string  (use in logs + responses)
  - message      : human-readable string
  - http_status  : suggested HTTP status code (useful if ever exposed via API GW)
"""

from __future__ import annotations


# ── Base ──────────────────────────────────────────────────────────────────────

class AppBaseException(Exception):
    """Root for all application-level exceptions."""

    error_code:  str = "INTERNAL_ERROR"
    http_status: int = 500

    def __init__(self, message: str, **context):
        super().__init__(message)
        self.message = message
        self.context = context          # arbitrary k/v for structured logging

    def to_dict(self) -> dict:
        return {
            "error_code":  self.error_code,
            "message":     self.message,
            "http_status": self.http_status,
            **self.context,
        }


# ── Validation errors (4xx) ───────────────────────────────────────────────────

class ValidationError(AppBaseException):
    error_code  = "VALIDATION_ERROR"
    http_status = 422


class FileSizeError(ValidationError):
    error_code = "FILE_SIZE_EXCEEDED"

    def __init__(self, max_mb: int, actual_bytes: int):
        actual_mb = round(actual_bytes / (1024 * 1024), 2)
        super().__init__(
            f"File size {actual_mb} MB exceeds maximum {max_mb} MB.",
            max_mb=max_mb,
            actual_bytes=actual_bytes,
        )
        self.max_mb       = max_mb
        self.actual_bytes = actual_bytes


class FileFormatError(ValidationError):
    error_code = "INVALID_FILE_FORMAT"

    def __init__(self, reason: str, **context):
        super().__init__(reason, **context)


class MagicBytesError(FileFormatError):
    error_code = "INVALID_MAGIC_BYTES"

    def __init__(self, expected: int, actual: int):
        super().__init__(
            f"File header magic mismatch — not a GLB binary. "
            f"Expected 0x{expected:08X}, got 0x{actual:08X}.",
            expected_magic=f"0x{expected:08X}",
            actual_magic=f"0x{actual:08X}",
        )


class GlbVersionError(FileFormatError):
    error_code = "UNSUPPORTED_GLB_VERSION"

    def __init__(self, supported: int, actual: int):
        super().__init__(
            f"Unsupported GLB version {actual}. Only version {supported} is accepted.",
            supported_version=supported,
            actual_version=actual,
        )


class PolygonCountError(ValidationError):
    error_code = "POLYGON_LIMIT_EXCEEDED"

    def __init__(self, limit: int, actual: int):
        super().__init__(
            f"Polygon count {actual:,} exceeds the maximum of {limit:,}.",
            polygon_limit=limit,
            actual_polygon_count=actual,
        )


class FileExtensionError(ValidationError):
    error_code = "INVALID_FILE_EXTENSION"

    def __init__(self, filename: str):
        super().__init__(
            f"File must have a .glb extension, got: {filename}",
            filename=filename,
        )


class MalformedHeaderError(FileFormatError):
    error_code = "MALFORMED_HEADER"

    def __init__(self, detail: str = ""):
        super().__init__(
            f"Malformed file: incomplete GLB header. {detail}".strip(),
        )


# ── Tenant / authorisation errors (403 / 400) ─────────────────────────────────

class TenantKeyError(AppBaseException):
    error_code  = "INVALID_TENANT_KEY"
    http_status = 400

    def __init__(self, key: str, reason: str):
        super().__init__(
            f"Invalid S3 key structure: {reason}",
            s3_key=key,
            reason=reason,
        )
        self.s3_key = key


class TenantMismatchError(AppBaseException):
    error_code  = "TENANT_MISMATCH"
    http_status = 403

    def __init__(self, expected: str, actual: str):
        super().__init__(
            "Tenant ID in S3 key does not match authenticated tenant.",
            expected_tenant=expected,
            actual_tenant=actual,
        )


# ── Storage errors (502 / 503) ────────────────────────────────────────────────

class StorageError(AppBaseException):
    error_code  = "STORAGE_ERROR"
    http_status = 502


class S3ReadError(StorageError):
    error_code = "S3_READ_ERROR"

    def __init__(self, bucket: str, key: str, cause: str = ""):
        super().__init__(
            f"Failed to read s3://{bucket}/{key}. {cause}".strip(),
            bucket=bucket,
            s3_key=key,
        )
        self.bucket = bucket
        self.key    = key


class S3WriteError(StorageError):
    error_code = "S3_WRITE_ERROR"

    def __init__(self, bucket: str, key: str, cause: str = ""):
        super().__init__(
            f"Failed to write s3://{bucket}/{key}. {cause}".strip(),
            bucket=bucket,
            s3_key=key,
        )


# ── Authorization / Auth errors (401 / 403) ──────────────────────────────────

class AuthError(AppBaseException):
    error_code  = "AUTH_ERROR"
    http_status = 401

    def __init__(self, message: str = "Unauthorized", **context):
        super().__init__(message, **context)


class TokenMissingError(AuthError):
    error_code = "TOKEN_MISSING"

    def __init__(self):
        super().__init__("Authorization header missing.")


class TokenExpiredError(AuthError):
    error_code = "TOKEN_EXPIRED"

    def __init__(self):
        super().__init__("Token has expired.")


class TokenInvalidError(AuthError):
    error_code = "TOKEN_INVALID"

    def __init__(self, reason: str = ""):
        super().__init__(f"Invalid token. {reason}".strip())


class ForbiddenError(AppBaseException):
    error_code  = "FORBIDDEN"
    http_status = 403

    def __init__(self, message: str = "Forbidden", **context):
        super().__init__(message, **context)


class RbacError(ForbiddenError):
    error_code = "RBAC_INSUFFICIENT_ROLE"

    def __init__(self, required_roles: list[str], user_groups: list[str]):
        super().__init__(
            f"Access denied. Required roles: {required_roles}. "
            f"User roles: {user_groups}.",
            required_roles=required_roles,
            user_groups=user_groups,
        )


# ── Resource errors (404) ─────────────────────────────────────────────────────

class ResourceNotFoundError(AppBaseException):
    error_code  = "RESOURCE_NOT_FOUND"
    http_status = 404

    def __init__(self, resource: str, identifier: str = ""):
        msg = f"{resource} not found."
        if identifier:
            msg = f"{resource} '{identifier}' not found."
        super().__init__(msg, resource=resource, identifier=identifier)


# ── Request errors (400) ──────────────────────────────────────────────────────

class BadRequestError(AppBaseException):
    error_code  = "BAD_REQUEST"
    http_status = 400

    def __init__(self, message: str, **context):
        super().__init__(message, **context)


class MissingParameterError(BadRequestError):
    error_code = "MISSING_PARAMETER"

    def __init__(self, param_name: str):
        super().__init__(
            f"Required parameter '{param_name}' is missing.",
            parameter=param_name,
        )


class InvalidJsonError(BadRequestError):
    error_code = "INVALID_JSON"

    def __init__(self):
        super().__init__("Request body is not valid JSON.")


class NoValidFieldsError(BadRequestError):
    error_code = "NO_VALID_FIELDS"

    def __init__(self, allowed_fields: list[str]):
        super().__init__(
            f"No valid fields provided. Allowed: {allowed_fields}.",
            allowed_fields=allowed_fields,
        )


# ── External service errors ───────────────────────────────────────────────────

class CloudFrontError(AppBaseException):
    error_code  = "CLOUDFRONT_ERROR"
    http_status = 502

    def __init__(self, message: str = "CloudFront operation failed.", **context):
        super().__init__(message, **context)


class PresignError(StorageError):
    error_code = "PRESIGN_FAILED"

    def __init__(self, bucket: str, key: str, cause: str = ""):
        super().__init__(
            f"Failed to generate presigned URL for s3://{bucket}/{key}. {cause}".strip(),
            bucket=bucket,
            s3_key=key,
        )


# ── Notification errors (non-fatal) ───────────────────────────────────────────

class NotificationError(AppBaseException):
    error_code  = "NOTIFICATION_ERROR"
    http_status = 500

    def __init__(self, topic_arn: str, cause: str = ""):
        super().__init__(
            f"Failed to publish to SNS topic {topic_arn}. {cause}".strip(),
            topic_arn=topic_arn,
        )
