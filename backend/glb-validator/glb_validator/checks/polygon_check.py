"""
glb_validator/checks/polygon_check.py
=======================================
Validates that the GLB does not exceed the maximum polygon (triangle) count
by inspecting the JSON chunk embedded in the binary.

GLB chunk layout (after the 12-byte header)
--------------------------------------------
Offset  Size  Field
12      4     chunkLength  uint32  — byte length of chunk data
16      4     chunkType    uint32  — 0x4E4F534A == JSON
20      N     chunkData    bytes   — UTF-8 glTF JSON

The polygon count is computed by summing the `accessor.count / 3` values
for every mesh primitive that uses `mode == 4` (TRIANGLES).
Primitives with other modes (LINES, POINTS, etc.) are ignored.
"""

from __future__ import annotations

import json
import struct

from shared.exceptions import PolygonCountError

# ── Constants ─────────────────────────────────────────────────────────────────

CHUNK_HEADER_SIZE  = 8              # chunkLength (4) + chunkType (4)
CHUNK_TYPE_JSON    = 0x4E4F534A    # "JSON" in little-endian
GLB_HEADER_SIZE    = 12
TRIANGLE_MODE      = 4             # glTF primitive mode for triangles

MAX_POLYGON_COUNT  = 500_000


def check_polygon_count(file_bytes: bytes) -> None:
    """
    Parse the JSON chunk and enforce the polygon limit.

    Parameters
    ----------
    file_bytes : bytes
        The full GLB file content (or at least enough to cover the JSON chunk).

    Raises
    ------
    PolygonCountError  — polygon count exceeds `MAX_POLYGON_COUNT`

    Notes
    -----
    - If the JSON chunk is absent or unparseable, this check is **skipped**
      (the file may still be valid; binary-only GLBs without meshes are allowed).
    - Non-triangle primitives do not contribute to the count.
    """
    json_bytes = _extract_json_chunk(file_bytes)
    if json_bytes is None:
        return  # No JSON chunk — skip polygon check

    try:
        gltf = json.loads(json_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return  # Corrupt JSON — skip check, don't reject

    polygon_count = _count_polygons(gltf)
    if polygon_count > MAX_POLYGON_COUNT:
        raise PolygonCountError(limit=MAX_POLYGON_COUNT, actual=polygon_count)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_json_chunk(file_bytes: bytes) -> bytes | None:
    """
    Locate and return the raw bytes of the first JSON chunk, or None.
    """
    offset = GLB_HEADER_SIZE  # skip the 12-byte GLB header

    while offset + CHUNK_HEADER_SIZE <= len(file_bytes):
        chunk_length, chunk_type = struct.unpack_from("<II", file_bytes, offset)
        offset += CHUNK_HEADER_SIZE

        chunk_data = file_bytes[offset: offset + chunk_length]
        offset += chunk_length

        if chunk_type == CHUNK_TYPE_JSON:
            return chunk_data

    return None


def _count_polygons(gltf: dict) -> int:
    """
    Count triangles across all TRIANGLE-mode mesh primitives.

    For each primitive with mode 4 (TRIANGLES) that has an `indices` accessor,
    polygon_count += accessor["count"] // 3.
    """
    accessors = gltf.get("accessors", [])
    meshes    = gltf.get("meshes", [])
    total     = 0

    for mesh in meshes:
        for primitive in mesh.get("primitives", []):
            if primitive.get("mode", 4) != TRIANGLE_MODE:
                continue  # skip non-triangle primitives

            accessor_index = primitive.get("indices")
            if accessor_index is None:
                continue  # non-indexed geometry; skip

            try:
                accessor = accessors[accessor_index]
                total += int(accessor.get("count", 0)) // 3
            except (IndexError, TypeError, ValueError):
                continue  # malformed reference; skip gracefully

    return total
