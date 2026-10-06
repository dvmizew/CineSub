"""Tests for metadata parsing, encoding normalization, and language normalization."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from cinesub.core.constants import SUPPORTED_VIDEO_EXTS
from cinesub.core.utils import (
    decode_and_normalize_subtitle_content,
    extract_best_subtitle_from_archive,
    find_video_files,
    has_existing_subtitle,
    normalize_language,
    parse_directory_metadata,
    parse_video_metadata,
    score_subtitle_candidate,
)


def test_normalize_language() -> None:
    assert normalize_language("ro") == "ro"
    assert normalize_language("rum") == "ro"
    assert normalize_language("romanian") == "ro"
    assert normalize_language("en") == "en"
    assert normalize_language("eng") == "en"
    assert normalize_language("english") == "en"
    assert normalize_language("es") == "es"
    assert normalize_language("spanish") == "es"


def test_decode_and_normalize_subtitle_content() -> None:
    bom_data = (
        b"\xef\xbb\xbf5\n00:00:01,000 --> 00:00:04,000\nSalut lume!\n\n"
        b"9\n00:00:05,000 --> 00:00:08,000\nA doua linie\n"
    )
    normalized = decode_and_normalize_subtitle_content(bom_data)
    assert not normalized.startswith(b"\xef\xbb\xbf")
    decoded_str = normalized.decode("utf-8")
    assert "Salut lume!" in decoded_str
    assert "1\n00:00:01,000 --> 00:00:04,000" in decoded_str
    assert "2\n00:00:05,000 --> 00:00:08,000" in decoded_str

    text_ro = (
        "1\n00:00:01,000 --> 00:00:04,000\nAcesta este un text cu diacritice: ş, ţ, ă, î, â.\n"
    )
    cp1250_data = text_ro.encode("cp1250")
    normalized_cp1250 = decode_and_normalize_subtitle_content(cp1250_data)
    assert "diacritice" in normalized_cp1250.decode("utf-8")

    # ISO-8859-2 (Latin-2) encoding
    iso_8859_2_data = text_ro.encode("iso-8859-2")
    normalized_iso = decode_and_normalize_subtitle_content(iso_8859_2_data)
    assert "diacritice" in normalized_iso.decode("utf-8")

    # Windows-1252 (CP1252) Western European encoding
    text_western = "1\n00:00:01,000 --> 00:00:04,000\nCafé français avec résumé et naïve.\n"
    cp1252_data = text_western.encode("windows-1252")
    normalized_cp1252 = decode_and_normalize_subtitle_content(cp1252_data)
    assert "français" in normalized_cp1252.decode("utf-8")

    # Corrupt / Non-standard SRT structure fallback
    corrupt_srt = b"NOT AN SRT TIMECODE AT ALL\nJust plain corrupted text line."
    normalized_corrupt = decode_and_normalize_subtitle_content(corrupt_srt)
    assert "corrupted text" in normalized_corrupt.decode("utf-8")

    text_cyrillic = (
        "1\n00:00:01,000 --> 00:00:04,000\nПривет, это тестовая субтитра для фильма.\n\n"
        "2\n00:00:05,000 --> 00:00:08,000\nВторая строка с русским текстом.\n"
    )
    cp1251_data = text_cyrillic.encode("cp1251")
    normalized_cyrillic = decode_and_normalize_subtitle_content(cp1251_data)
    assert "Привет, это тестовая субтитра" in normalized_cyrillic.decode("utf-8")

    assert decode_and_normalize_subtitle_content(b"") == b""

    with pytest.raises(ValueError, match="too large"):
        decode_and_normalize_subtitle_content(b"A" * (11 * 1024 * 1024))


def test_parse_movie_filename(sample_video_file: Path) -> None:
    meta = parse_video_metadata(sample_video_file)
    assert meta.title.lower() == "inception"
    assert meta.year == 2010
    assert meta.screen_size == "1080p"
    assert meta.source in ("BluRay", "Blu-ray")
    assert meta.release_group == "SPARKS"
    assert meta.is_episode is False
    assert meta.moviehash is not None


def test_parse_episode_filename(sample_episode_file: Path) -> None:
    meta = parse_video_metadata(sample_episode_file)
    assert "breaking bad" in meta.title.lower()
    assert meta.season == 1
    assert meta.episode == 1
    assert meta.is_episode is True
    assert meta.screen_size == "720p"
    assert meta.release_group == "CTU"


def test_score_subtitle_candidate(sample_video_meta) -> None:
    hash_score = score_subtitle_candidate(sample_video_meta, "Any.Name", matched_by_hash=True)
    assert hash_score >= 100.0

    match_score = score_subtitle_candidate(sample_video_meta, "Inception.1080p.BluRay.SPARKS")
    mismatch_score = score_subtitle_candidate(sample_video_meta, "Inception.720p.HDTV.DIMENSION")
    assert match_score > mismatch_score


def test_find_video_files_nas_pruning(tmp_path: Path) -> None:
    movie = tmp_path / "Movies" / "Inception (2010)" / "Inception.2010.1080p.mkv"
    movie.parent.mkdir(parents=True, exist_ok=True)
    movie.write_bytes(b"\x00" * 1024)

    syno_junk = (
        tmp_path
        / "Movies"
        / "@eaDir"
        / "Inception (2010)"
        / "Inception.2010.1080p.mkv@SynoEAStream"
    )
    syno_junk.parent.mkdir(parents=True, exist_ok=True)
    syno_junk.write_bytes(b"\x00" * 512)

    plex_junk = tmp_path / "Movies" / ".plex" / "thumb.mkv"
    plex_junk.parent.mkdir(parents=True, exist_ok=True)
    plex_junk.write_bytes(b"\x00" * 512)

    recycle_junk = tmp_path / "Movies" / "#recycle" / "deleted.mp4"
    recycle_junk.parent.mkdir(parents=True, exist_ok=True)
    recycle_junk.write_bytes(b"\x00" * 512)

    featurette = tmp_path / "Movies" / "Inception (2010)" / "Featurettes" / "behind_scenes.mp4"
    featurette.parent.mkdir(parents=True, exist_ok=True)
    featurette.write_bytes(b"\x00" * 512)

    found = find_video_files(tmp_path)
    assert len(found) == 1
    assert found[0] == movie


def test_has_existing_subtitle(tmp_path: Path) -> None:
    video = tmp_path / "Dune.Part.Two.2024.mkv"
    video.write_bytes(b"\x00" * 1024)

    assert has_existing_subtitle(video, "ro") is None

    ro_sub = tmp_path / "Dune.Part.Two.2024.ro.srt"
    ro_sub.write_text("1\n00:00:01,000 --> 00:00:04,000\nTest\n")

    existing_sub = has_existing_subtitle(video, "ro")
    assert existing_sub == ro_sub


def test_all_video_container_extensions_accepted(tmp_path: Path) -> None:
    created_files = []
    for ext in SUPPORTED_VIDEO_EXTS:
        clean_ext = ext.lstrip(".")
        f = tmp_path / f"Sample_Movie_{clean_ext}{ext}"
        f.write_bytes(b"\x00" * 1024)
        created_files.append(f)

    found = find_video_files(tmp_path)
    assert len(found) == len(SUPPORTED_VIDEO_EXTS)
    assert len(found) >= 25


def test_parse_directory_metadata(tmp_path: Path) -> None:
    movie_dir = tmp_path / "Inception (2010)"
    movie_dir.mkdir()
    meta = parse_directory_metadata(movie_dir)
    assert meta.title.lower() == "inception"
    assert meta.year == 2010
    assert meta.is_episode is False

    tv_dir = tmp_path / "Breaking Bad Season 1"
    tv_dir.mkdir()
    tv_meta = parse_directory_metadata(tv_dir)
    assert "breaking bad" in tv_meta.title.lower()
    assert tv_meta.season == 1

    with pytest.raises(NotADirectoryError):
        parse_directory_metadata(tmp_path / "non_existent_folder")


def test_parse_video_metadata_parent_fallback(tmp_path: Path) -> None:
    gladiator_dir = tmp_path / "Gladiator (2000)"
    gladiator_dir.mkdir()
    cd1_file = gladiator_dir / "cd1.avi"
    cd1_file.write_bytes(b"\x00" * 1024)

    meta = parse_video_metadata(cd1_file, compute_hash=False)
    assert meta.title.lower() == "gladiator"
    assert meta.year == 2000

    interstellar_dir = tmp_path / "Interstellar (2014)"
    interstellar_dir.mkdir()
    movie_file = interstellar_dir / "movie.mkv"
    movie_file.write_bytes(b"\x00" * 1024)

    interstellar_meta = parse_video_metadata(movie_file, compute_hash=False)
    assert interstellar_meta.title.lower() == "interstellar"
    assert interstellar_meta.year == 2014


def test_extract_best_subtitle_from_archive() -> None:
    # 1. Non-zip payload pass-through
    raw_text = b"1\n00:00:01,000 --> 00:00:02,000\nPlain SRT\n"
    assert extract_best_subtitle_from_archive(raw_text, "movie") == raw_text

    # 2. Empty payload raises ValueError
    with pytest.raises(ValueError, match="Archive payload is empty"):
        extract_best_subtitle_from_archive(b"", "movie")

    # 3. Zip with multiple files, picking best match stem and ignoring hidden files
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr(".DS_Store", b"junk")
        zf.writestr(".hidden.srt", b"hidden")
        zf.writestr("Other.Release.srt", b"1\n00:00:01,000 --> 00:00:02,000\nOther\n")
        zf.writestr("Target.Movie.1080p.srt", b"1\n00:00:01,000 --> 00:00:02,000\nTarget\n")
    zip_bytes = zip_buf.getvalue()

    extracted = extract_best_subtitle_from_archive(zip_bytes, "Target.Movie.1080p")
    assert b"Target" in extracted

    # 4. Zip without valid subtitle extensions raises ValueError
    empty_zip_buf = io.BytesIO()
    with zipfile.ZipFile(empty_zip_buf, "w") as zf:
        zf.writestr("readme.txt", b"instructions")
    with pytest.raises(ValueError, match="No valid subtitle file found"):
        extract_best_subtitle_from_archive(empty_zip_buf.getvalue(), "movie")
