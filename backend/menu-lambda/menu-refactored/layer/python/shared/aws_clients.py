"""
shared/aws_clients.py
======================
Centralized boto3 client / resource factory with per-region caching.

Why a factory instead of module-level globals?
- Unit tests can patch `shared.aws_clients.get_s3_client` in one place and
  every caller is automatically mocked — no per-file patching needed.
- Supports multi-region Lambda deployments without code changes.
- Warm Lambda containers reuse cached clients (no repeated handshakes).

Usage
-----
    from shared.aws_clients import get_s3_client, get_sns_client

    s3  = get_s3_client()
    sns = get_sns_client()
    obj = s3.get_object(Bucket="my-bucket", Key="some/key")
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config

# ── Default botocore config ───────────────────────────────────────────────────
# Adaptive retry mode handles throttling / transient errors automatically.
_DEFAULT_CONFIG = Config(
    retries={"max_attempts": 3, "mode": "adaptive"},
)


# ── S3 ────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=8)
def get_s3_client(region: str | None = None) -> Any:
    """Return a cached boto3 S3 client for *region* (default: AWS_DEFAULT_REGION)."""
    return boto3.client(
        "s3",
        region_name=region or _default_region(),
        config=_DEFAULT_CONFIG,
    )


# ── SNS ───────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=8)
def get_sns_client(region: str | None = None) -> Any:
    """Return a cached boto3 SNS client."""
    return boto3.client(
        "sns",
        region_name=region or _default_region(),
        config=_DEFAULT_CONFIG,
    )


# ── DynamoDB ──────────────────────────────────────────────────────────────────

@lru_cache(maxsize=8)
def get_dynamodb_resource(region: str | None = None) -> Any:
    """Return a cached boto3 DynamoDB resource (high-level Table API)."""
    return boto3.resource(
        "dynamodb",
        region_name=region or _default_region(),
        config=_DEFAULT_CONFIG,
    )


@lru_cache(maxsize=8)
def get_dynamodb_client(region: str | None = None) -> Any:
    """Return a cached boto3 DynamoDB low-level client (for batch ops, TTL, etc.)."""
    return boto3.client(
        "dynamodb",
        region_name=region or _default_region(),
        config=_DEFAULT_CONFIG,
    )


# ── Cognito ───────────────────────────────────────────────────────────────────

@lru_cache(maxsize=8)
def get_cognito_idp_client(region: str | None = None) -> Any:
    """Return a cached Cognito Identity Provider client."""
    return boto3.client(
        "cognito-idp",
        region_name=region or _default_region(),
        config=_DEFAULT_CONFIG,
    )


# ── SQS (future use) ──────────────────────────────────────────────────────────

@lru_cache(maxsize=8)
def get_sqs_client(region: str | None = None) -> Any:
    """Return a cached boto3 SQS client."""
    return boto3.client(
        "sqs",
        region_name=region or _default_region(),
        config=_DEFAULT_CONFIG,
    )


# ── SecretsManager (future use) ───────────────────────────────────────────────

@lru_cache(maxsize=4)
def get_secrets_client(region: str | None = None) -> Any:
    """Return a cached Secrets Manager client."""
    return boto3.client(
        "secretsmanager",
        region_name=region or _default_region(),
        config=_DEFAULT_CONFIG,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _default_region() -> str:
    return os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


def clear_cache() -> None:
    """
    Evict all cached clients.

    Call this in tests between test cases to ensure a clean state,
    or when credentials rotate in long-running processes.
    """
    get_s3_client.cache_clear()
    get_sns_client.cache_clear()
    get_dynamodb_resource.cache_clear()
    get_dynamodb_client.cache_clear()
    get_cognito_idp_client.cache_clear()
    get_sqs_client.cache_clear()
    get_secrets_client.cache_clear()
