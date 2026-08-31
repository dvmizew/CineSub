"""Shared test fixtures for CineSub."""

from __future__ import annotations

from pathlib import Path

import pytest

from cinesub.core.models import VideoMetadata


@pytest.fixture
def sample_video_file(tmp_path: Path) -> Path:
    """Create a dummy video file of sufficient size (>= 128KB) for hash calculation."""
    video_file = tmp_path / "Inception.2010.1080p.BluRay.x264-SPARKS.mp4"
    chunk_size = 65536
    data = b"\x00" * chunk_size + b"\x01" * chunk_size
    video_file.write_bytes(data)
    return video_file


@pytest.fixture
def sample_episode_file(tmp_path: Path) -> Path:
    """Create a dummy TV episode video file."""
    video_file = tmp_path / "Breaking.Bad.S01E01.720p.HDTV.x264-CTU.mkv"
    chunk_size = 65536
    data = b"\x02" * (chunk_size * 2)
    video_file.write_bytes(data)
    return video_file


@pytest.fixture
def sample_srt_file(tmp_path: Path) -> Path:
    """Create a sample SRT subtitle file."""
    srt_file = tmp_path / "test.srt"
    srt_content = (
        "1\n"
        "00:00:01,000 --> 00:00:04,000\n"
        "Hello, this is a test subtitle line.\n\n"
        "2\n"
        "00:00:05,000 --> 00:00:08,000\n"
        "Synchronized with CineSub.\n"
    )
    srt_file.write_text(srt_content, encoding="utf-8")
    return srt_file


@pytest.fixture
def sample_video_meta(sample_video_file: Path) -> VideoMetadata:
    """Return a sample VideoMetadata instance."""
    return VideoMetadata(
        file_path=sample_video_file,
        title="Inception",
        year=2010,
        release_group="SPARKS",
        screen_size="1080p",
        source="BluRay",
        video_codec="x264",
        is_episode=False,
        moviehash="0123456789abcdef",
        file_size=sample_video_file.stat().st_size,
    )
