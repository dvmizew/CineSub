from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from cinesub.cli.main import app
from cinesub.modules.downloader import save_report_output


def test_cli_help(capsys) -> None:
    app(["--help"])
    captured = capsys.readouterr()
    assert "CineSub" in captured.out or "cinesub" in captured.out


def test_cli_config(capsys) -> None:
    app(["config"])
    captured = capsys.readouterr()
    assert "Configuration Status" in captured.out
    assert "OpenSubtitles" in captured.out
    assert "SubDL" in captured.out


def test_cli_sync(sample_video_file: Path, capsys) -> None:
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


def test_cli_dry_run_sync(sample_video_file: Path, capsys) -> None:
    mock_report = {
        "command": "sync",
        "dry_run": True,
        "total_files": 1,
        "successful": 0,
        "dry_run_count": 1,
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
                    "language": "ro",
                    "release_name": "Inception.1080p.SPARKS",
                    "matched_by_hash": True,
                    "saved_path": str(sample_video_file.with_suffix(".srt")),
                },
                "sync": {
                    "success": True,
                    "offset_seconds": 0.0,
                    "message": "Simulated (Dry Run)",
                },
                "status": "dry_run",
            }
        ],
    }

    with patch("cinesub.cli.main.download_and_sync_batch", return_value=mock_report) as mock_fn:
        app(["sync", str(sample_video_file), "-l", "ro", "--dry-run"])
        mock_fn.assert_called_once()
        _, kwargs = mock_fn.call_args
        assert kwargs["dry_run"] is True

        captured = capsys.readouterr()
        assert "Dry-Run Simulation" in captured.out


def test_cli_bulk(sample_video_file: Path, capsys) -> None:
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


def test_save_report_json_and_jsonl(tmp_path: Path) -> None:
    report_data = {
        "timestamp": "2026-08-31T12:00:00Z",
        "command": "sync",
        "dry_run": False,
        "total_files": 2,
        "successful": 2,
        "skipped": 0,
        "dry_run_count": 0,
        "failed": 0,
        "duration_seconds": 1.5,
        "throughput_files_per_sec": 1.3,
        "results": [
            {"video_file": "movie1.mkv", "status": "success"},
            {"video_file": "movie2.mkv", "status": "success"},
        ],
    }

    json_p = tmp_path / "report.json"
    save_report_output(report_data, json_p)
    assert json_p.is_file()
    assert b'"command": "sync"' in json_p.read_bytes()

    jsonl_p = tmp_path / "report.jsonl"
    save_report_output(report_data, jsonl_p)
    assert jsonl_p.is_file()
    lines = jsonl_p.read_bytes().strip().split(b"\n")
    assert len(lines) == 3
    assert b"movie1.mkv" in lines[0]
    assert b"movie2.mkv" in lines[1]
    assert b"summary" in lines[2]
