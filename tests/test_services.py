from __future__ import annotations

import gzip
import io
import lzma
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.modules.downloader import get_active_services
from cinesub.services.animetosho import AnimeToshoService
from cinesub.services.assrt import AssrtService
from cinesub.services.betaseries import BetaSeriesService
from cinesub.services.bsplayer import BsplayerService
from cinesub.services.gestdown import GestdownService
from cinesub.services.opensubtitles import OpenSubtitlesService
from cinesub.services.subdl import SubdlService
from cinesub.services.subsource import SubsourceService
from cinesub.services.subsro import SubsRoService
from cinesub.services.tmdb import TmdbService


def test_opensubtitles_search_and_download(
    sample_video_meta: VideoMetadata, tmp_path: Path
) -> None:
    service = OpenSubtitlesService(api_key="mock_os_key")

    mock_search_payload = {
        "data": [
            {
                "id": "101",
                "attributes": {
                    "language": "en",
                    "release": "Inception.2010.1080p.BluRay.x264-SPARKS",
                    "ratings": 9.0,
                    "download_count": 500,
                    "files": [{"file_id": 9999, "file_name": "Inception.srt"}],
                },
            }
        ]
    }

    def mock_request(method, url, **kwargs):
        if "/subtitles" in url:
            return httpx.Response(200, json=mock_search_payload, request=httpx.Request(method, url))
        if "/download" in url:
            return httpx.Response(
                200,
                json={"link": "https://dl.opensubtitles.org/sub.srt"},
                request=httpx.Request(method, url),
            )
        if "dl.opensubtitles.org" in url:
            return httpx.Response(
                200,
                content=b"1\n00:00:01,000 --> 00:00:03,000\nOpenSubtitles Content\n",
                request=httpx.Request(method, url),
            )
        return httpx.Response(404, request=httpx.Request(method, url))

    with patch("cinesub.services.opensubtitles.SESSION.request", side_effect=mock_request):
        matches = service.search(sample_video_meta, language="en")
        assert len(matches) == 1
        assert matches[0].matched_by_hash is True
        assert matches[0].file_id == 9999

        dest = tmp_path / "Inception.srt"
        service.download(matches[0], dest)
        assert dest.is_file()
        assert "OpenSubtitles Content" in dest.read_text()


def test_opensubtitles_tv_episode_search(tmp_path: Path) -> None:
    service = OpenSubtitlesService(api_key="mock_os_key")

    tv_meta = VideoMetadata(
        file_path=tmp_path / "Breaking.Bad.S01E01.720p.HDTV.mkv",
        title="Breaking Bad",
        year=2008,
        season=1,
        episode=1,
        is_episode=True,
        moviehash=None,
    )

    captured_params: list[dict] = []

    def mock_request(method, url, **kwargs):
        if "/subtitles" in url:
            captured_params.append(kwargs.get("params", {}))
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "bb_101",
                            "attributes": {
                                "language": "ro",
                                "release": "Breaking.Bad.S01E01.720p.HDTV.x264-CTU",
                                "files": [{"file_id": 8888, "file_name": "BB_S01E01.srt"}],
                            },
                        }
                    ]
                },
                request=httpx.Request(method, url),
            )
        return httpx.Response(404, request=httpx.Request(method, url))

    with patch("cinesub.services.opensubtitles.SESSION.request", side_effect=mock_request):
        matches = service.search(tv_meta, language="ro")
        assert len(matches) == 1
        assert matches[0].release_name == "Breaking.Bad.S01E01.720p.HDTV.x264-CTU"
        assert len(captured_params) == 1
        assert captured_params[0]["type"] == "episode"
        assert captured_params[0]["season_number"] == 1
        assert captured_params[0]["episode_number"] == 1
        assert captured_params[0]["query"] == "Breaking Bad"


