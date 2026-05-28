"""
glb_validator/checks/size_check.py
====================================
Validates that the GLB file does not exceed the configured size limit.
"""

from __future__ import annotations

from shared.exceptions import FileSizeError

MAX_FILE_SIZE_MB    = 50
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1_024 * 1_024


def check_file_size(size_bytes: int) -> None:
    """
    Raise `FileSizeError` if *size_bytes* exceeds the maximum allowed size.

    Parameters
    ----------
    size_bytes : int
        File size in bytes (from the S3 event or HeadObject response).

    Raises
    ------
    FileSizeError
    """
    if size_bytes > MAX_FILE_SIZE_BYTES:
        raise FileSizeError(max_mb=MAX_FILE_SIZE_MB, actual_bytes=size_bytes)
