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
    assert "SubSource" in captured.out
    assert "Subs.ro" in captured.out
    assert "BetaSeries" in captured.out
    assert "FFprobe" in captured.out


def test_cli_sync(sample_video_file: Path, capsys) -> None:
    mock_report = {
        "command": "download",
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
                    "score": 95.0,
                    "saved_path": str(sample_video_file.with_suffix(".srt")),
                },
                "status": "success",
            }
        ],
    }

    with patch("cinesub.cli.main.download_batch", return_value=mock_report):
        app(["download", str(sample_video_file), "-l", "en"])
        captured = capsys.readouterr()
        assert "Subtitle Download Complete" in captured.out
        assert "Inception" in captured.out
        assert "95.0" in captured.out

        # Verify backward-compatible 'sync' alias
        app(["sync", str(sample_video_file), "-l", "en"])
        captured_alias = capsys.readouterr()
        assert "Subtitle Download Complete" in captured_alias.out


def test_cli_sync_json_and_threads(tmp_path: Path, sample_video_file: Path, capsys) -> None:
    json_out = tmp_path / "sync_report.json"
    mock_report = {
        "command": "download",
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

    with patch("cinesub.cli.main.download_batch", return_value=mock_report) as mock_fn:
        app(["download", str(sample_video_file), "-l", "ro", "-t", "8", "-j", str(json_out)])
        mock_fn.assert_called_once()
        _, kwargs = mock_fn.call_args
        assert kwargs["threads"] == 8
        assert kwargs["json_path"] == json_out


def test_cli_dry_run_sync(sample_video_file: Path, capsys) -> None:
    mock_report = {
        "command": "download",
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
                    "score": 90.0,
                    "saved_path": str(sample_video_file.with_suffix(".srt")),
                },
                "status": "dry_run",
            }
        ],
    }

    with patch("cinesub.cli.main.download_batch", return_value=mock_report) as mock_fn:
        app(["download", str(sample_video_file), "-l", "ro", "--dry-run"])
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
        "command": "download",
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
    assert b'"command": "download"' in json_p.read_bytes()

    jsonl_p = tmp_path / "report.jsonl"
    save_report_output(report_data, jsonl_p)
    assert jsonl_p.is_file()
    lines = jsonl_p.read_bytes().strip().split(b"\n")
    assert len(lines) == 3
    assert b"movie1.mkv" in lines[0]
    assert b"movie2.mkv" in lines[1]
    assert b"summary" in lines[2]


def test_cli_tmdb_sync(sample_video_file: Path, capsys) -> None:
    from unittest.mock import MagicMock

    mock_tmdb = MagicMock()
    mock_tmdb.is_configured = True
    mock_tmdb.search_movie.return_value = {
        "id": 27205,
        "title": "Inception",
        "release_date": "2010-07-16",
    }
    mock_tmdb.get_external_ids.return_value = {"imdb_id": "tt1375666"}
    mock_tmdb.add_to_favorite.return_value = True
    mock_tmdb.add_to_watchlist.return_value = True

    with patch("cinesub.cli.main.TmdbService", return_value=mock_tmdb):
        sync_result = app(["tmdb", str(sample_video_file), "--favorite", "--watchlist"])
        assert sync_result["status"] == "success"
        captured = capsys.readouterr()
        assert "TMDb Media Synchronization" in captured.out
        assert "Inception" in captured.out
        assert "Favorited" in captured.out
        assert "Watchlisted" in captured.out


def test_cli_tmdb_bookmark_alias(sample_video_file: Path, capsys) -> None:
    from unittest.mock import MagicMock

    mock_tmdb = MagicMock()
    mock_tmdb.is_configured = True
    mock_tmdb.search_movie.return_value = {
        "id": 27205,
        "title": "Inception",
        "release_date": "2010-07-16",
    }
    mock_tmdb.get_external_ids.return_value = {"imdb_id": "tt1375666"}
    mock_tmdb.add_to_watchlist.return_value = True

    with patch("cinesub.cli.main.TmdbService", return_value=mock_tmdb):
        sync_result = app(["tmdb", str(sample_video_file), "--bookmark"])
        assert sync_result["status"] == "success"
        mock_tmdb.add_to_watchlist.assert_called_once_with(27205, is_tv=False, watchlist=True)
        captured = capsys.readouterr()
        assert "Bookmarked" in captured.out