def test_opensubtitles_gzip_download(tmp_path: Path) -> None:
    service = OpenSubtitlesService(api_key="mock_os_key")

    raw_srt = b"1\n00:00:01,000 --> 00:00:04,000\nGzip Decompressed Subtitle\n"
    gzipped_srt = gzip.compress(raw_srt)

    def mock_request(method, url, **kwargs):
        if "/download" in url:
            return httpx.Response(
                200,
                json={"link": "https://dl.opensubtitles.org/sub.gz"},
                request=httpx.Request(method, url),
            )
        if "dl.opensubtitles.org" in url:
            return httpx.Response(200, content=gzipped_srt, request=httpx.Request(method, url))
        return httpx.Response(404, request=httpx.Request(method, url))

    match = SubtitleMatch(
        id="gz_1",
        provider="opensubtitles",
        language="en",
        release_name="Test.Release",
        file_id=7777,
    )

    dest = tmp_path / "Test.srt"
    with patch("cinesub.services.opensubtitles.SESSION.request", side_effect=mock_request):
        service.download(match, dest)
        assert dest.is_file()
        assert "Gzip Decompressed Subtitle" in dest.read_text()


def test_subdl_search_and_zip_download(sample_video_meta: VideoMetadata, tmp_path: Path) -> None:
    service = SubdlService(api_key="mock_subdl_key")

    mock_subdl_payload = {
        "status": True,
        "subtitles": [
            {
                "id": "subdl_1",
                "name": "Inception",
                "release_name": "Inception.1080p.BluRay.SPARKS",
                "lang": "English",
                "download_link": "https://dl.subdl.com/subtitle/100.zip",
            }
        ],
    }

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("Inception.SPARKS.srt", "1\n00:00:01,000 --> 00:00:04,000\nSubDL Subtitle\n")
    zip_bytes = zip_buf.getvalue()

    def mock_request(method, url, params=None, headers=None, **kwargs):
        if "subtitles" in url:
            return httpx.Response(200, json=mock_subdl_payload, request=httpx.Request(method, url))
        if "download" in url or "100.zip" in url:
            return httpx.Response(200, content=zip_bytes, request=httpx.Request(method, url))
        return httpx.Response(404, request=httpx.Request(method, url))

    with patch("cinesub.services.subdl.SESSION.request", side_effect=mock_request):
        matches = service.search(sample_video_meta, language="en")
        assert len(matches) == 1
        assert matches[0].provider == "subdl"

        dest = tmp_path / "Subdl.srt"
        service.download(matches[0], dest)
        assert dest.is_file()
        assert "SubDL Subtitle" in dest.read_text()


def test_subdl_tv_series_search(tmp_path: Path) -> None:
    service = SubdlService(api_key="mock_subdl_key")

    tv_meta = VideoMetadata(
        file_path=tmp_path / "House.of.the.Dragon.S02E01.1080p.mkv",
        title="House of the Dragon",
        season=2,
        episode=1,
        is_episode=True,
    )

    captured_params: list[dict] = []

    def mock_request(method, url, **kwargs):
        if "subtitles" in url:
            captured_params.append(kwargs.get("params", {}))
            return httpx.Response(
                200,
                json={
                    "status": True,
                    "subtitles": [
                        {
                            "id": "hotd_201",
                            "name": "House of the Dragon",
                            "release_name": "House.of.the.Dragon.S02E01.1080p.MAX",
                            "lang": "Romanian",
                            "download_link": "https://dl.subdl.com/sub/hotd.zip",
                        }
                    ],
                },
                request=httpx.Request(method, url),
            )
        return httpx.Response(404, request=httpx.Request(method, url))

    with patch("cinesub.services.subdl.SESSION.request", side_effect=mock_request):
        matches = service.search(tv_meta, language="ro")
        assert len(matches) == 1
        assert len(captured_params) == 1
        assert captured_params[0]["type"] == "tv"
        assert captured_params[0]["season_number"] == 2
        assert captured_params[0]["episode_number"] == 1
        assert captured_params[0]["film_name"] == "House of the Dragon"
        assert captured_params[0]["languages"] == "RO"


def test_service_unconfigured_skipping(sample_video_meta: VideoMetadata) -> None:
    os_service = OpenSubtitlesService(api_key="")
    assert os_service.is_configured is False
    assert os_service.search(sample_video_meta, language="en") == []

    subdl_service = SubdlService(api_key="")
    assert subdl_service.is_configured is False
    assert subdl_service.search(sample_video_meta, language="en") == []

    ss_service = SubsourceService(api_key="")
    assert ss_service.is_configured is False
    assert ss_service.search(sample_video_meta, language="en") == []

    subsro_service = SubsRoService(api_key="")
    assert subsro_service.is_configured is False
    assert subsro_service.search(sample_video_meta, language="ro") == []

    betaseries_service = BetaSeriesService(api_key="")
    assert betaseries_service.is_configured is False
    assert betaseries_service.search(sample_video_meta, language="en") == []

    tmdb_service = TmdbService(access_token="", api_key="")
    assert tmdb_service.is_configured is False
    assert tmdb_service.search_movie("Inception") is None


