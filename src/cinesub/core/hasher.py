from __future__ import annotations

import os
import struct
from pathlib import Path

from cinesub.core.constants import HASH_CHUNK_SIZE, MIN_HASH_FILE_SIZE


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
    bytesize = struct.calcsize("<q")
    num_chunks = HASH_CHUNK_SIZE // bytesize

    with open(path, "rb") as f:
        # First 64KB
        for _ in range(num_chunks):
            chunk = f.read(bytesize)
            if len(chunk) < bytesize:
                break
            (val,) = struct.unpack("<q", chunk)
            hash_val = (hash_val + val) & 0xFFFFFFFFFFFFFFFF

        # Last 64KB
        f.seek(max(0, file_size - HASH_CHUNK_SIZE), os.SEEK_SET)
        for _ in range(num_chunks):
            chunk = f.read(bytesize)
            if len(chunk) < bytesize:
                break
            (val,) = struct.unpack("<q", chunk)
            hash_val = (hash_val + val) & 0xFFFFFFFFFFFFFFFF

    return f"{hash_val:016x}"
