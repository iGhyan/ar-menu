"""
glb-validator-lambda
====================
Trigger  : S3 ObjectCreated (via EventBridge Rule or Native S3)
Actions  : validate .glb → move to approved/ or rejected/
           notify admin via SNS on rejection

Auth     : No JWT needed — S3 internal trigger (IAM secured)
Security : Tenant isolation via S3 key prefix validation
Env vars : BUCKET_AR  – bucket name
           SNS_ADMIN  – SNS topic ARN for admin alerts
"""

import os
import re
import json
import struct
import logging
import urllib.parse
from typing import Tuple

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3  = boto3.client("s3")
sns = boto3.client("sns")

BUCKET_AR = os.environ["BUCKET_AR"]
SNS_ADMIN = os.environ["SNS_ADMIN"]

# ── GLB limits ────────────────────────────────────────────────────────────────
GLB_MAGIC           = 0x46546C67
MAX_FILE_SIZE_MB    = 50
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1_024 * 1_024
MAX_POLYGON_COUNT   = 500_000

GLB_HEADER_SIZE   = 12
CHUNK_HEADER_SIZE = 8
CHUNK_TYPE_JSON   = 0x4E4F534A

# ── Tenant key pattern ────────────────────────────────────────────────────────
# Expected: uploads/TENANT#<uuid>/restaurants/<rid>/ar-models/<iid>.glb
TENANT_KEY_PATTERN = re.compile(
    r"^uploads/TENANT#([a-f0-9\-]{36})/restaurants/([a-f0-9\-]{36})/ar-models/(.+\.glb)$",
    re.IGNORECASE
)

UUID_PATTERN = re.compile(
    r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$",
    re.IGNORECASE
)


def _extract_tenant_from_key(key: str) -> Tuple[str | None, str | None, str | None]:
    """
    Extract tenantId, restaurantId, filename from S3 key.
    Returns (tenantId, restaurantId, filename) or (None, None, None)
    """
    match = TENANT_KEY_PATTERN.match(key)
    if match:
        return match.group(1), match.group(2), match.group(3)
    return None, None, None


def _validate_tenant_key(key: str) -> Tuple[bool, str]:
    """
    Validate S3 key structure — tenant isolation check.
    Key must follow: uploads/TENANT#<uuid>/restaurants/<uuid>/ar-models/<file>.glb
    """
    if not key.startswith("uploads/"):
        return False, "Key must start with uploads/"

    tenant_id, restaurant_id, filename = _extract_tenant_from_key(key)

    if not tenant_id:
        return False, f"Invalid key structure — expected uploads/TENANT#<uuid>/restaurants/<uuid>/ar-models/<file>.glb, got: {key}"

    if not UUID_PATTERN.match(tenant_id):
        return False, f"Invalid tenantId format in key: {tenant_id}"

    if not UUID_PATTERN.match(restaurant_id):
        return False, f"Invalid restaurantId format in key: {restaurant_id}"

    if not filename.lower().endswith(".glb"):
        return False, f"File must have .glb extension, got: {filename}"

    return True, ""