def test_subsource_search_and_download(sample_video_meta: VideoMetadata, tmp_path: Path) -> None:
    service = SubsourceService(api_key="mock_subsource_key")
    assert service.is_configured is True

    mock_movie_search = {
        "success": True,
        "data": [
            {
                "id": 149171,
                "title": "Inception",
                "year": 2010,
                "imdb_id": "tt1375666",
            }
        ],
    }

    mock_subs_list = {
        "success": True,
        "data": [
            {
                "id": 842109,
                "movie_id": 149171,
                "release_name": "Inception.2010.1080p.BluRay.x264-SPARKS",
                "lang": "ro",
                "downloads": 120,
                "download_url": "https://api.subsource.net/api/v1/subtitles/842109/download",
            }
        ],
    }

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("Inception.ro.srt", "1\n00:00:01,000 --> 00:00:04,000\nSubSource Subtitle\n")
    zip_bytes = zip_buffer.getvalue()

    def mock_request(method, url, **kwargs):
        if "movies/search" in url:
            return httpx.Response(200, json=mock_movie_search, request=httpx.Request(method, url))
        if "subtitles" in url and "download" not in url:
            return httpx.Response(200, json=mock_subs_list, request=httpx.Request(method, url))
        if "download" in url:
            return httpx.Response(200, content=zip_bytes, request=httpx.Request(method, url))
        return httpx.Response(404, request=httpx.Request(method, url))

    with patch("cinesub.services.subsource.SESSION.request", side_effect=mock_request):
        matches = service.search(sample_video_meta, language="ro")
        assert len(matches) == 1
        assert matches[0].provider == "subsource"
        assert matches[0].id == "842109"

        target_srt = tmp_path / "Inception.ro.srt"
        service.download(matches[0], target_srt)
        assert target_srt.is_file()
        assert "SubSource Subtitle" in target_srt.read_text(encoding="utf-8")


def test_tmdb_service_search_and_enrichment(sample_video_meta: VideoMetadata) -> None:
    service = TmdbService(access_token="mock_tmdb_token", account_id="12345")
    assert service.is_configured is True

    mock_movie_res = {
        "page": 1,
        "results": [
            {
                "id": 27205,
                "title": "Inception",
                "release_date": "2010-07-16",
            }
        ],
    }

    mock_ext_ids = {
        "id": 27205,
        "imdb_id": "tt1375666",
    }

    def mock_request(method, url, **kwargs):
        if "search/movie" in url:
            return httpx.Response(200, json=mock_movie_res, request=httpx.Request(method, url))
        if "movie/27205/external_ids" in url:
            return httpx.Response(200, json=mock_ext_ids, request=httpx.Request(method, url))
        if "account/12345/favorite" in url or "account/12345/watchlist" in url:
            return httpx.Response(
                200, json={"status_code": 1, "success": True}, request=httpx.Request(method, url)
            )
        return httpx.Response(404, request=httpx.Request(method, url))

    with patch("cinesub.services.tmdb.SESSION.request", side_effect=mock_request):
        # 1. Search movie
        movie = service.search_movie("Inception", 2010)
        assert movie is not None
        assert movie["id"] == 27205

        # 2. Enrich metadata
        enriched = service.enrich_video_metadata(sample_video_meta)
        assert enriched.tmdb_id == 27205
        assert enriched.imdb_id == "tt1375666"

        # 3. Add to favorite and watchlist
        fav_ok = service.add_to_favorite(27205, favorite=True)
        assert fav_ok is True

        watch_ok = service.add_to_watchlist(27205, watchlist=True)
        assert watch_ok is True


