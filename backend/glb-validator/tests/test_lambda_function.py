"""
Unit tests for glb-validator-lambda
Run locally:  python -m pytest tests/ -v
Coverage target: ≥ 75 %
"""

import json
import struct
import unittest
from unittest.mock import MagicMock, patch, call

# ── Bootstrap env vars before importing the lambda ───────────────────────────
import os
os.environ["BUCKET_AR"]          = "test-bucket"
os.environ["SNS_ADMIN"]          = "arn:aws:sns:us-east-1:123456789012:admin-topic"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_ACCESS_KEY_ID"]  = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"

import lambda_function as lf


# ─────────────────────────────────────────────────────────────────────────────
# Helpers to build fake GLB binary blobs
# ─────────────────────────────────────────────────────────────────────────────

def _make_glb(version=2, magic=lf.GLB_MAGIC, gltf_dict=None, corrupt_json=False):
    """Return minimal binary GLB bytes."""
    if gltf_dict is None:
        gltf_dict = {}

    if corrupt_json:
        json_bytes = b"{ this is not valid json !!!"
    else:
        json_bytes = json.dumps(gltf_dict).encode("utf-8")

    # Pad JSON to 4-byte alignment
    pad = (4 - len(json_bytes) % 4) % 4
    json_bytes += b" " * pad

    chunk0_length = len(json_bytes)
    total_length  = lf.GLB_HEADER_SIZE + lf.CHUNK_HEADER_SIZE + chunk0_length

    data = struct.pack("<III", magic, version, total_length)          # header
    data += struct.pack("<II", chunk0_length, lf.CHUNK_TYPE_JSON)      # chunk header
    data += json_bytes                                                  # chunk data
    return data


