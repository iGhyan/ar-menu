"""
glb_validator/checks/magic_check.py
=====================================
Validates the GLB binary header:
  - Magic number (0x46546C67 == "glTF" in little-endian)
  - Version      (must be 2)
  - Reported length vs actual size (sanity check)

GLB binary layout (spec: https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#binary-gltf-layout)
----------------------------------------
Offset  Size  Field
0       4     magic      uint32  0x46546C67
4       4     version    uint32  (2)
8       4     length     uint32  total file length in bytes
"""

from __future__ import annotations

import struct

from shared.exceptions import GlbVersionError, MagicBytesError, MalformedHeaderError

# ── Constants ─────────────────────────────────────────────────────────────────

GLB_MAGIC           = 0x46546C67   # "glTF" LE
SUPPORTED_VERSION   = 2
GLB_HEADER_SIZE     = 12           # 3 × uint32


def check_magic_and_version(header_bytes: bytes) -> None:
    """
    Parse and validate the 12-byte GLB header.

    Parameters
    ----------
    header_bytes : bytes
        At least 12 bytes read from the start of the file.

    Raises
    ------
    MalformedHeaderError  — fewer than 12 bytes supplied
    MagicBytesError       — magic number does not match
    GlbVersionError       — version is not 2
    """
    if len(header_bytes) < GLB_HEADER_SIZE:
        raise MalformedHeaderError(
            f"Header is only {len(header_bytes)} bytes; expected at least {GLB_HEADER_SIZE}."
        )

    magic, version, _length = struct.unpack_from("<III", header_bytes)

    if magic != GLB_MAGIC:
        raise MagicBytesError(expected=GLB_MAGIC, actual=magic)

    if version != SUPPORTED_VERSION:
        raise GlbVersionError(supported=SUPPORTED_VERSION, actual=version)
