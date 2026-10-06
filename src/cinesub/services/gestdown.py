from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import orjson

from cinesub.core.constants import GESTDOWN_API_URL, USER_AGENT
from cinesub.core.http import SESSION
from cinesub.core.logger import LOG
from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.core.ratelimit import GESTDOWN_LIMITER
from cinesub.core.utils import (
    normalize_language,
    normalize_language_alpha3,
    save_subtitle_to_disk,
    score_subtitle_candidate,
)


class GestdownService:
    """Service client for Gestdown public REST API."""

    def __init__(self, base_url: str = GESTDOWN_API_URL) -> None:
        self.base_url = base_url.rstrip("/")

    @property
    def is_configured(self) -> bool:
        """Gestdown is a free, public service requiring no API key."""
        return True

    @property
    def is_available(self) -> bool:
        """Check if Gestdown is not currently circuit-broken due to connection outage."""
        return GESTDOWN_LIMITER.is_available

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

    def _send_request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> httpx.Response:
        """Send HTTP request with token-bucket rate limiting and 429 backoff."""
        for attempt in range(max_retries):
            GESTDOWN_LIMITER.acquire()
            try:
                response = SESSION.request(
                    method=method,
                    url=url,
                    params=params,
                    headers=self._headers(),
                )
                if response.status_code == 429:
                    retry_after = response.headers.get("retry-after")
                    wait_sec = float(retry_after) if retry_after and retry_after.isdigit() else 5.0
                    LOG.warning(f"Gestdown 429 rate limit received. Pausing for {wait_sec:.1f}s.")
                    GESTDOWN_LIMITER.trigger_cooldown(wait_sec)
                    continue

                return response
            except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
                GESTDOWN_LIMITER.mark_unreachable(60.0)
                LOG.warning(f"Gestdown server unreachable ({exc}). Skipping for 60s.")
                raise
            except httpx.RequestError as exc:
                if attempt == max_retries - 1:
                    raise
                backoff_delay = 0.5 * (2**attempt)
                LOG.debug(f"Gestdown network error ({exc}), retrying in {backoff_delay:.1f}s...")
                time.sleep(backoff_delay)

        raise RuntimeError("Gestdown request failed after retries.")

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

        # Find best matching show by TMDb ID or title similarity
        selected_show: dict[str, Any] | None = None
        for candidate_show in shows_list:
            if video_meta.tmdb_id and candidate_show.get("tmdbId") == video_meta.tmdb_id:
                selected_show = candidate_show
                break
            candidate_name = str(candidate_show.get("name", "")).lower()
            if candidate_name == clean_show_title.lower():
                selected_show = candidate_show
                break

        if not selected_show:
            selected_show = shows_list[0]

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

    def download(self, subtitle: SubtitleMatch, destination: Path) -> Path:
        """Download subtitle from Gestdown, normalize to UTF-8 SRT, and write atomically."""
        if not subtitle.download_url:
            raise ValueError("Gestdown match missing download URL.")

        response = self._send_request("GET", subtitle.download_url)
        if response.status_code != 200:
            status = response.status_code
            snippet = response.text[:100]
            raise RuntimeError(f"Gestdown download failed with status {status}: {snippet}")

        return save_subtitle_to_disk(response.content, destination)
