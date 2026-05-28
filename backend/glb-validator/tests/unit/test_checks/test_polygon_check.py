"""tests/unit/test_checks/test_polygon_check.py"""

import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "tests"))

from conftest import make_glb, make_gltf_with_polygons
from glb_validator.checks.polygon_check import check_polygon_count, MAX_POLYGON_COUNT
from shared.exceptions import PolygonCountError


class TestCheckPolygonCount:
    def test_passes_valid_glb_no_meshes(self):
        """GLB with empty glTF JSON — zero polygons."""
        data = make_glb(gltf_dict={})
        check_polygon_count(data)  # Should not raise

    def test_passes_at_limit(self):
        data = make_glb(gltf_dict=make_gltf_with_polygons(MAX_POLYGON_COUNT))
        check_polygon_count(data)  # Should not raise

    def test_passes_below_limit(self):
        data = make_glb(gltf_dict=make_gltf_with_polygons(1_000))
        check_polygon_count(data)

    def test_raises_one_over_limit(self):
        data = make_glb(gltf_dict=make_gltf_with_polygons(MAX_POLYGON_COUNT + 1))
        with pytest.raises(PolygonCountError) as exc_info:
            check_polygon_count(data)
        assert exc_info.value.context["polygon_limit"] == MAX_POLYGON_COUNT

    def test_skips_corrupt_json(self):
        """Corrupt JSON chunk — polygon check is skipped, file still passes."""
        data = make_glb(corrupt_json=True)
        check_polygon_count(data)  # Should not raise

    def test_skips_non_triangle_primitives(self):
        """Primitives with mode != 4 should not contribute to polygon count."""
        gltf = {
            "accessors": [{"count": 999_999 * 3, "componentType": 5123, "type": "SCALAR"}],
            "meshes":    [{"primitives": [{"indices": 0, "mode": 1}]}],  # LINES
        }
        data = make_glb(gltf_dict=gltf)
        check_polygon_count(data)  # Should not raise — non-triangle mode ignored

    def test_skips_primitives_without_indices(self):
        """Non-indexed geometry should not be counted."""
        gltf = {
            "accessors": [{"count": 999_999 * 3}],
            "meshes":    [{"primitives": [{"mode": 4}]}],  # no "indices" key
        }
        data = make_glb(gltf_dict=gltf)
        check_polygon_count(data)  # Should not raise

    def test_multiple_meshes_accumulate(self):
        """Multiple meshes each contributing polygons should be summed."""
        half = MAX_POLYGON_COUNT // 2 + 1
        gltf = {
            "accessors": [
                {"count": half * 3, "componentType": 5123, "type": "SCALAR"},
                {"count": half * 3, "componentType": 5123, "type": "SCALAR"},
            ],
            "meshes": [
                {"primitives": [{"indices": 0, "mode": 4}]},
                {"primitives": [{"indices": 1, "mode": 4}]},
            ],
        }
        data = make_glb(gltf_dict=gltf)
        with pytest.raises(PolygonCountError):
            check_polygon_count(data)

    def test_no_json_chunk_skips_check(self):
        """Binary-only GLB with no JSON chunk — polygon check silently passes."""
        import struct
        # Only GLB header, no chunks
        data = struct.pack("<III", 0x46546C67, 2, 12)
        check_polygon_count(data)  # Should not raise
