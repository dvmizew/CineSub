from __future__ import annotations

import os
import struct
from pathlib import Path

from cinesub.core.constants import HASH_CHUNK_SIZE, MIN_HASH_FILE_SIZE

_CHUNKS_COUNT = HASH_CHUNK_SIZE // 8
_CHUNKS_STRUCT = struct.Struct(f"<{_CHUNKS_COUNT}q")
_MASK_64 = 0xFFFFFFFFFFFFFFFF


def calculate_movie_hash(file_path: str | Path) -> str:
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {path}")

    file_size = path.stat().st_size
    if file_size < MIN_HASH_FILE_SIZE:
        raise ValueError(
            f"File is too small ({file_size} bytes) for OpenSubtitles hash "
            f"(minimum {MIN_HASH_FILE_SIZE} bytes)."
        )

    with open(path, "rb") as f:
        head = f.read(HASH_CHUNK_SIZE)
        f.seek(max(0, file_size - HASH_CHUNK_SIZE), os.SEEK_SET)
        tail = f.read(HASH_CHUNK_SIZE)

    hash_val = file_size + sum(_CHUNKS_STRUCT.unpack(head)) + sum(_CHUNKS_STRUCT.unpack(tail))
    return f"{hash_val & _MASK_64:016x}"
