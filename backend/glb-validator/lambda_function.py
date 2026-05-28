"""
lambda_function.py
==================
GLB Validator Lambda — thin orchestration handler.

Trigger  : S3 ObjectCreated (EventBridge Rule or Native S3 notification)
Actions  : validate .glb → move to approved/ or rejected/
           notify admin via SNS on rejection

This file contains ONLY orchestration logic.
All business logic lives in:
  - glb_validator/  (validation checks)
  - layer/python/shared/  (AWS clients, error handling, logging, tenant isolation)

Env vars
--------
  ASSET_BUCKET_NAME  — S3 bucket name (replaces old BUCKET_AR)
  SNS_ADMIN_ARN      — SNS topic ARN for admin rejection alerts
  LOG_LEVEL          — optional, defaults to INFO
"""

from __future__ import annotations

import os
import json

from shared.aws_clients import get_s3_client, get_sns_client
from shared.exceptions import (
    AppBaseException,
    TenantKeyError,
    ValidationError,
    StorageError,
    NotificationError,
)
from shared.request_parser import parse_event, S3Record
from shared.response_builder import ResponseBuilder, bind_request_id
from shared.structured_logger import get_logger, bind_lambda_context
from shared.tenant_validator import extract_tenant_context, TenantContext

from glb_validator.models import ValidationResult
from glb_validator.validator import GlbValidator

# ── Module-level logger (reused across warm invocations) ──────────────────────
_log = get_logger("glb-validator")

# ── Env vars ──────────────────────────────────────────────────────────────────
_BUCKET_NAME  = os.environ["ASSET_BUCKET_NAME"]
_SNS_ADMIN    = os.environ["SNS_ADMIN_ARN"]


# ── Entry point ───────────────────────────────────────────────────────────────

def lambda_handler(event: dict, context) -> dict:
    """Process S3 ObjectCreated records from EventBridge or Native S3."""
    bind_lambda_context(context)
    bind_request_id(getattr(context, "aws_request_id", "unknown"))

    _log.info("handler.invoked", event_keys=list(event.keys()))

    try:
        records = parse_event(event)
    except ValueError as exc:
        _log.error("event.parse.failed", reason=str(exc))
        return ResponseBuilder.error("EVENT_PARSE_ERROR", str(exc), status=400)

    if not records:
        _log.warning("handler.no_records")
        return ResponseBuilder.success(data=[], message="No records to process.")

    validator = GlbValidator(s3_client=get_s3_client())
    service   = GlbValidationService(
        validator=validator,
        s3_client=get_s3_client(),
        sns_client=get_sns_client(),
        bucket=_BUCKET_NAME,
        sns_topic_arn=_SNS_ADMIN,
    )

    results = [service.process(record) for record in records]

    _log.info(
        "handler.completed",
        total=len(results),
        approved=sum(1 for r in results if r.status == "approved"),
        rejected=sum(1 for r in results if r.status == "rejected"),
        skipped=sum(1 for r in results if r.status == "skipped"),
    )

    return ResponseBuilder.partial([r.to_dict() for r in results])


# ── Service class ─────────────────────────────────────────────────────────────

