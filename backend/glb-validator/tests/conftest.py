"""
tests/conftest.py
==================
Shared pytest fixtures and environment bootstrap.

Runs before ALL tests — sets up AWS env vars so boto3 doesn't complain
and clears the client cache between test cases.
"""

import os
import sys
import struct
import json

import pytest

# ── Bootstrap env vars before any module import ───────────────────────────────
os.environ.setdefault("ASSET_BUCKET_NAME",    "test-bucket")
os.environ.setdefault("SNS_ADMIN_ARN",        "arn:aws:sns:us-east-1:123456789012:admin-topic")
os.environ.setdefault("AWS_DEFAULT_REGION",   "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID",    "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY","testing")

# Add project root to sys.path so imports work without install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "layer", "python"))


@pytest.fixture(autouse=True)
def clear_client_cache():
    """Evict cached boto3 clients between tests."""
    from shared.aws_clients import clear_cache
    clear_cache()
    yield
    clear_cache()


# ── GLB binary builder helpers (shared across test modules) ───────────────────

GLB_MAGIC         = 0x46546C67
CHUNK_TYPE_JSON   = 0x4E4F534A
GLB_HEADER_SIZE   = 12
CHUNK_HEADER_SIZE = 8


def make_glb(version=2, magic=GLB_MAGIC, gltf_dict=None, corrupt_json=False) -> bytes:
    """Build a minimal valid (or intentionally broken) GLB binary."""
    if gltf_dict is None:
        gltf_dict = {}

    if corrupt_json:
        json_bytes = b"{ NOT VALID JSON !!!"
    else:
        json_bytes = json.dumps(gltf_dict).encode("utf-8")

    # Pad to 4-byte alignment
    pad = (4 - len(json_bytes) % 4) % 4
    json_bytes += b" " * pad

    chunk_len    = len(json_bytes)
    total_length = GLB_HEADER_SIZE + CHUNK_HEADER_SIZE + chunk_len

    data  = struct.pack("<III", magic, version, total_length)
    data += struct.pack("<II",  chunk_len, CHUNK_TYPE_JSON)
    data += json_bytes
    return data


def make_gltf_with_polygons(n_triangles: int) -> dict:
    """Return a minimal glTF JSON dict whose polygon count equals n_triangles."""
    return {
        "accessors": [{"count": n_triangles * 3, "componentType": 5123, "type": "SCALAR"}],
        "meshes":    [{"primitives": [{"indices": 0, "mode": 4}]}],
    }


@pytest.fixture
def glb_bytes():
    """Return a minimal valid GLB binary."""
    return make_glb()


@pytest.fixture
def mock_context():
    """Minimal Lambda context mock."""
    class Ctx:
        aws_request_id    = "test-request-id-123"
        function_name     = "glb-validator-test"
        function_version  = "$LATEST"
    return Ctx()
