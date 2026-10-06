from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cinesub.modules.extractor import (
    EmbeddedSubtitleTrack,
    extract_embedded_subtitles_batch,
    extract_track_to_srt,
    extract_video_embedded_subtitles,
    inspect_embedded_subtitles,
)


def test_inspect_embedded_subtitles_file_not_found(tmp_path: Path) -> None:
    non_existent = tmp_path / "does_not_exist.mkv"
    with pytest.raises(FileNotFoundError):
        inspect_embedded_subtitles(non_existent)


def test_inspect_embedded_subtitles_missing_ffprobe(sample_video_file: Path) -> None:
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="ffprobe is not installed"):
            inspect_embedded_subtitles(sample_video_file)


def test_inspect_embedded_subtitles_parsing(sample_video_file: Path) -> None:
    probe_output = """{
        "streams": [
            {
                "index": 2,
                "codec_name": "subrip",
                "disposition": {
                    "default": 1,
                    "forced": 0,
                    "hearing_impaired": 1
                },
                "tags": {
                    "language": "eng",
                    "title": "English SDH"
                }
            },
            {
                "index": 3,
                "codec_name": "ass",
                "disposition": {
                    "default": 0,
                    "forced": 1,
                    "hearing_impaired": 0
                },
                "tags": {
                    "language": "ron",
                    "title": "Romanian Forced"
                }
            },
            {
                "index": 4,
                "codec_name": "hdmv_pgs_subtitle",
                "disposition": {
                    "default": 0,
                    "forced": 0,
                    "hearing_impaired": 0
                },
                "tags": {
                    "language": "fra",
                    "title": "French PGS"
                }
            }
        ]
    }"""

    mock_proc = MagicMock()
    mock_proc.stdout = probe_output
    mock_proc.returncode = 0

    with (
        patch("shutil.which", return_value="/usr/bin/ffprobe"),
        patch("subprocess.run", return_value=mock_proc),
    ):
        tracks = inspect_embedded_subtitles(sample_video_file)
        assert len(tracks) == 3

        # Track 0: English SDH subrip
        track_en = tracks[0]
        assert track_en.stream_index == 2
        assert track_en.codec_name == "subrip"
        assert track_en.language == "en"
        assert track_en.title == "English SDH"
        assert track_en.is_default is True
        assert track_en.is_sdh is True
        assert track_en.is_forced is False
        assert track_en.is_text_based is True

        # Track 1: Romanian Forced ASS
        track_ro = tracks[1]
        assert track_ro.stream_index == 3
        assert track_ro.codec_name == "ass"
        assert track_ro.language == "ro"
        assert track_ro.title == "Romanian Forced"
        assert track_ro.is_forced is True
        assert track_ro.is_text_based is True

        # Track 2: French PGS (bitmap)
        track_fr = tracks[2]
        assert track_fr.stream_index == 4
        assert track_fr.codec_name == "hdmv_pgs_subtitle"
        assert track_fr.language == "fr"
        assert track_fr.is_text_based is False


def test_inspect_embedded_subtitles_empty_or_failed(sample_video_file: Path) -> None:
    with (
        patch("shutil.which", return_value="/usr/bin/ffprobe"),
        patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, ["ffprobe"])),
    ):
        tracks = inspect_embedded_subtitles(sample_video_file)
        assert tracks == []