def test_subsro_search_and_download(sample_video_meta: VideoMetadata, tmp_path: Path) -> None:
    service = SubsRoService(api_key="mock_subsro_key")
    assert service.is_configured is True

    video_meta = VideoMetadata(
        file_path=sample_video_meta.file_path,
        title=sample_video_meta.title,
        year=sample_video_meta.year,
        release_group=sample_video_meta.release_group,
        screen_size=sample_video_meta.screen_size,
        source=sample_video_meta.source,
        imdb_id="tt1375666",
    )

    mock_search_payload = {
        "status": "success",
        "items": [
            {
                "id": 55432,
                "language": "ro",
                "title": "Inception (2010)",
                "release": "Inception.2010.1080p.BluRay.x264-SPARKS",
                "translator": "Retail SubsRo",
            }
        ],
    }

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr(
            "Inception.ro.srt",
            "1\n00:00:01,000 --> 00:00:04,000\nSubs.ro Subtitle\n",
        )
    zip_bytes = zip_buffer.getvalue()

    def mock_request(method, url, **kwargs):
        if "search/imdbid" in url:
            return httpx.Response(200, json=mock_search_payload, request=httpx.Request(method, url))
        if "download" in url:
            return httpx.Response(200, content=zip_bytes, request=httpx.Request(method, url))
        return httpx.Response(404, request=httpx.Request(method, url))

    with patch("cinesub.services.subsro.SESSION.request", side_effect=mock_request):
        matches = service.search(video_meta, language="ro")
        assert len(matches) == 1
        assert matches[0].provider == "subsro"
        assert matches[0].id == "55432"
        # Check retail bonus was applied
        assert matches[0].score > 50.0

        target_srt = tmp_path / "Inception.ro.srt"
        service.download(matches[0], target_srt)
        assert target_srt.is_file()
        assert "Subs.ro Subtitle" in target_srt.read_text(encoding="utf-8")


def test_subsro_decompression_bomb_protection(tmp_path: Path) -> None:
    service = SubsRoService(api_key="mock_subsro_key")
    huge_zip_buffer = io.BytesIO()
    with zipfile.ZipFile(huge_zip_buffer, "w") as zf:
        zf.writestr("huge.srt", b"A" * (11 * 1024 * 1024))
    huge_bytes = huge_zip_buffer.getvalue()

    sub_match = SubtitleMatch(
        id="999",
        provider="subsro",
        language="ro",
        release_name="Huge.Release",
        matched_by_hash=False,
        download_url="https://subs.ro/api/v1.0/subtitle/999/download",
    )

    with patch(
        "cinesub.services.subsro.SESSION.request",
        return_value=httpx.Response(
            200, content=huge_bytes, request=httpx.Request("GET", "https://subs.ro")
        ),
    ):
        with pytest.raises(ValueError, match="exceeds safe uncompressed threshold"):
            service.download(sub_match, tmp_path / "Huge.srt")


def test_subdl_decompression_bomb_protection(tmp_path: Path) -> None:
    service = SubdlService(api_key="mock_subdl_key")
    huge_zip_buffer = io.BytesIO()
    with zipfile.ZipFile(huge_zip_buffer, "w") as zf:
        zf.writestr("huge_subdl.srt", b"S" * (11 * 1024 * 1024))
    huge_bytes = huge_zip_buffer.getvalue()

    sub_match = SubtitleMatch(
        id="subdl_999",
        provider="subdl",
        language="en",
        release_name="Huge.Release",
        matched_by_hash=False,
        download_url="https://dl.subdl.com/subtitle.zip",
    )

    with patch(
        "cinesub.services.subdl.SESSION.request",
        return_value=httpx.Response(
            200, content=huge_bytes, request=httpx.Request("GET", "https://dl.subdl.com")
        ),
    ):
        with pytest.raises(ValueError, match="exceeds safe uncompressed threshold"):
            service.download(sub_match, tmp_path / "HugeSubdl.srt")


def test_subsource_decompression_bomb_protection(tmp_path: Path) -> None:
    service = SubsourceService(api_key="mock_subsource_key")
    huge_zip_buffer = io.BytesIO()
    with zipfile.ZipFile(huge_zip_buffer, "w") as zf:
        zf.writestr("huge_subsource.srt", b"X" * (11 * 1024 * 1024))
    huge_bytes = huge_zip_buffer.getvalue()

    sub_match = SubtitleMatch(
        id="subsource_999",
        provider="subsource",
        language="en",
        release_name="Huge.Release",
        matched_by_hash=False,
        download_url="https://api.subsource.net/api/v1/subtitles/999/download",
    )

    with patch(
        "cinesub.services.subsource.SESSION.request",
        return_value=httpx.Response(
            200, content=huge_bytes, request=httpx.Request("GET", "https://api.subsource.net")
        ),
    ):
        with pytest.raises(ValueError, match="exceeds safe uncompressed threshold"):
            service.download(sub_match, tmp_path / "HugeSubsource.srt")


