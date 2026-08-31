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

    text_cyrillic = (
        "1\n00:00:01,000 --> 00:00:04,000\nПривет, это тестовая субтитра для фильма.\n\n"
        "2\n00:00:05,000 --> 00:00:08,000\nВторая строка с русским текстом.\n"
    )
    cp1251_data = text_cyrillic.encode("cp1251")
    normalized_cyrillic = decode_and_normalize_subtitle_content(cp1251_data)
    assert "Привет, это тестовая субтитра" in normalized_cyrillic.decode("utf-8")

    assert decode_and_normalize_subtitle_content(b"") == b""


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
