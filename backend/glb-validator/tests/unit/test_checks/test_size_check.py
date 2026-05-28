"""tests/unit/test_checks/test_size_check.py"""

import pytest
from glb_validator.checks.size_check import check_file_size, MAX_FILE_SIZE_BYTES, MAX_FILE_SIZE_MB
from shared.exceptions import FileSizeError


class TestCheckFileSize:
    def test_passes_at_limit(self):
        check_file_size(MAX_FILE_SIZE_BYTES)  # Should not raise

    def test_passes_below_limit(self):
        check_file_size(1024)

    def test_passes_zero(self):
        check_file_size(0)

    def test_raises_one_byte_over_limit(self):
        with pytest.raises(FileSizeError) as exc_info:
            check_file_size(MAX_FILE_SIZE_BYTES + 1)
        assert exc_info.value.max_mb == MAX_FILE_SIZE_MB

    def test_raises_large_file(self):
        with pytest.raises(FileSizeError):
            check_file_size(500 * 1024 * 1024)  # 500 MB