def test_betaseries_movie_search_and_download(
    sample_video_meta: VideoMetadata, tmp_path: Path
) -> None:
    service = BetaSeriesService(api_key="mock_bs_key")
    assert service.is_configured is True

    mock_search_payload = {
        "subtitles": [
            {
                "id": 1234,
                "file": "Inception.2010.1080p.BluRay.x264-SPARKS.srt",
                "language": "en",
                "url": "https://api.betaseries.com/subtitles/1234/download.zip",
            }
        ]
    }

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr(
            "Inception.srt",
            "1\n00:00:01,000 --> 00:00:04,000\nBetaSeries Movie Subtitle\n",
        )
    zip_bytes = zip_buffer.getvalue()

    def mock_request(method, url, **kwargs):
        if "subtitles/last" in url:
            return httpx.Response(200, json=mock_search_payload, request=httpx.Request(method, url))
        if "download.zip" in url:
            return httpx.Response(200, content=zip_bytes, request=httpx.Request(method, url))
        return httpx.Response(404, request=httpx.Request(method, url))

    with patch("cinesub.services.betaseries.SESSION.request", side_effect=mock_request):
        matches = service.search(sample_video_meta, language="en")
        assert len(matches) == 1
        assert matches[0].provider == "betaseries"
        assert matches[0].id == "1234"

        target_srt = tmp_path / "Inception.srt"
        service.download(matches[0], target_srt)
        assert target_srt.is_file()
        assert "BetaSeries Movie Subtitle" in target_srt.read_text(encoding="utf-8")


def test_betaseries_episode_search(tmp_path: Path) -> None:
    service = BetaSeriesService(api_key="mock_bs_key")

    tv_meta = VideoMetadata(
        file_path=tmp_path / "Dark.S01E01.1080p.mkv",
        title="Dark",
        season=1,
        episode=1,
        is_episode=True,
        imdb_id="tt5753856",
    )

    mock_episode_payload = {
        "subtitles": [
            {
                "id": 5678,
                "file": "Dark.S01E01.1080p.NF.WEBRip.srt",
                "language": "en",
                "season": 1,
                "episode": 1,
                "url": "https://api.betaseries.com/subtitles/5678/download.zip",
            },
            {
                "id": 5679,
                "file": "Dark.S01E02.1080p.NF.WEBRip.srt",
                "language": "en",
                "season": 1,
                "episode": 2,
                "url": "https://api.betaseries.com/subtitles/5679/download.zip",
            },
        ]
    }

    with patch(
        "cinesub.services.betaseries.SESSION.request",
        return_value=httpx.Response(
            200,
            json=mock_episode_payload,
            request=httpx.Request("GET", "https://api.betaseries.com"),
        ),
    ):
        matches = service.search(tv_meta, language="en")
        assert len(matches) == 1
        assert matches[0].id == "5678"


def test_betaseries_decompression_bomb_protection(tmp_path: Path) -> None:
    service = BetaSeriesService(api_key="mock_bs_key")
    huge_zip_buffer = io.BytesIO()
    with zipfile.ZipFile(huge_zip_buffer, "w") as zf:
        zf.writestr("huge_series.srt", b"B" * (12 * 1024 * 1024))
    huge_bytes = huge_zip_buffer.getvalue()

    sub_match = SubtitleMatch(
        id="888",
        provider="betaseries",
        language="en",
        release_name="Huge.Series",
        matched_by_hash=False,
        download_url="https://api.betaseries.com/subtitles/888/download.zip",
    )

    with patch(
        "cinesub.services.betaseries.SESSION.request",
        return_value=httpx.Response(
            200, content=huge_bytes, request=httpx.Request("GET", "https://api.betaseries.com")
        ),
    ):
        with pytest.raises(ValueError, match="exceeds safe uncompressed threshold"):
            service.download(sub_match, tmp_path / "Huge.srt")


