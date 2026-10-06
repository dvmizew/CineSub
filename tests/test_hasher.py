from __future__ import annotations

from pathlib import Path

import pytest

from cinesub.core.hasher import calculate_movie_hash


def test_calculate_movie_hash_valid(sample_video_file: Path) -> None:
    """Test hash calculation on valid file."""
    moviehash = calculate_movie_hash(sample_video_file)
    assert isinstance(moviehash, str)
    assert len(moviehash) == 16
    int(moviehash, 16)


def test_calculate_movie_hash_file_not_found(tmp_path: Path) -> None:
    """Test non-existent file raises FileNotFoundError."""
    non_existent = tmp_path / "ghost.mp4"
    with pytest.raises(FileNotFoundError):
        calculate_movie_hash(non_existent)


def test_calculate_movie_hash_too_small(tmp_path: Path) -> None:
    """Test file smaller than 128KB raises ValueError."""
    small_file = tmp_path / "tiny.mp4"
    small_file.write_bytes(b"small video content")
    with pytest.raises(ValueError, match="too small"):
        calculate_movie_hash(small_file)
