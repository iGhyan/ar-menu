"""
TenantService — DynamoDB CRUD for TenantTable.

TenantTable uses a simple hash-key-only schema (tenantId).
Responsibilities:
  - Tenant lifecycle (create / get / update / delete)
  - Plan quota enforcement (maxRestaurants check)
  - Tenant validation before any cross-service operation

Error Handling:
  DDB throttle → 3× retry + jitter via @retry decorator
  Tenant not found → TenantNotFoundError
  Quota breach   → QuotaExceededError
"""
from __future__ import annotations

import os
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from models.tenant import Tenant
from utils.dynamo_helpers import decimal_to_python, build_update_expression
from utils.ids import new_id, utc_now
from utils.logger import get_logger
from utils.retry import retry

log = get_logger(__name__)

_TABLE_NAME = os.environ.get("TENANT_TABLE", "TenantTable")


class TenantNotFoundError(Exception):
    pass


class TenantQuotaExceededError(Exception):
    """Raised when a tenant tries to exceed plan limits."""
    pass


class TenantService:
    def __init__(self, table=None) -> None:
        self._table = table or boto3.resource("dynamodb").Table(_TABLE_NAME)

    # ── Private DDB helpers ───────────────────────────────────────────────

    @retry(retries=3, base_delay=0.1, exceptions=(ClientError,))
    def _ddb_get(self, tenant_id: str) -> Optional[dict]:
        resp = self._table.get_item(Key={"tenantId": tenant_id})
        item = resp.get("Item")
        return decimal_to_python(item) if item else None

    @retry(retries=3, base_delay=0.1, exceptions=(ClientError,))
    def _ddb_put(self, item: dict) -> None:
        self._table.put_item(Item=item)

    @retry(retries=3, base_delay=0.1, exceptions=(ClientError,))
    def _ddb_update(self, tenant_id: str, updates: dict) -> dict:
        expr, names, values = build_update_expression(updates)
        resp = self._table.update_item(
            Key={"tenantId": tenant_id},
            UpdateExpression=expr,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
        return decimal_to_python(resp.get("Attributes", {}))

    @retry(retries=3, base_delay=0.1, exceptions=(ClientError,))
    def _ddb_delete(self, tenant_id: str) -> None:
        self._table.delete_item(Key={"tenantId": tenant_id})

    # ── Public API ────────────────────────────────────────────────────────

    def create(self, body: dict) -> Tenant:
        """
        Create a new tenant account.
        tenantId is auto-generated; caller should NOT pass one.
        """
        tenant_id = new_id()
        now = utc_now()

        tenant = Tenant(
            tenantId=tenant_id,
            name=body.get("name", ""),
            email=body.get("email", ""),
            plan=body.get("plan", "FREE"),
            isActive=bool(body.get("isActive", True)),
            maxRestaurants=int(body.get("maxRestaurants", 1)),
            createdAt=now,
            updatedAt=now,
            contactPhone=body.get("contactPhone"),
        )
        tenant.validate()

        self._ddb_put(tenant.to_dynamo_item())

        log.info("Tenant created", extra={
            "tenantId": tenant_id,
            "plan": tenant.plan,
        })
        return tenant

    def get(self, tenant_id: str) -> Tenant:
        """Fetch a single tenant by ID. Raises TenantNotFoundError on miss."""
        raw = self._ddb_get(tenant_id)
        if raw is None:
            raise TenantNotFoundError(
                f"Tenant {tenant_id} not found"
            )
        return Tenant.from_dynamo_item(raw)

    def update(self, tenant_id: str, body: dict) -> Tenant:
        """Partial update — only mutable fields accepted."""
        self.get(tenant_id)   # 404 guard

        mutable = {"name", "email", "plan", "isActive",
                   "maxRestaurants", "contactPhone"}
        updates = {k: v for k, v in body.items() if k in mutable}
        updates["updatedAt"] = utc_now()

        attrs = self._ddb_update(tenant_id, updates)

        log.info("Tenant updated", extra={"tenantId": tenant_id})
        return Tenant.from_dynamo_item(attrs)

    def delete(self, tenant_id: str) -> None:
        """Hard-delete a tenant record."""
        self.get(tenant_id)   # 404 guard
        self._ddb_delete(tenant_id)
        log.info("Tenant deleted", extra={"tenantId": tenant_id})

    def check_restaurant_quota(
        self, tenant_id: str, current_count: int
    ) -> None:
        """
        Raise TenantQuotaExceededError if adding one more restaurant
        would breach the tenant's plan limit.

        maxRestaurants == 0 means unlimited.
        """
        tenant = self.get(tenant_id)

        if tenant.maxRestaurants == 0:
            return   # unlimited plan

        if current_count >= tenant.maxRestaurants:
            raise TenantQuotaExceededError(
                f"Tenant {tenant_id} has reached the restaurant limit "
                f"({tenant.maxRestaurants}) for plan '{tenant.plan}'"
            )

    def validate_active(self, tenant_id: str) -> Tenant:
        """
        Fetch tenant and raise AuthorisationError if account is inactive.
        Returns the Tenant on success.
        """
        tenant = self.get(tenant_id)
        if not tenant.isActive:
            raise PermissionError(
                f"Tenant {tenant_id} account is disabled"
            )
        return tenant