def test_betaseries_error_4001_handling(sample_video_meta: VideoMetadata) -> None:
    service = BetaSeriesService(api_key="mock_bs_key")
    error_4001_payload = {"errors": [{"code": 4001, "text": "La série n'existe pas"}]}

    with patch(
        "cinesub.services.betaseries.SESSION.request",
        return_value=httpx.Response(
            400,
            json=error_4001_payload,
            request=httpx.Request("GET", "https://api.betaseries.com"),
        ),
    ):
        results = service.search(sample_video_meta, language="en")
        assert results == []


def test_opensubtitles_download_quota_exhausted(tmp_path: Path) -> None:
    service = OpenSubtitlesService(api_key="mock_os_key")
    quota_406_payload = {
        "message": "Download limit reached for today",
        "remaining": 0,
    }

    sub_match = SubtitleMatch(
        id="9999",
        provider="opensubtitles",
        language="en",
        release_name="Quota.Test",
        matched_by_hash=False,
        file_id=9999,
    )

    with patch(
        "cinesub.services.opensubtitles.SESSION.request",
        return_value=httpx.Response(
            406,
            json=quota_406_payload,
            request=httpx.Request("POST", "https://api.opensubtitles.com/api/v1/download"),
        ),
    ):
        with pytest.raises(RuntimeError, match="OpenSubtitles download quota exhausted"):
            service.download(sub_match, tmp_path / "QuotaTest.srt")


def test_rate_limiter_http_429_backoff_and_retry(sample_video_file: Path) -> None:
    service = OpenSubtitlesService(api_key="mock_os_key")
    video_meta = VideoMetadata(
        file_path=sample_video_file,
        title="Inception",
        year=2010,
        moviehash=None,
    )

    call_count = 0

    def mock_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            headers = {"retry-after": "1"}
            return httpx.Response(429, headers=headers, request=httpx.Request(method, url))
        return httpx.Response(200, json={"data": []}, request=httpx.Request(method, url))

    with (
        patch("cinesub.services.opensubtitles.SESSION.request", side_effect=mock_request),
        patch("cinesub.core.ratelimit.OPENSUBTITLES_LIMITER.trigger_cooldown") as mock_cooldown,
    ):
        matches = service.search(video_meta, language="en")
        assert matches == []
        assert call_count == 2
        mock_cooldown.assert_called_once_with(1.0)


def test_gestdown_search_and_download(tmp_path: Path) -> None:
    service = GestdownService()
    tv_meta = VideoMetadata(
        file_path=tmp_path / "Breaking.Bad.S01E01.mkv",
        title="Breaking Bad",
        season=1,
        episode=1,
        is_episode=True,
    )

    catalog_payload = {
        "shows": [
            {
                "id": "show-uuid-1",
                "name": "Breaking Bad",
                "seasons": [1, 2, 3],
            }
        ]
    }
    subtitles_payload = {
        "matchingSubtitles": [
            {
                "subtitleId": "sub-uuid-10",
                "version": "720p.HDTV",
                "hearingImpaired": False,
                "downloadCount": 150,
                "downloadUri": "/subtitles/download/sub-uuid-10",
            }
        ]
    }

    def mock_gestdown_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        if "/shows/search" in url:
            return httpx.Response(200, json=catalog_payload, request=httpx.Request(method, url))
        if "/subtitles/get" in url:
            return httpx.Response(200, json=subtitles_payload, request=httpx.Request(method, url))
        if "/subtitles/download" in url:
            return httpx.Response(
                200,
                content=b"1\n00:00:01,000 --> 00:00:03,000\nGestdown Test Subtitle\n",
                request=httpx.Request(method, url),
            )
        return httpx.Response(404, request=httpx.Request(method, url))

    with patch("cinesub.services.gestdown.SESSION.request", side_effect=mock_gestdown_request):
        matches = service.search(tv_meta, language="en")
        assert len(matches) == 1
        assert matches[0].id == "sub-uuid-10"
        assert matches[0].provider == "gestdown"
        assert "720p.HDTV" in matches[0].release_name

        dest_srt = tmp_path / "bb.srt"
        service.download(matches[0], dest_srt)
        assert dest_srt.is_file()
        assert "Gestdown Test Subtitle" in dest_srt.read_text(encoding="utf-8")


def test_gestdown_movie_skipped(sample_video_meta: VideoMetadata) -> None:
    service = GestdownService()
    # sample_video_meta is not an episode
    matches = service.search(sample_video_meta, language="en")
    assert matches == []