def test_cli_tmdb_folder_mode_and_fallback(tmp_path: Path, capsys) -> None:
    from unittest.mock import MagicMock

    # Create directory with two movie subfolders and NO video files
    library_dir = tmp_path / "Movies"
    library_dir.mkdir()
    (library_dir / "Inception (2010)").mkdir()
    (library_dir / "Gladiator (2000)").mkdir()

    mock_tmdb = MagicMock()
    mock_tmdb.is_configured = True

    def mock_search_movie(title: str, year: int | None = None):
        if "inception" in title.lower():
            return {"id": 27205, "title": "Inception", "release_date": "2010-07-16"}
        if "gladiator" in title.lower():
            return {"id": 98, "title": "Gladiator", "release_date": "2000-05-01"}
        return None

    mock_tmdb.search_movie.side_effect = mock_search_movie
    mock_tmdb.get_external_ids.return_value = {"imdb_id": "tt0000000"}
    mock_tmdb.add_to_watchlist.return_value = True

    with patch("cinesub.cli.main.TmdbService", return_value=mock_tmdb):
        sync_result = app(["tmdb", str(library_dir), "-b"])
        assert sync_result["status"] == "success"
        assert sync_result["total_files"] == 2
        assert mock_tmdb.add_to_watchlist.call_count == 2
        captured = capsys.readouterr()
        assert "Bookmarked" in captured.out


def test_cli_tmdb_by_folder_flag(tmp_path: Path, capsys) -> None:
    from unittest.mock import MagicMock

    movie_dir = tmp_path / "Interstellar (2014)"
    movie_dir.mkdir()
    # Dummy video file inside with generic name
    (movie_dir / "movie.mkv").write_bytes(b"\x00" * 1024)

    mock_tmdb = MagicMock()
    mock_tmdb.is_configured = True
    mock_tmdb.search_movie.return_value = {
        "id": 157336,
        "title": "Interstellar",
        "release_date": "2014-11-05",
    }
    mock_tmdb.get_external_ids.return_value = {"imdb_id": "tt0816692"}
    mock_tmdb.add_to_watchlist.return_value = True

    with patch("cinesub.cli.main.TmdbService", return_value=mock_tmdb):
        sync_result = app(["tmdb", str(movie_dir), "-F", "-b"])
        assert sync_result["status"] == "success"
        assert sync_result["results"][0]["title"] == "Interstellar"
        mock_tmdb.search_movie.assert_called_with("Interstellar", 2014)


def test_cli_tmdb_deduplication(tmp_path: Path, capsys) -> None:
    from unittest.mock import MagicMock

    movie_dir = tmp_path / "The.Matrix.1999"
    movie_dir.mkdir()
    (movie_dir / "cd1.avi").write_bytes(b"\x00" * 1024)
    (movie_dir / "cd2.avi").write_bytes(b"\x00" * 1024)

    mock_tmdb = MagicMock()
    mock_tmdb.is_configured = True
    mock_tmdb.search_movie.return_value = {
        "id": 603,
        "title": "The Matrix",
        "release_date": "1999-03-30",
    }
    mock_tmdb.get_external_ids.return_value = {"imdb_id": "tt0133093"}
    mock_tmdb.add_to_watchlist.return_value = True

    with patch("cinesub.cli.main.TmdbService", return_value=mock_tmdb):
        sync_result = app(["tmdb", str(movie_dir), "-b"])
        assert sync_result["status"] == "success"
        # add_to_watchlist called once despite 2 video files
        mock_tmdb.add_to_watchlist.assert_called_once_with(603, is_tv=False, watchlist=True)
        captured = capsys.readouterr()
        assert "Bookmarked (already)" in captured.out


def test_cli_extract_command(sample_video_file: Path, capsys) -> None:
    mock_report = {
        "status": "success",
        "total_files": 1,
        "total_tracks": 1,
        "successful": 1,
        "dry_run_count": 0,
        "skipped": 0,
        "failed": 0,
        "duration_seconds": 0.25,
        "results": [
            {
                "video_file": sample_video_file.name,
                "stream_index": 2,
                "codec": "subrip",
                "language": "ro",
                "target_path": str(sample_video_file.with_suffix(".ro.srt")),
                "status": "success",
                "message": "Extracted & UTF-8 Normalized",
            }
        ],
    }

    with patch("cinesub.cli.main.extract_embedded_subtitles_batch", return_value=mock_report):
        result = app(["extract", str(sample_video_file), "--language", "ro"])
        assert result["status"] == "success"
        captured = capsys.readouterr()
        assert "Embedded Subtitle Extraction Results" in captured.out
        assert "EXTRACTED" in captured.out
        assert "RO" in captured.out


def test_cli_extract_dry_run(sample_video_file: Path, capsys) -> None:
    mock_report = {
        "status": "success",
        "total_files": 1,
        "total_tracks": 1,
        "successful": 0,
        "dry_run_count": 1,
        "skipped": 0,
        "failed": 0,
        "duration_seconds": 0.05,
        "results": [
            {
                "video_file": sample_video_file.name,
                "stream_index": 2,
                "codec": "subrip",
                "language": "ro",
                "target_path": str(sample_video_file.with_suffix(".ro.srt")),
                "status": "dry_run",
                "message": "Simulated extraction",
            }
        ],
    }

    with patch("cinesub.cli.main.extract_embedded_subtitles_batch", return_value=mock_report):
        result = app(["extract", str(sample_video_file), "--language", "ro", "--dry-run"])
        assert result["status"] == "success"
        captured = capsys.readouterr()
        assert "Extract Dry-Run" in captured.out
        assert "DRY-RUN" in captured.out
