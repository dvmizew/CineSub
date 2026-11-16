from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
import orjson

from cinesub.core.constants import (
    SUBDL_API_URL,
    SUBDL_DL_URL,
)
from cinesub.core.logger import LOG
from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.core.ratelimit import SUBDL_LIMITER
from cinesub.core.utils import (
    save_subtitle_to_disk,
    score_subtitle_candidate,
)
from cinesub.services.base import BaseHttpService


class SubdlService(BaseHttpService):
    """Service client for SubDL API."""

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(service_name="SubDL", limiter=SUBDL_LIMITER)
        self.api_key = api_key or os.getenv("SUBDL_API_KEY", "").strip() or None

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def search(self, video_meta: VideoMetadata, language: str) -> list[SubtitleMatch]:
        """Search SubDL for matching subtitles."""
        if not self.is_configured or not self.is_available:
            return []

        url = f"{SUBDL_API_URL}/subtitles"
        params: dict[str, Any] = {
            "api_key": self.api_key,
            "film_name": video_meta.title,
            "file_name": video_meta.file_path.name,
            "languages": language.upper(),
            "releases": 1,
            "subs_per_page": 30,
        }

        if video_meta.imdb_id:
            params["imdb_id"] = video_meta.imdb_id
        if video_meta.tmdb_id:
            params["tmdb_id"] = video_meta.tmdb_id

        if video_meta.is_episode:
            params["type"] = "tv"
            if video_meta.season is not None:
                params["season_number"] = video_meta.season
            if video_meta.episode is not None:
                params["episode_number"] = video_meta.episode
        else:
            params["type"] = "movie"
            if video_meta.year:
                params["year"] = video_meta.year

        try:
            response = self._send_request("GET", url, params=params)
            if response.status_code != 200:
                return []

            response_payload = orjson.loads(response.content)
            if not response_payload.get("status"):
                return []

            matches: list[SubtitleMatch] = []
            for idx, candidate_entry in enumerate(response_payload.get("subtitles", [])):
                release_name = (
                    candidate_entry.get("release_name")
                    or candidate_entry.get("name")
                    or f"{video_meta.title} Subtitle {idx + 1}"
                )
                raw_url = candidate_entry.get("download_link") or candidate_entry.get("url") or ""
                if raw_url.startswith("http"):
                    dl_url = raw_url
                else:
                    dl_url = urljoin(SUBDL_DL_URL, raw_url)

                score = score_subtitle_candidate(
                    video_meta=video_meta,
                    release_name=release_name,
                    matched_by_hash=False,
                )

                sub_id = str(
                    candidate_entry.get("id") or candidate_entry.get("file_n_id") or f"subdl_{idx}"
                )
                matches.append(
                    SubtitleMatch(
                        id=sub_id,
                        provider="subdl",
                        language=(candidate_entry.get("lang") or language).lower(),
                        release_name=release_name,
                        matched_by_hash=False,
                        download_url=dl_url,
                        file_id=sub_id,
                        hearing_impaired=bool(candidate_entry.get("hi", False)),
                        fps=candidate_entry.get("fps") or candidate_entry.get("framerate"),
                        score=score,
                    )
                )

            matches.sort(key=lambda match: match.score, reverse=True)
            return matches

        except (httpx.HTTPError, orjson.JSONDecodeError, RuntimeError) as exc:
            LOG.debug(f"SubDL search error: {exc}")
            return []

    def download(self, subtitle: SubtitleMatch, destination: Path) -> Path:
        """Download subtitle from SubDL, unpack if needed, and normalize encoding to UTF-8."""
        if not subtitle.download_url:
            raise ValueError("SubDL match missing download URL.")

        download_params = {"api_key": self.api_key} if self.api_key else None
        response = self._send_request("GET", subtitle.download_url, params=download_params)
        response.raise_for_status()

        return save_subtitle_to_disk(response.content, destination)