def test_bsplayer_search_and_download(sample_video_meta: VideoMetadata, tmp_path: Path) -> None:
    service = BsplayerService(endpoint_url="http://s1.api.bsplayer-subtitles.com/v1.php")

    login_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">'
        "<SOAP-ENV:Body><return><result>200</result><status>OK</status><data>sess_123</data></return></SOAP-ENV:Body>"
        "</SOAP-ENV:Envelope>"
    )
    search_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">'
        "<SOAP-ENV:Body><return>"
        "<result><result>200</result><status>OK</status></result>"
        "<data>"
        "<item>"
        "<subID>9911</subID>"
        "<subName>Inception.2010.1080p.BluRay.x264.srt</subName>"
        "<subLang>eng</subLang>"
        "<subFormat>srt</subFormat>"
        "<subRating>9.20</subRating>"
        "<subDownloadLink>http://s1.api.bsplayer-subtitles.com/download.php?id=9911</subDownloadLink>"
        "</item>"
        "</data>"
        "</return></SOAP-ENV:Body>"
        "</SOAP-ENV:Envelope>"
    )
    logout_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">'
        "<SOAP-ENV:Body><return><result>200</result></return></SOAP-ENV:Body>"
        "</SOAP-ENV:Envelope>"
    )

    def mock_post(url: str, **kwargs: Any) -> httpx.Response:
        content_str = kwargs.get("content", b"").decode("utf-8")
        if "logIn" in content_str:
            return httpx.Response(200, text=login_xml, request=httpx.Request("POST", url))
        if "searchSubtitles" in content_str:
            return httpx.Response(200, text=search_xml, request=httpx.Request("POST", url))
        if "logOut" in content_str:
            return httpx.Response(200, text=logout_xml, request=httpx.Request("POST", url))
        return httpx.Response(404, request=httpx.Request("POST", url))

    raw_srt_bytes = b"1\n00:00:01,000 --> 00:00:04,000\nBSPlayer Subtitle Content\n"
    gzipped_bytes = gzip.compress(raw_srt_bytes)

    def mock_get(url: str, **kwargs: Any) -> httpx.Response:
        if "download.php" in url:
            return httpx.Response(200, content=gzipped_bytes, request=httpx.Request("GET", url))
        return httpx.Response(404, request=httpx.Request("GET", url))

    with (
        patch("cinesub.services.bsplayer.SESSION.post", side_effect=mock_post),
        patch("cinesub.services.bsplayer.SESSION.get", side_effect=mock_get),
    ):
        matches = service.search(sample_video_meta, language="en")
        assert len(matches) == 1
        assert matches[0].id == "9911"
        assert matches[0].provider == "bsplayer"
        assert matches[0].matched_by_hash is True

        dest_srt = tmp_path / "Inception_bs.srt"
        service.download(matches[0], dest_srt)
        assert dest_srt.is_file()
        assert "BSPlayer Subtitle Content" in dest_srt.read_text(encoding="utf-8")


def test_bsplayer_skipped_without_hash_or_imdb(tmp_path: Path) -> None:
    service = BsplayerService()
    meta_without_hash = VideoMetadata(
        file_path=tmp_path / "Unknown.Movie.mp4",
        title="Unknown Movie",
        moviehash=None,
        imdb_id=None,
    )
    matches = service.search(meta_without_hash, language="en")
    assert matches == []


