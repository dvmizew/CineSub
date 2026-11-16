from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
import orjson
from rapidfuzz import fuzz

from cinesub.core.constants import GESTDOWN_API_URL
from cinesub.core.logger import LOG
from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.core.ratelimit import GESTDOWN_LIMITER
from cinesub.core.utils import (
    normalize_language,
    normalize_language_alpha3,
    score_subtitle_candidate,
)
from cinesub.services.base import BaseHttpService


class GestdownService(BaseHttpService):
    """Service client for Gestdown public REST API."""

    def __init__(self, base_url: str = GESTDOWN_API_URL) -> None:
        super().__init__(service_name="Gestdown", limiter=GESTDOWN_LIMITER)
        self.base_url = base_url.rstrip("/")

    def _to_alpha3(self, language_code: str) -> str:
        """Convert 2-letter or arbitrary language code to ISO 639-2/3 alpha3."""
        return normalize_language_alpha3(language_code)

    def search(self, video_meta: VideoMetadata, language: str) -> list[SubtitleMatch]:
        """Search Gestdown for episode subtitles."""
        if not self.is_available:
            return []
        if not video_meta.is_episode and video_meta.season is None:
            return []
        if video_meta.season is None or video_meta.episode is None:
            return []

        target_lang = normalize_language(language)
        lang_alpha3 = self._to_alpha3(target_lang)

        # 1. Search show catalog
        clean_show_title = video_meta.title.strip()
        search_url = f"{self.base_url}/shows/search/{quote(clean_show_title)}"
        try:
            search_response = self._send_request("GET", search_url)
            if search_response.status_code != 200:
                return []
            catalog_payload = orjson.loads(search_response.content)
        except (httpx.HTTPError, orjson.JSONDecodeError) as exc:
            LOG.debug(f"Gestdown show search failed: {exc}")
            return []

        shows_list: list[dict[str, Any]] = catalog_payload.get("shows", [])
        if not shows_list:
            return []

        # Find best matching show by TMDb ID, exact title, or high fuzzy similarity
        selected_show: dict[str, Any] | None = None
        best_ratio = 0.0

        for candidate_show in shows_list:
            if video_meta.tmdb_id and candidate_show.get("tmdbId") == video_meta.tmdb_id:
                selected_show = candidate_show
                break
            candidate_name = str(candidate_show.get("name", "")).lower()
            if candidate_name == clean_show_title.lower():
                selected_show = candidate_show
                break

            ratio = fuzz.ratio(candidate_name, clean_show_title.lower())
            if ratio > best_ratio:
                best_ratio = ratio
                if ratio >= 70.0:
                    selected_show = candidate_show

        if not selected_show:
            LOG.debug(f"Gestdown: No show closely matching '{clean_show_title}'.")
            return []

        show_seasons: list[int] = selected_show.get("seasons", [])
        if show_seasons and video_meta.season not in show_seasons:
            show_name = selected_show.get("name")
            LOG.debug(f"Gestdown: Season {video_meta.season} not found in show {show_name}.")
            return []

        show_id = selected_show.get("id")
        if not show_id:
            return []

        # 2. Retrieve subtitles for target episode and language
        season_num = video_meta.season
        ep_num = video_meta.episode
        subtitles_url = (
            f"{self.base_url}/subtitles/get/{show_id}/{season_num}/{ep_num}/{lang_alpha3}"
        )
        try:
            subtitles_response = self._send_request("GET", subtitles_url)
            if subtitles_response.status_code != 200:
                return []
            subtitles_payload = orjson.loads(subtitles_response.content)
        except (httpx.HTTPError, orjson.JSONDecodeError) as exc:
            LOG.debug(f"Gestdown subtitle retrieval failed: {exc}")
            return []

        raw_candidates: list[dict[str, Any]] = subtitles_payload.get("matchingSubtitles", [])
        if not raw_candidates:
            return []

        matches: list[SubtitleMatch] = []
        for raw_candidate in raw_candidates:
            subtitle_id = str(raw_candidate.get("subtitleId", ""))
            download_uri = str(raw_candidate.get("downloadUri", ""))
            if not download_uri:
                continue

            version_str = str(raw_candidate.get("version") or "").strip()
            hearing_impaired = bool(raw_candidate.get("hearingImpaired", False))
            download_count = int(raw_candidate.get("downloadCount", 0))

            season_prefix = f"S{video_meta.season:02d}E{video_meta.episode:02d}"
            if version_str:
                release_name = f"{video_meta.title}.{season_prefix}.{version_str}"
            else:
                release_name = f"{video_meta.title}.{season_prefix}"

            candidate_score = score_subtitle_candidate(
                video_meta=video_meta,
                release_name=release_name,
                downloads=download_count,
            )

            if download_uri.startswith("/"):
                full_download_url = f"{self.base_url}{download_uri}"
            else:
                full_download_url = download_uri

            matches.append(
                SubtitleMatch(
                    id=subtitle_id,
                    provider="gestdown",
                    language=target_lang,
                    release_name=release_name,
                    download_url=full_download_url,
                    hearing_impaired=hearing_impaired,
                    download_count=download_count,
                    score=candidate_score,
                )
            )

        matches.sort(key=lambda match_item: match_item.score, reverse=True)
        return matches
