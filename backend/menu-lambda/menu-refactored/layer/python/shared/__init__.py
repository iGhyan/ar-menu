# shared — Lambda Layer public API
from shared.aws_clients import (
    get_s3_client,
    get_sns_client,
    get_dynamodb_resource,
    get_dynamodb_client,
    get_cognito_idp_client,
    get_sqs_client,
    get_secrets_client,
    clear_cache,
)
from shared.exceptions import (
    AppBaseException,
    ValidationError,
    FileSizeError,
    FileFormatError,
    MagicBytesError,
    GlbVersionError,
    PolygonCountError,
    FileExtensionError,
    MalformedHeaderError,
    TenantKeyError,
    TenantMismatchError,
    StorageError,
    S3ReadError,
    S3WriteError,
    PresignError,
    NotificationError,
    AuthError,
    TokenMissingError,
    TokenExpiredError,
    TokenInvalidError,
    ForbiddenError,
    RbacError,
    ResourceNotFoundError,
    BadRequestError,
    MissingParameterError,
    InvalidJsonError,
    NoValidFieldsError,
    CloudFrontError,
)
from shared.structured_logger import get_logger, bind_correlation_id, bind_lambda_context
from shared.error_handler import handle_errors
from shared.request_parser import parse_event, S3Record
from shared.response_builder import ResponseBuilder, bind_request_id
from shared.tenant_validator import extract_tenant_context, validate_tenant_key, TenantContext
from shared.cognito_auth import CognitoAuth, UserContext, default_auth

__all__ = [
    # AWS clients
    "get_s3_client", "get_sns_client", "get_dynamodb_resource",
    "get_dynamodb_client", "get_cognito_idp_client", "get_sqs_client",
    "get_secrets_client", "clear_cache",
    # Exceptions — core
    "AppBaseException", "ValidationError", "FileSizeError", "FileFormatError",
    "MagicBytesError", "GlbVersionError", "PolygonCountError", "FileExtensionError",
    "MalformedHeaderError", "TenantKeyError", "TenantMismatchError",
    "StorageError", "S3ReadError", "S3WriteError", "PresignError", "NotificationError",
    # Exceptions — auth / rbac
    "AuthError", "TokenMissingError", "TokenExpiredError", "TokenInvalidError",
    "ForbiddenError", "RbacError",
    # Exceptions — request / resource
    "ResourceNotFoundError", "BadRequestError", "MissingParameterError",
    "InvalidJsonError", "NoValidFieldsError", "CloudFrontError",
    # Logger
    "get_logger", "bind_correlation_id", "bind_lambda_context",
    # Error handler
    "handle_errors",
    # Request / response
    "parse_event", "S3Record", "ResponseBuilder", "bind_request_id",
    # Tenant
    "extract_tenant_context", "validate_tenant_key", "TenantContext",
    # Cognito auth
    "CognitoAuth", "UserContext", "default_auth",
]
