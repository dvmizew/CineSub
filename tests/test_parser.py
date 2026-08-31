"""Tests for metadata parsing, encoding normalization, and language normalization."""

from __future__ import annotations

from pathlib import Path

from cinesub.core.utils import (
    decode_and_normalize_subtitle_content,
    normalize_language,
    parse_video_metadata,
    score_subtitle_candidate,
)


def test_normalize_language() -> None:
    """Test ISO 639-1 language code normalization."""
    assert normalize_language("ro") == "ro"
    assert normalize_language("rum") == "ro"
    assert normalize_language("romanian") == "ro"
    assert normalize_language("en") == "en"
    assert normalize_language("eng") == "en"
    assert normalize_language("english") == "en"
    assert normalize_language("es") == "es"
    assert normalize_language("spanish") == "es"


def test_decode_and_normalize_subtitle_content() -> None:
    """Test encoding normalization across UTF-8 BOM, CP1250, and UTF-8."""
    # 1. UTF-8 with BOM
    bom_data = b"\xef\xbb\xbf1\n00:00:01,000 --> 00:00:04,000\nSalut lume!\n"
    normalized = decode_and_normalize_subtitle_content(bom_data)
    assert not normalized.startswith(b"\xef\xbb\xbf")
    assert "Salut lume!" in normalized.decode("utf-8")

    # 2. Legacy CP1250 (Romanian diacritics: ş, ţ, ă, î, â)
    text_ro = (
        "1\n00:00:01,000 --> 00:00:04,000\nAcesta este un text cu diacritice: ş, ţ, ă, î, â.\n"
    )
    cp1250_data = text_ro.encode("cp1250")
    normalized_cp1250 = decode_and_normalize_subtitle_content(cp1250_data)
    assert "diacritice" in normalized_cp1250.decode("utf-8")

    # 3. Empty input
    assert decode_and_normalize_subtitle_content(b"") == b""


def test_parse_movie_filename(sample_video_file: Path) -> None:
    """Test parsing movie metadata."""
    meta = parse_video_metadata(sample_video_file)
    assert meta.title.lower() == "inception"
    assert meta.year == 2010
    assert meta.screen_size == "1080p"
    assert meta.source in ("BluRay", "Blu-ray")
    assert meta.release_group == "SPARKS"
    assert meta.is_episode is False
    assert meta.moviehash is not None


def test_parse_episode_filename(sample_episode_file: Path) -> None:
    """Test parsing TV episode metadata."""
    meta = parse_video_metadata(sample_episode_file)
    assert "breaking bad" in meta.title.lower()
    assert meta.season == 1
    assert meta.episode == 1
    assert meta.is_episode is True
    assert meta.screen_size == "720p"
    assert meta.release_group == "CTU"


def test_score_subtitle_candidate(sample_video_meta) -> None:
    """Test scoring logic with rapidfuzz."""
    # Hash match gets >= 100
    hash_score = score_subtitle_candidate(sample_video_meta, "Any.Name", matched_by_hash=True)
    assert hash_score >= 100.0

    # Matching release group gets higher score than non-matching
    match_score = score_subtitle_candidate(sample_video_meta, "Inception.1080p.BluRay.SPARKS")
    mismatch_score = score_subtitle_candidate(sample_video_meta, "Inception.720p.HDTV.DIMENSION")
    assert match_score > mismatch_score
