"""Tests for the audio synchronization module (ffsubsync and alass)."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cinesub.modules.syncer import sync_subtitle_audio


def test_ffmpeg_detection() -> None:
    """Test ffmpeg detection with shutil.which."""
    res = shutil.which("ffmpeg")
    assert res is None or isinstance(res, str)


def test_sync_missing_files(tmp_path: Path, sample_video_file: Path, sample_srt_file: Path) -> None:
    """Test standard FileNotFoundError on missing files."""
    with pytest.raises(FileNotFoundError):
        sync_subtitle_audio(tmp_path / "ghost.mp4", sample_srt_file)

    with pytest.raises(FileNotFoundError):
        sync_subtitle_audio(sample_video_file, tmp_path / "ghost.srt")


def test_sync_execution_ffsubsync(sample_video_file: Path, sample_srt_file: Path) -> None:
    """Test successful ffsubsync execution, offset extraction, and backup creation."""

    def mock_subprocess_run(cmd, capture_output=True, text=True, check=False):
        if "-o" in cmd:
            out_idx = cmd.index("-o") + 1
            temp_out = Path(cmd[out_idx])
            temp_out.write_text("1\n00:00:02,000 --> 00:00:05,000\nSynced line\n")
        return MagicMock(
            returncode=0,
            stdout="",
            stderr="offset seconds: -0.350\nframerate scale factor: 1.0000\n",
        )

    with (
        patch("shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("cinesub.modules.syncer.subprocess.run", side_effect=mock_subprocess_run),
    ):
        res = sync_subtitle_audio(
            video_path=sample_video_file,
            srt_path=sample_srt_file,
            keep_backup=True,
            engine="ffsubsync",
        )

        assert res.success is True
        assert res.offset_seconds == -0.350
        assert res.framerate_scale == 1.0
        assert "-0.350s" in res.message
        assert "Synced line" in sample_srt_file.read_text()

        backup_p = sample_srt_file.parent / f"{sample_srt_file.stem}.orig.srt"
        assert backup_p.exists()
        assert "Hello, this is a test subtitle line" in backup_p.read_text()


def test_sync_execution_alass(sample_video_file: Path, sample_srt_file: Path) -> None:
    """Test alass sync execution."""

    def mock_subprocess_run(cmd, capture_output=True, text=True, check=False):
        out_path = Path(cmd[3])
        out_path.write_text("1\n00:00:03,000 --> 00:00:06,000\nAlass synced\n")
        return MagicMock(
            returncode=0,
            stdout="alass output",
            stderr="",
        )

    with (
        patch("shutil.which", return_value="/usr/bin/alass"),
        patch("cinesub.modules.syncer.subprocess.run", side_effect=mock_subprocess_run),
    ):
        res = sync_subtitle_audio(
            video_path=sample_video_file,
            srt_path=sample_srt_file,
            keep_backup=False,
            engine="alass",
        )

        assert res.success is True
        assert "alass" in res.message
        assert "Alass synced" in sample_srt_file.read_text()


def test_sync_missing_alass_binary(sample_video_file: Path, sample_srt_file: Path) -> None:
    """Test RuntimeError when alass binary is not found."""
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="alass binary not found"):
            sync_subtitle_audio(
                video_path=sample_video_file,
                srt_path=sample_srt_file,
                engine="alass",
            )
