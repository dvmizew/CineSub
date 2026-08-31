from __future__ import annotations

import gzip
import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx

from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.services.opensubtitles import OpenSubtitlesService
from cinesub.services.subdl import SubdlService


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
