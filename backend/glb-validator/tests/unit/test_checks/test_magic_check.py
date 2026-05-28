"""tests/unit/test_checks/test_magic_check.py"""

import struct
import pytest
from glb_validator.checks.magic_check import check_magic_and_version, GLB_MAGIC, GLB_HEADER_SIZE
from shared.exceptions import MagicBytesError, GlbVersionError, MalformedHeaderError


def _make_header(magic=GLB_MAGIC, version=2, length=100) -> bytes:
    return struct.pack("<III", magic, version, length)


class TestCheckMagicAndVersion:
    def test_valid_header_does_not_raise(self):
        check_magic_and_version(_make_header())

    def test_raises_on_wrong_magic(self):
        header = _make_header(magic=0xDEADBEEF)
        with pytest.raises(MagicBytesError) as exc_info:
            check_magic_and_version(header)
        assert "DEADBEEF" in exc_info.value.message.upper()

    def test_raises_on_version_1(self):
        header = _make_header(version=1)
        with pytest.raises(GlbVersionError) as exc_info:
            check_magic_and_version(header)
        assert "1" in exc_info.value.message
        assert "2" in exc_info.value.message

    def test_raises_on_version_3(self):
        header = _make_header(version=3)
        with pytest.raises(GlbVersionError):
            check_magic_and_version(header)

    def test_raises_on_empty_bytes(self):
        with pytest.raises(MalformedHeaderError):
            check_magic_and_version(b"")

    def test_raises_on_truncated_header(self):
        with pytest.raises(MalformedHeaderError):
            check_magic_and_version(b"\x67\x6C\x54\x46")  # Only 4 bytes

    def test_accepts_extra_bytes(self):
        """Extra bytes beyond 12 should be ignored."""
        header = _make_header() + b"\x00" * 100
        check_magic_and_version(header)  # Should not raise