class GlbValidationService:
    """
    Orchestrates the validate → move → notify workflow for one S3 record.

    Keeping this as a class (rather than free functions) makes it easy to
    inject test doubles for the S3/SNS clients.
    """

    def __init__(
        self,
        validator:     GlbValidator,
        s3_client,
        sns_client,
        bucket:        str,
        sns_topic_arn: str,
    ):
        self._validator     = validator
        self._s3            = s3_client
        self._sns           = sns_client
        self._bucket        = bucket
        self._sns_topic_arn = sns_topic_arn

    # ── Public ────────────────────────────────────────────────────────────────

    def process(self, record: S3Record) -> ValidationResult:
        """
        Process a single S3 record: validate → move → (optionally) notify.

        Returns a `ValidationResult` regardless of outcome; never raises.
        """
        key = record.key

        # ── Step 1: Tenant key validation ─────────────────────────────────────
        try:
            tenant_ctx = extract_tenant_context(key)
        except TenantKeyError as exc:
            _log.warning(
                "tenant.key.invalid",
                key=key,
                reason=exc.message,
                error_code=exc.error_code,
            )
            return ValidationResult(
                status="skipped",
                original_key=key,
                reason=exc.message,
                error_code=exc.error_code,
            )

        _log.info(
            "record.processing",
            bucket=record.bucket,
            key=key,
            size=record.size,
            tenant_id=tenant_ctx.tenant_id,
            restaurant_id=tenant_ctx.restaurant_id,
        )

        # ── Step 2: GLB validation ─────────────────────────────────────────────
        try:
            self._validator.validate(bucket=self._bucket, key=key, size=record.size)

        except ValidationError as exc:
            return self._handle_rejection(key, tenant_ctx, exc)

        except StorageError as exc:
            # Storage errors are not the asset's fault — log as error, skip.
            _log.error(
                "storage.error",
                key=key,
                tenant_id=tenant_ctx.tenant_id,
                error_code=exc.error_code,
                exc_message=exc.message,
            )
            return ValidationResult(
                status="skipped",
                original_key=key,
                reason=exc.message,
                error_code=exc.error_code,
                tenant_id=tenant_ctx.tenant_id,
                restaurant_id=tenant_ctx.restaurant_id,
                filename=tenant_ctx.filename,
            )

        # ── Step 3: Approve ───────────────────────────────────────────────────
        return self._handle_approval(key, tenant_ctx)

    # ── Private ───────────────────────────────────────────────────────────────

    def _handle_approval(self, key: str, ctx: TenantContext) -> ValidationResult:
        dest_key = self._move(key, ctx.approved_key)
        _log.info(
            "file.approved",
            key=key,
            dest=dest_key,
            tenant_id=ctx.tenant_id,
        )
        return ValidationResult(
            status="approved",
            original_key=key,
            destination_key=dest_key,
            tenant_id=ctx.tenant_id,
            restaurant_id=ctx.restaurant_id,
            filename=ctx.filename,
        )

    def _handle_rejection(
        self, key: str, ctx: TenantContext, exc: ValidationError
    ) -> ValidationResult:
        dest_key = self._move(key, ctx.rejected_key)
        _log.warning(
            "file.rejected",
            key=key,
            dest=dest_key,
            tenant_id=ctx.tenant_id,
            error_code=exc.error_code,
            reason=exc.message,
        )
        self._notify_admin(key, dest_key, exc.message, ctx.tenant_id)
        return ValidationResult(
            status="rejected",
            original_key=key,
            destination_key=dest_key,
            reason=exc.message,
            error_code=exc.error_code,
            tenant_id=ctx.tenant_id,
            restaurant_id=ctx.restaurant_id,
            filename=ctx.filename,
        )

    def _move(self, source_key: str, dest_key: str) -> str:
        """Copy to destination then delete source. Returns dest_key."""
        self._s3.copy_object(
            Bucket=self._bucket,
            CopySource={"Bucket": self._bucket, "Key": source_key},
            Key=dest_key,
        )
        self._s3.delete_object(Bucket=self._bucket, Key=source_key)
        return dest_key

    def _notify_admin(
        self, original_key: str, rejected_key: str, reason: str, tenant_id: str
    ) -> None:
        """Publish rejection notice to SNS. Logs error but never raises."""
        subject = f"[GLB Validator] Rejected: {original_key.split('/')[-1]}"
        message = (
            f"A .glb asset failed validation and has been moved to rejected/.\n\n"
            f"Bucket       : {self._bucket}\n"
            f"Tenant ID    : {tenant_id}\n"
            f"Original key : {original_key}\n"
            f"Rejected key : {rejected_key}\n"
            f"Reason       : {reason}\n"
        )
        try:
            self._sns.publish(
                TopicArn=self._sns_topic_arn,
                Subject=subject,
                Message=message,
            )
            _log.info("sns.notification.sent", key=original_key, tenant_id=tenant_id)
        except Exception as exc:  # noqa: BLE001
            _log.error(
                "sns.notification.failed",
                key=original_key,
                tenant_id=tenant_id,
                exc_message=str(exc),
            )
