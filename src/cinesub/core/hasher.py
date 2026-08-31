from __future__ import annotations

import os
import struct
from pathlib import Path

from cinesub.core.constants import HASH_CHUNK_SIZE, MIN_HASH_FILE_SIZE

_INT64_STRUCT = struct.Struct("<q")
_BYTESIZE = _INT64_STRUCT.size
_NUM_CHUNKS = HASH_CHUNK_SIZE // _BYTESIZE
_MASK_64 = 0xFFFFFFFFFFFFFFFF


def calculate_movie_hash(file_path: str | Path) -> str:
    """Calculate the 64-bit OpenSubtitles hash for a video file.

    Args:
        file_path: Path to the target video file.

    Returns:
        16-character lowercase hexadecimal hash string.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is smaller than 128KB.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {path}")

    file_size = path.stat().st_size
    if file_size < MIN_HASH_FILE_SIZE:
        raise ValueError(
            f"File is too small ({file_size} bytes) for OpenSubtitles hash "
            f"(minimum {MIN_HASH_FILE_SIZE} bytes)."
        )

    hash_val = file_size

    with open(path, "rb") as f:
        # First 64KB
        for _ in range(_NUM_CHUNKS):
            chunk = f.read(_BYTESIZE)
            if len(chunk) < _BYTESIZE:
                break
            (val,) = _INT64_STRUCT.unpack(chunk)
            hash_val = (hash_val + val) & _MASK_64

        # Last 64KB
        f.seek(max(0, file_size - HASH_CHUNK_SIZE), os.SEEK_SET)
        for _ in range(_NUM_CHUNKS):
            chunk = f.read(_BYTESIZE)
            if len(chunk) < _BYTESIZE:
                break
            (val,) = _INT64_STRUCT.unpack(chunk)
            hash_val = (hash_val + val) & _MASK_64

    return f"{hash_val:016x}"
