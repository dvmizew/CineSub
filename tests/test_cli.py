"""Tests for Cyclopts CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from cinesub.cli.main import app


def test_cli_help(capsys) -> None:
    """Test CLI help output."""
    app(["--help"])
    captured = capsys.readouterr()
    assert "CineSub" in captured.out or "cinesub" in captured.out


def test_cli_config(capsys) -> None:
    """Test CLI config command."""
    app(["config"])
    captured = capsys.readouterr()
    assert "Configuration Status" in captured.out
    assert "OpenSubtitles" in captured.out
    assert "SubDL" in captured.out


def test_cli_sync(sample_video_file: Path, capsys) -> None:
    """Test sync command with mocked batch downloader."""
    mock_report = {
        "command": "sync",
        "total_files": 1,
        "successful": 1,
        "failed": 0,
        "results": [
            {
                "video_file": sample_video_file.name,
                "video_path": str(sample_video_file),
                "title": "Inception",
                "year": 2010,
                "moviehash": "0123456789abcdef",
                "subtitle": {
                    "provider": "opensubtitles",
                    "language": "en",
                    "release_name": "Inception.1080p.SPARKS",
                    "matched_by_hash": True,
                    "saved_path": str(sample_video_file.with_suffix(".srt")),
                },
                "sync": {
                    "success": True,
                    "offset_seconds": -0.350,
                },
                "status": "success",
            }
        ],
    }

    with patch("cinesub.cli.main.download_and_sync_batch", return_value=mock_report):
        app(["sync", str(sample_video_file), "-l", "en"])
        captured = capsys.readouterr()
        assert "Subtitle Download Complete" in captured.out
        assert "Inception" in captured.out
        assert "-0.350s" in captured.out


def test_cli_sync_json_and_threads(tmp_path: Path, sample_video_file: Path, capsys) -> None:
    """Test sync command with --threads and --json options."""
    json_out = tmp_path / "sync_report.json"
    mock_report = {
        "command": "sync",
        "total_files": 1,
        "successful": 1,
        "failed": 0,
        "results": [
            {
                "video_file": sample_video_file.name,
                "status": "success",
            }
        ],
    }

    with patch("cinesub.cli.main.download_and_sync_batch", return_value=mock_report) as mock_fn:
        app(["sync", str(sample_video_file), "-l", "ro", "-t", "8", "-j", str(json_out)])
        mock_fn.assert_called_once()
        _, kwargs = mock_fn.call_args
        assert kwargs["threads"] == 8
        assert kwargs["json_path"] == json_out


def test_cli_bulk(sample_video_file: Path, capsys) -> None:
    """Test bulk command with mocked batch downloader."""
    dest_srt = sample_video_file.parent / "Inception_1_opensubtitles.srt"
    mock_report = {
        "command": "bulk",
        "total_files": 1,
        "successful": 1,
        "failed": 0,
        "results": [
            {
                "video_file": sample_video_file.name,
                "subtitles": [
                    {
                        "id": "1",
                        "provider": "opensubtitles",
                        "release_name": "Inception.1080p",
                        "score": 100.0,
                        "saved_path": str(dest_srt),
                    },
                    {
                        "id": "2",
                        "provider": "subdl",
                        "release_name": "Inception.720p",
                        "score": 50.0,
                        "saved_path": str(sample_video_file.parent / "Inception_2_subdl.srt"),
                    },
                ],
                "status": "success",
            }
        ],
    }

    with patch("cinesub.cli.main.download_bulk_batch", return_value=mock_report):
        app(["bulk", str(sample_video_file), "-l", "en", "-n", "2"])
        captured = capsys.readouterr()
        assert "Downloaded 2 Subtitle Alternatives" in captured.out
        assert "OPENSUBTITLES" in captured.out
        assert "SUBDL" in captured.out