def test_extract_track_to_srt(sample_video_file: Path, tmp_path: Path) -> None:
    target_srt = tmp_path / "Inception.ro.srt"
    track = EmbeddedSubtitleTrack(
        stream_index=2,
        codec_name="subrip",
        language="ro",
    )

    def mock_ffmpeg_run(cmd, **kwargs):
        out_target = Path(cmd[-1])
        out_target.write_text("1\n00:00:01,000 --> 00:00:04,000\nSubtitrare Română\n")
        return MagicMock(returncode=0)

    with (
        patch("shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("subprocess.run", side_effect=mock_ffmpeg_run),
    ):
        result_path = extract_track_to_srt(sample_video_file, track, target_srt)
        assert result_path == target_srt
        assert target_srt.is_file()
        assert "Subtitrare Română" in target_srt.read_text(encoding="utf-8")


def test_extract_video_embedded_subtitles_workflow(sample_video_file: Path, tmp_path: Path) -> None:
    tracks = [
        EmbeddedSubtitleTrack(
            stream_index=2,
            codec_name="subrip",
            language="ro",
            is_forced=False,
        ),
        EmbeddedSubtitleTrack(
            stream_index=3,
            codec_name="subrip",
            language="en",
            is_forced=True,
        ),
        EmbeddedSubtitleTrack(
            stream_index=4,
            codec_name="hdmv_pgs_subtitle",
            language="ro",
            is_forced=False,
        ),
    ]

    with patch("cinesub.modules.extractor.inspect_embedded_subtitles", return_value=tracks):
        # 1. Dry run
        dry_results = extract_video_embedded_subtitles(
            sample_video_file, language="all", dry_run=True
        )
        assert len(dry_results) == 3
        assert dry_results[0]["status"] == "dry_run"
        assert dry_results[1]["status"] == "dry_run"
        assert dry_results[2]["status"] == "unsupported_codec"

        # 2. Language filter: 'ro' only
        ro_results = extract_video_embedded_subtitles(
            sample_video_file, language="ro", dry_run=True
        )
        assert len(ro_results) == 2
        assert all(r["language"] == "ro" for r in ro_results)

        # 3. Live extraction with mock ffmpeg
        def mock_extract(video_p, trk, target_p):
            target_p.write_text("SRT content")
            return target_p

        with patch("cinesub.modules.extractor.extract_track_to_srt", side_effect=mock_extract):
            live_results = extract_video_embedded_subtitles(
                sample_video_file, language="ro", force=False
            )
            assert len(live_results) == 2
            success_item = next(r for r in live_results if r["status"] == "success")
            assert "Inception.2010.1080p.BluRay.x264-SPARKS.ro.srt" in success_item["target_path"]

            # 4. Skip existing when force=False
            skip_results = extract_video_embedded_subtitles(
                sample_video_file, language="ro", force=False
            )
            skipped_item = next(r for r in skip_results if r["status"] == "skipped")
            assert "already exists" in skipped_item["message"]


def test_extract_embedded_subtitles_batch(tmp_path: Path) -> None:
    video_1 = tmp_path / "Movie1.mkv"
    video_1.write_bytes(b"\x00" * 2048)
    video_2 = tmp_path / "Movie2.mp4"
    video_2.write_bytes(b"\x00" * 2048)

    mock_track = EmbeddedSubtitleTrack(
        stream_index=2,
        codec_name="subrip",
        language="ro",
    )

    with (
        patch("cinesub.modules.extractor.inspect_embedded_subtitles", return_value=[mock_track]),
        patch(
            "cinesub.modules.extractor.extract_track_to_srt",
            side_effect=lambda v, t, dest: dest.touch() or dest,
        ),
    ):
        batch_report = extract_embedded_subtitles_batch(
            path=tmp_path,
            language="ro",
            force=True,
            dry_run=False,
        )
        assert batch_report["status"] == "success"
        assert batch_report["total_files"] == 2
        assert batch_report["successful"] == 2
        assert batch_report["failed"] == 0


def test_extract_track_missing_ffmpeg(sample_video_file: Path, tmp_path: Path) -> None:
    track = EmbeddedSubtitleTrack(stream_index=2, codec_name="subrip", language="en")
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="ffmpeg is not installed"):
            extract_track_to_srt(sample_video_file, track, tmp_path / "test.srt")


def test_extract_track_ffmpeg_failure_and_cleanup(sample_video_file: Path, tmp_path: Path) -> None:
    target_srt = tmp_path / "Sub.srt"
    track = EmbeddedSubtitleTrack(stream_index=2, codec_name="subrip", language="en")

    def mock_failing_ffmpeg(cmd, **kwargs):
        temp_file = Path(cmd[-1])
        temp_file.write_text("corrupted partial output")
        raise subprocess.CalledProcessError(1, cmd)

    with (
        patch("shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("subprocess.run", side_effect=mock_failing_ffmpeg),
    ):
        with pytest.raises(subprocess.CalledProcessError):
            extract_track_to_srt(sample_video_file, track, target_srt)

        # Confirm temporary file was cleaned up and destination was not created
        assert not target_srt.exists()
        temp_files = list(tmp_path.glob(".*.tmp"))
        assert len(temp_files) == 0


def test_extract_video_embedded_subtitles_forced_track_naming(
    sample_video_file: Path,
) -> None:
    track_forced = EmbeddedSubtitleTrack(
        stream_index=3,
        codec_name="subrip",
        language="ro",
        is_forced=True,
    )
    with patch("cinesub.modules.extractor.inspect_embedded_subtitles", return_value=[track_forced]):
        results = extract_video_embedded_subtitles(sample_video_file, language="ro", dry_run=True)
        assert len(results) == 1
        assert results[0]["target_path"].endswith(".ro.forced.srt")