def _make_gltf_with_polygons(n_triangles: int) -> dict:
    """Return a minimal glTF JSON dict whose polygon count equals n_triangles."""
    return {
        "accessors": [{"count": n_triangles * 3, "componentType": 5123, "type": "SCALAR"}],
        "meshes": [{"primitives": [{"indices": 0, "mode": 4}]}],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateGlb(unittest.TestCase):

    # ── Extension checks ──────────────────────────────────────────────────────

    def test_rejects_non_glb_extension(self):
        valid, reason = lf.validate_glb("bucket", "uploads/model.obj", 100)
        self.assertFalse(valid)
        self.assertIn("extension", reason)

    def test_rejects_no_extension(self):
        valid, reason = lf.validate_glb("bucket", "uploads/model", 100)
        self.assertFalse(valid)

    # ── Size checks ───────────────────────────────────────────────────────────

    def test_rejects_file_too_large(self):
        big = lf.MAX_FILE_SIZE_BYTES + 1
        valid, reason = lf.validate_glb("bucket", "uploads/model.glb", big)
        self.assertFalse(valid)
        self.assertIn("MB", reason)

    # ── S3 read failure ───────────────────────────────────────────────────────

    @patch.object(lf, "_get_bytes", return_value=None)
    def test_rejects_when_s3_unreadable(self, _mock):
        valid, reason = lf.validate_glb("bucket", "uploads/model.glb", 100)
        self.assertFalse(valid)
        self.assertIn("S3", reason)

    # ── Magic bytes ───────────────────────────────────────────────────────────

    @patch.object(lf, "_get_bytes")
    def test_rejects_wrong_magic(self, mock_get):
        glb = _make_glb(magic=0xDEADBEEF)
        mock_get.return_value = glb[:lf.GLB_HEADER_SIZE + lf.CHUNK_HEADER_SIZE]
        valid, reason = lf.validate_glb("bucket", "uploads/model.glb", len(glb))
        self.assertFalse(valid)
        self.assertIn("magic", reason)

    # ── Version ───────────────────────────────────────────────────────────────

    @patch.object(lf, "_get_bytes")
    def test_rejects_version_1(self, mock_get):
        glb = _make_glb(version=1)
        mock_get.return_value = glb[:lf.GLB_HEADER_SIZE + lf.CHUNK_HEADER_SIZE]
        valid, reason = lf.validate_glb("bucket", "uploads/model.glb", len(glb))
        self.assertFalse(valid)
        self.assertIn("version", reason)

    # ── Polygon count ─────────────────────────────────────────────────────────

    @patch.object(lf, "_get_bytes")
    def test_accepts_valid_glb_below_polygon_limit(self, mock_get):
        gltf = _make_gltf_with_polygons(1_000)
        glb  = _make_glb(gltf_dict=gltf)

        def side_effect(bucket, key, start, end):
            return glb[start: end + 1]

        mock_get.side_effect = side_effect
        valid, reason = lf.validate_glb("bucket", "uploads/model.glb", len(glb))
        self.assertTrue(valid, reason)

    @patch.object(lf, "_get_bytes")
    def test_rejects_too_many_polygons(self, mock_get):
        gltf = _make_gltf_with_polygons(lf.MAX_POLYGON_COUNT + 1)
        glb  = _make_glb(gltf_dict=gltf)

        def side_effect(bucket, key, start, end):
            return glb[start: end + 1]

        mock_get.side_effect = side_effect
        valid, reason = lf.validate_glb("bucket", "uploads/model.glb", len(glb))
        self.assertFalse(valid)
        self.assertIn("Polygon", reason)

    @patch.object(lf, "_get_bytes")
    def test_accepts_exactly_at_polygon_limit(self, mock_get):
        gltf = _make_gltf_with_polygons(lf.MAX_POLYGON_COUNT)
        glb  = _make_glb(gltf_dict=gltf)

        def side_effect(bucket, key, start, end):
            return glb[start: end + 1]

        mock_get.side_effect = side_effect
        valid, reason = lf.validate_glb("bucket", "uploads/model.glb", len(glb))
        self.assertTrue(valid, reason)

    @patch.object(lf, "_get_bytes")
    def test_corrupt_json_does_not_reject(self, mock_get):
        """Corrupt JSON chunk → polygon check skipped, file is still valid."""
        glb = _make_glb(corrupt_json=True)

        def side_effect(bucket, key, start, end):
            return glb[start: end + 1]

        mock_get.side_effect = side_effect
        valid, _reason = lf.validate_glb("bucket", "uploads/model.glb", len(glb))
        self.assertTrue(valid)

    @patch.object(lf, "_get_bytes")
    def test_non_triangle_primitives_skipped(self, mock_get):
        """mode != 4 primitives must not inflate polygon count."""
        gltf = {
            "accessors": [{"count": 999_999 * 3}],
            "meshes": [{"primitives": [{"indices": 0, "mode": 1}]}],   # LINES, not TRIANGLES
        }
        glb = _make_glb(gltf_dict=gltf)

        def side_effect(bucket, key, start, end):
            return glb[start: end + 1]

        mock_get.side_effect = side_effect
        valid, reason = lf.validate_glb("bucket", "uploads/model.glb", len(glb))
        self.assertTrue(valid, reason)


# ─────────────────────────────────────────────────────────────────────────────

class TestMoveKey(unittest.TestCase):

    @patch.object(lf, "s3")
    def test_copy_then_delete(self, mock_s3):
        dest = lf._move_key("my-bucket", "uploads/model.glb", "approved")
        self.assertEqual(dest, "approved/model.glb")
        mock_s3.copy_object.assert_called_once()
        mock_s3.delete_object.assert_called_once_with(
            Bucket="my-bucket", Key="uploads/model.glb"
        )

    @patch.object(lf, "s3")
    def test_rejected_prefix(self, mock_s3):
        dest = lf._move_key("my-bucket", "uploads/bad.glb", "rejected")
        self.assertEqual(dest, "rejected/bad.glb")


# ─────────────────────────────────────────────────────────────────────────────

class TestNotifyAdmin(unittest.TestCase):

    @patch.object(lf, "sns")
    def test_publishes_to_correct_topic(self, mock_sns):
        lf._notify_admin("bkt", "uploads/x.glb", "rejected/x.glb", "too big")
        mock_sns.publish.assert_called_once()
        kwargs = mock_sns.publish.call_args.kwargs
        self.assertEqual(kwargs["TopicArn"], os.environ["SNS_ADMIN"])
        self.assertIn("x.glb", kwargs["Subject"])
        self.assertIn("too big", kwargs["Message"])

    @patch.object(lf, "sns")
    def test_sns_error_does_not_raise(self, mock_sns):
        from botocore.exceptions import ClientError
        mock_sns.publish.side_effect = ClientError(
            {"Error": {"Code": "500", "Message": "oops"}}, "Publish"
        )
        # Should log and return, not raise
        lf._notify_admin("bkt", "uploads/x.glb", "rejected/x.glb", "bad")


# ─────────────────────────────────────────────────────────────────────────────

class TestLambdaHandler(unittest.TestCase):

    def _make_event(self, key, size=1000):
        return {
            "Records": [{
                "s3": {
                    "bucket": {"name": "test-bucket"},
                    "object": {"key": key, "size": size},
                }
            }]
        }

    @patch.object(lf, "_notify_admin")
    @patch.object(lf, "_move_key", return_value="approved/model.glb")
    @patch.object(lf, "validate_glb", return_value=(True, ""))
    def test_approved_flow(self, mock_val, mock_move, mock_notify):
        result = lf.lambda_handler(self._make_event("uploads/model.glb"), {})
        self.assertEqual(result["statusCode"], 200)
        body = json.loads(result["body"])
        self.assertEqual(body[0]["result"], "approved")
        mock_notify.assert_not_called()

    @patch.object(lf, "_notify_admin")
    @patch.object(lf, "_move_key", return_value="rejected/bad.glb")
    @patch.object(lf, "validate_glb", return_value=(False, "bad magic"))
    def test_rejected_flow(self, mock_val, mock_move, mock_notify):
        result = lf.lambda_handler(self._make_event("uploads/bad.glb"), {})
        self.assertEqual(result["statusCode"], 200)
        body = json.loads(result["body"])
        self.assertEqual(body[0]["result"], "rejected")
        mock_notify.assert_called_once()

    @patch.object(lf, "validate_glb")
    def test_skips_non_uploads_prefix(self, mock_val):
        event = self._make_event("approved/model.glb")
        lf.lambda_handler(event, {})
        mock_val.assert_not_called()

    @patch.object(lf, "validate_glb", return_value=(True, ""))
    @patch.object(lf, "_move_key", return_value="approved/a.glb")
    def test_multiple_records(self, mock_move, mock_val):
        event = {
            "Records": [
                {"s3": {"bucket": {"name": "b"}, "object": {"key": "uploads/a.glb", "size": 10}}},
                {"s3": {"bucket": {"name": "b"}, "object": {"key": "uploads/b.glb", "size": 10}}},
            ]
        }
        result = lf.lambda_handler(event, {})
        body = json.loads(result["body"])
        self.assertEqual(len(body), 2)


if __name__ == "__main__":
    unittest.main()