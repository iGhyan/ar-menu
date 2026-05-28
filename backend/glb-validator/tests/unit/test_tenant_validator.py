"""tests/unit/test_tenant_validator.py"""

import pytest
from shared.tenant_validator import extract_tenant_context, validate_tenant_key, TenantContext
from shared.exceptions import TenantKeyError

_VALID_TENANT = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_VALID_RESTAURANT = "b2c3d4e5-f6a7-8901-bcde-f12345678901"
_VALID_KEY = f"uploads/TENANT#{_VALID_TENANT}/restaurants/{_VALID_RESTAURANT}/ar-models/model.glb"


class TestExtractTenantContext:
    def test_valid_key_returns_context(self):
        ctx = extract_tenant_context(_VALID_KEY)
        assert isinstance(ctx, TenantContext)
        assert ctx.tenant_id     == _VALID_TENANT
        assert ctx.restaurant_id == _VALID_RESTAURANT
        assert ctx.filename      == "model.glb"
        assert ctx.original_key  == _VALID_KEY

    def test_approved_key_property(self):
        ctx = extract_tenant_context(_VALID_KEY)
        assert ctx.approved_key.startswith("approved/")
        assert "uploads/" not in ctx.approved_key

    def test_rejected_key_property(self):
        ctx = extract_tenant_context(_VALID_KEY)
        assert ctx.rejected_key.startswith("rejected/")

    def test_destination_key_approved(self):
        ctx = extract_tenant_context(_VALID_KEY)
        assert ctx.destination_key("approved") == ctx.approved_key

    def test_destination_key_rejected(self):
        ctx = extract_tenant_context(_VALID_KEY)
        assert ctx.destination_key("rejected") == ctx.rejected_key

    def test_destination_key_invalid_status_raises(self):
        ctx = extract_tenant_context(_VALID_KEY)
        with pytest.raises(ValueError, match="Unknown status"):
            ctx.destination_key("pending")

    def test_wrong_prefix_raises(self):
        with pytest.raises(TenantKeyError, match="uploads/"):
            extract_tenant_context("approved/model.glb")

    def test_missing_tenant_prefix_raises(self):
        bad = f"uploads/{_VALID_TENANT}/restaurants/{_VALID_RESTAURANT}/ar-models/x.glb"
        with pytest.raises(TenantKeyError):
            extract_tenant_context(bad)

    def test_invalid_tenant_uuid_raises(self):
        bad = f"uploads/TENANT#NOT-A-UUID/restaurants/{_VALID_RESTAURANT}/ar-models/x.glb"
        with pytest.raises(TenantKeyError):
            extract_tenant_context(bad)

    def test_invalid_restaurant_uuid_raises(self):
        bad = f"uploads/TENANT#{_VALID_TENANT}/restaurants/NOT-A-UUID/ar-models/x.glb"
        with pytest.raises(TenantKeyError):
            extract_tenant_context(bad)

    def test_non_glb_extension_raises(self):
        bad = f"uploads/TENANT#{_VALID_TENANT}/restaurants/{_VALID_RESTAURANT}/ar-models/model.obj"
        with pytest.raises(TenantKeyError):
            extract_tenant_context(bad)

    def test_case_insensitive_matching(self):
        upper_key = _VALID_KEY.replace("TENANT#", "TENANT#").upper()
        # The pattern is IGNORECASE — rebuild a proper mixed-case key
        mixed = _VALID_KEY.replace("uploads/", "UPLOADS/")
        ctx = extract_tenant_context(mixed)
        assert ctx.tenant_id == _VALID_TENANT.lower()


class TestValidateTenantKey:
    def test_returns_true_for_valid_key(self):
        ok, reason = validate_tenant_key(_VALID_KEY)
        assert ok is True
        assert reason == ""

    def test_returns_false_for_invalid_key(self):
        ok, reason = validate_tenant_key("bad/key")
        assert ok is False
        assert reason != ""

    def test_reason_is_descriptive(self):
        _, reason = validate_tenant_key("not-uploads/model.glb")
        assert len(reason) > 10   # not an empty or trivial string