# ── Entry point ───────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    """Process S3 ObjectCreated records from EventBridge or Native S3."""
    results = []
    normalized_records = []

    # Case 1: EventBridge
    if "detail-type" in event and "detail" in event:
        detail = event["detail"]
        normalized_records.append({
            "bucket": detail["bucket"]["name"],
            "key":    urllib.parse.unquote_plus(detail["object"]["key"]),
            "size":   detail["object"].get("size", 0),
        })

    # Case 2: Native S3
    elif "Records" in event:
        for record in event["Records"]:
            normalized_records.append({
                "bucket": record["s3"]["bucket"]["name"],
                "key":    urllib.parse.unquote_plus(record["s3"]["object"]["key"]),
                "size":   record["s3"]["object"].get("size", 0),
            })

    for record in normalized_records:
        bucket = record["bucket"]
        key    = record["key"]
        size   = record["size"]

        logger.info("Processing s3://%s/%s (%d bytes)", bucket, key, size)

        # ── Step 1: Tenant key structure validation ───────────────────────────
        key_valid, key_reason = _validate_tenant_key(key)
        if not key_valid:
            logger.warning("INVALID KEY STRUCTURE — skipping: %s reason: %s", key, key_reason)
            results.append({"key": key, "result": "skipped", "reason": key_reason})
            continue

        tenant_id, restaurant_id, filename = _extract_tenant_from_key(key)
        logger.info("Tenant=%s Restaurant=%s File=%s", tenant_id, restaurant_id, filename)

        # ── Step 2: GLB content validation ───────────────────────────────────
        valid, reason = validate_glb(bucket, key, size)

        if valid:
            dest_key = _move_key(bucket, key, "approved")
            logger.info("APPROVED tenant=%s → %s", tenant_id, dest_key)
            results.append({
                "key":      key,
                "result":   "approved",
                "dest":     dest_key,
                "tenantId": tenant_id,
            })
        else:
            dest_key = _move_key(bucket, key, "rejected")
            logger.warning("REJECTED tenant=%s → %s reason: %s", tenant_id, dest_key, reason)
            _notify_admin(bucket, key, dest_key, reason, tenant_id)
            results.append({
                "key":      key,
                "result":   "rejected",
                "reason":   reason,
                "dest":     dest_key,
                "tenantId": tenant_id,
            })

    return {"statusCode": 200, "body": json.dumps(results)}


# ── GLB validation ────────────────────────────────────────────────────────────

def validate_glb(bucket: str, key: str, size: int) -> Tuple[bool, str]:
    """Validate file size and GLB magic header."""
    if size > MAX_FILE_SIZE_BYTES:
        return False, f"File size {size} bytes exceeds maximum {MAX_FILE_SIZE_MB}MB."

    try:
        response     = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{GLB_HEADER_SIZE-1}")
        header_bytes = response["Body"].read()

        if len(header_bytes) < GLB_HEADER_SIZE:
            return False, "Malformed file: incomplete 12-byte GLB header."

        magic, version, length = struct.unpack("<III", header_bytes)

        if magic != GLB_MAGIC:
            return False, "Invalid format: file header magic mismatch — not a GLB binary."

    except ClientError as exc:
        logger.error("S3 read error: %s", exc)
        return False, f"Storage read error: {str(exc)}"

    return True, ""


# ── S3 move helper ────────────────────────────────────────────────────────────

def _move_key(bucket: str, original_key: str, status_prefix: str) -> str:
    """Copy to approved/rejected prefix and delete original."""
    destination_key = original_key.replace("uploads/", f"{status_prefix}/", 1)
    copy_source = {"Bucket": bucket, "Key": original_key}
    s3.copy_object(Bucket=bucket, CopySource=copy_source, Key=destination_key)
    s3.delete_object(Bucket=bucket, Key=original_key)
    return destination_key


# ── SNS notification ──────────────────────────────────────────────────────────

def _notify_admin(bucket: str, original_key: str, rejected_key: str,
                  reason: str, tenant_id: str = "unknown"):
    """Notify admin SNS topic on rejection."""
    subject = f"[GLB Validator] Rejected: {original_key.split('/')[-1]}"
    message = (
        f"A .glb asset failed validation and has been moved to rejected/.\n\n"
        f"Bucket       : {bucket}\n"
        f"Tenant ID    : {tenant_id}\n"
        f"Original key : {original_key}\n"
        f"Rejected key : {rejected_key}\n"
        f"Reason       : {reason}\n"
    )
    try:
        sns.publish(TopicArn=SNS_ADMIN, Subject=subject, Message=message)
        logger.info("Admin notified via SNS — rejected file: %s", original_key)
    except ClientError as exc:
        logger.error("SNS publish failed: %s", exc)