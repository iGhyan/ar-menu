"""tests/unit/test_cognito_auth.py"""

import pytest
from unittest.mock import MagicMock

from tests.conftest import make_jwt, auth_event
from shared.cognito_auth import CognitoAuth, UserContext
from shared.exceptions import (
    TokenMissingError,
    TokenExpiredError,
    TokenInvalidError,
    RbacError,
)


@pytest.fixture
def auth():
    return CognitoAuth()


class TestGetUserFromEvent:
    def test_valid_tenant_token(self, auth):
        token = make_jwt(groups=["menulay_tenant"], tenant_id="t-123")
        event = auth_event("PUT", token=token)
        user  = auth.get_user_from_event(event)

        assert isinstance(user, UserContext)
        assert user.tenant_id == "t-123"
        assert user.is_tenant() is True
        assert user.is_admin() is False

    def test_valid_admin_token(self, auth):
        token = make_jwt(groups=["menulay_admin"])
        user  = auth.get_user_from_event(auth_event("DELETE", token=token))
        assert user.is_admin() is True
        assert user.is_admin_or_tenant() is True

    def test_missing_auth_header_raises(self, auth):
        with pytest.raises(TokenMissingError):
            auth.get_user_from_event({"httpMethod": "PUT", "headers": {}})

    def test_expired_token_raises(self, auth):
        token = make_jwt(expired=True)
        with pytest.raises(TokenExpiredError):
            auth.get_user_from_event(auth_event("PUT", token=token))

    def test_wrong_issuer_raises(self, auth):
        token = make_jwt(issuer="https://evil.com/fake")
        with pytest.raises(TokenInvalidError):
            auth.get_user_from_event(auth_event("PUT", token=token))

    def test_wrong_client_id_raises(self, auth):
        token = make_jwt(client_id="wrong-client")
        with pytest.raises(TokenInvalidError):
            auth.get_user_from_event(auth_event("PUT", token=token))

    def test_bearer_prefix_stripped(self, auth):
        token = make_jwt(groups=["menulay_admin"])
        event = {"httpMethod": "PUT", "headers": {"Authorization": f"Bearer {token}"}}
        user  = auth.get_user_from_event(event)
        assert user.is_admin()

    def test_lowercase_authorization_header(self, auth):
        token = make_jwt(groups=["menulay_tenant"])
        event = {"httpMethod": "PUT", "headers": {"authorization": f"Bearer {token}"}}
        user  = auth.get_user_from_event(event)
        assert user.is_tenant()

    def test_malformed_jwt_raises(self, auth):
        event = {"httpMethod": "PUT", "headers": {"Authorization": "Bearer not.a.valid"}}
        with pytest.raises((TokenInvalidError, TokenExpiredError)):
            auth.get_user_from_event(event)


class TestRequireRoles:
    def test_passes_when_user_has_role(self, auth):
        token = make_jwt(groups=["menulay_admin"])
        user  = auth.get_user_from_event(auth_event("PUT", token=token))
        auth.require_roles(user, ["menulay_admin"])  # Should not raise

    def test_raises_when_role_missing(self, auth):
        token = make_jwt(groups=["menulay_kitchen_staff"])
        user  = auth.get_user_from_event(auth_event("PUT", token=token))
        with pytest.raises(RbacError) as exc_info:
            auth.require_roles(user, ["menulay_admin", "menulay_tenant"])
        assert "menulay_kitchen_staff" in str(exc_info.value.context.get("user_groups", []))

    def test_passes_with_any_matching_role(self, auth):
        token = make_jwt(groups=["menulay_tenant"])
        user  = auth.get_user_from_event(auth_event("PUT", token=token))
        auth.require_roles(user, ["menulay_admin", "menulay_tenant"])  # Should not raise


class TestUserContextHelpers:
    def test_kitchen_or_admin_true_for_admin(self, auth):
        token = make_jwt(groups=["menulay_admin"])
        user  = auth.get_user_from_event(auth_event("PUT", token=token))
        assert user.is_kitchen_or_admin() is True

    def test_kitchen_or_admin_true_for_kitchen(self, auth):
        token = make_jwt(groups=["menulay_kitchen_staff"])
        user  = auth.get_user_from_event(auth_event("PUT", token=token))
        assert user.is_kitchen_or_admin() is True

    def test_kitchen_or_admin_false_for_tenant(self, auth):
        token = make_jwt(groups=["menulay_tenant"])
        user  = auth.get_user_from_event(auth_event("PUT", token=token))
        assert user.is_kitchen_or_admin() is False

    def test_has_any_role(self, auth):
        token = make_jwt(groups=["menulay_tenant"])
        user  = auth.get_user_from_event(auth_event("PUT", token=token))
        assert user.has_any_role(["menulay_tenant", "menulay_admin"]) is True
        assert user.has_any_role(["menulay_admin"]) is False