def test_animetosho_search_and_download(tmp_path: Path) -> None:
    service = AnimeToshoService()
    anime_meta = VideoMetadata(
        file_path=tmp_path / "[SubsPlease] Frieren - 01 (1080p).mkv",
        title="Frieren",
        episode=1,
        is_episode=True,
    )

    feed_payload = [
        {
            "id": 1234,
            "title": "[SubsPlease] Sousou no Frieren - 01 (1080p) [7D35515E].mkv",
            "status": "complete",
        }
    ]
    detail_payload = {
        "files": [
            {
                "filename": "[SubsPlease] Sousou no Frieren - 01 (1080p).mkv",
                "attachments": [
                    {
                        "id": 4321,
                        "type": "subtitle",
                        "info": {
                            "codec": "ASS",
                            "lang": "eng",
                            "name": "English subs",
                            "forced": 0,
                        },
                        "size": 100,
                    }
                ],
            }
        ]
    }

    ass_content = (
        b"[Script Info]\n"
        b"Title: Frieren 01\n"
        b"[Events]\n"
        b"Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        b"Dialogue: 0,0:00:01.00,0:00:04.00,Default,,0,0,0,,{\\i1}AnimeTosho Subtitle{\\i0}\n"
    )
    xz_compressed_bytes = lzma.compress(ass_content)

    def mock_animetosho_get(url: str, **kwargs: Any) -> httpx.Response:
        params = kwargs.get("params", {})
        if "feed.animetosho.org" in url:
            if params.get("show") == "torrent":
                return httpx.Response(200, json=detail_payload, request=httpx.Request("GET", url))
            return httpx.Response(200, json=feed_payload, request=httpx.Request("GET", url))
        if "storage.animetosho.org" in url:
            return httpx.Response(
                200,
                content=xz_compressed_bytes,
                request=httpx.Request("GET", url),
            )
        return httpx.Response(404, request=httpx.Request("GET", url))

    with patch("cinesub.services.animetosho.SESSION.get", side_effect=mock_animetosho_get):
        matches = service.search(anime_meta, language="en")
        assert len(matches) == 1
        assert matches[0].id == "1234:4321"
        assert matches[0].provider == "animetosho"

        dest_srt = tmp_path / "frieren.srt"
        service.download(matches[0], dest_srt)
        assert dest_srt.is_file()
        content = dest_srt.read_text(encoding="utf-8")
        assert "AnimeTosho Subtitle" in content


def test_assrt_search_and_download(sample_video_meta: VideoMetadata, tmp_path: Path) -> None:
    service = AssrtService(api_token="valid_32_char_token_123456789012")
    assert service.is_configured is True

    search_payload = {
        "status": 0,
        "sub": {
            "subs": [
                {
                    "id": 8888,
                    "native_name": "Inception (2010)",
                    "videoname": "Inception.2010.1080p.BluRay.x264",
                    "filename": "Inception.chs.srt",
                    "url": "https://api.assrt.net/download/8888",
                    "vote_score": 4.5,
                    "down_count": 200,
                    "lang": {
                        "langlist": {"langeng": True, "langchs": True},
                    },
                }
            ]
        },
    }

    raw_srt_bytes = b"1\n00:00:01,000 --> 00:00:03,000\nAssrt Chinese/English Subtitle\n"

    def mock_assrt_get(url: str, **kwargs: Any) -> httpx.Response:
        if "/sub/search" in url:
            return httpx.Response(200, json=search_payload, request=httpx.Request("GET", url))
        if "download/8888" in url:
            return httpx.Response(200, content=raw_srt_bytes, request=httpx.Request("GET", url))
        return httpx.Response(404, request=httpx.Request("GET", url))

    with patch("cinesub.services.assrt.SESSION.get", side_effect=mock_assrt_get):
        matches = service.search(sample_video_meta, language="en")
        assert len(matches) == 1
        assert matches[0].id == "8888"
        assert matches[0].provider == "assrt"

        dest_srt = tmp_path / "assrt_sub.srt"
        service.download(matches[0], dest_srt)
        assert dest_srt.is_file()
        assert "Assrt Chinese/English Subtitle" in dest_srt.read_text(encoding="utf-8")


def test_assrt_unconfigured_behavior(sample_video_meta: VideoMetadata) -> None:
    service = AssrtService(api_token=None)
    with patch.dict("os.environ", {}, clear=True):
        assert service.is_configured is False
        assert service.search(sample_video_meta, language="en") == []


def test_get_active_services_all_providers() -> None:
    active_gestdown = get_active_services("gestdown")
    assert "gestdown" in active_gestdown
    assert isinstance(active_gestdown["gestdown"], GestdownService)

    active_bsplayer = get_active_services("bsplayer")
    assert "bsplayer" in active_bsplayer
    assert isinstance(active_bsplayer["bsplayer"], BsplayerService)

    active_animetosho = get_active_services("animetosho")
    assert "animetosho" in active_animetosho
    assert isinstance(active_animetosho["animetosho"], AnimeToshoService)

    with patch.dict("os.environ", {"ASSRT_API_TOKEN": "mock_token_value"}):
        active_assrt = get_active_services("assrt")
        assert "assrt" in active_assrt
        assert isinstance(active_assrt["assrt"], AssrtService)
