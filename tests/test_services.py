"""Tests for OpenSubtitles and SubDL service integrations."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx

from cinesub.core.models import VideoMetadata
from cinesub.services.opensubtitles import OpenSubtitlesService
from cinesub.services.subdl import SubdlService


def test_opensubtitles_search_and_download(
    sample_video_meta: VideoMetadata, tmp_path: Path
) -> None:
    """Test OpenSubtitles search and download workflow."""
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


def test_subdl_search_and_zip_download(sample_video_meta: VideoMetadata, tmp_path: Path) -> None:
    """Test SubDL search and in-memory ZIP extraction."""
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

    def mock_request(method, url, params=None, headers=None):
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
