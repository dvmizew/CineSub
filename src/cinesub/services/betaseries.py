from __future__ import annotations

import os
from typing import Any

import httpx
import orjson

from cinesub.core.constants import BETASERIES_API_URL, USER_AGENT
from cinesub.core.logger import LOG
from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.core.ratelimit import BETASERIES_LIMITER
from cinesub.core.utils import (
    normalize_language,
    score_subtitle_candidate,
)
from cinesub.services.base import BaseHttpService
from cinesub.services.tmdb import TmdbService


class BetaSeriesService(BaseHttpService):
    """Service client for BetaSeries REST API v3.0."""

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(service_name="BetaSeries", limiter=BETASERIES_LIMITER)
        self.api_key = (
            api_key
            or os.getenv("BETASERIES_API_KEY", "").strip()
            or os.getenv("BETA_SERIES_API_KEY", "").strip()
            or None
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "X-BetaSeries-Key": self.api_key or "",
            "X-BetaSeries-Version": "3.0",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

    def search(self, video_meta: VideoMetadata, language: str) -> list[SubtitleMatch]:
        """Search BetaSeries for subtitles by IMDb ID or title query."""
        if not self.is_configured or not self.is_available:
            return []

        # Enrich IMDb ID via TMDb if not already present
        if not video_meta.imdb_id:
            tmdb_svc = TmdbService()
            if tmdb_svc.is_configured:
                video_meta = tmdb_svc.enrich_video_metadata(video_meta)

        target_lang = normalize_language(language)

        try:
            if video_meta.is_episode and video_meta.season and video_meta.episode:
                subs_list = self._search_episode(video_meta, target_lang)
            else:
                subs_list = self._search_movie(target_lang)

            results: list[SubtitleMatch] = []
            for sub_entry in subs_list:
                sub_id = str(sub_entry.get("id", ""))
                dl_url = str(sub_entry.get("url") or "")
                if not sub_id or not dl_url:
                    continue

                file_title = str(
                    sub_entry.get("file")
                    or sub_entry.get("title")
                    or f"{video_meta.title} BetaSeries {sub_id}"
                )
                entry_lang = normalize_language(str(sub_entry.get("language") or language))

                score = score_subtitle_candidate(
                    video_meta=video_meta,
                    release_name=file_title,
                    matched_by_hash=False,
                )

                results.append(
                    SubtitleMatch(
                        id=sub_id,
                        provider="betaseries",
                        language=entry_lang,
                        release_name=file_title,
                        matched_by_hash=False,
                        download_url=dl_url,
                        file_id=sub_id,
                        score=score,
                    )
                )

            results.sort(key=lambda m: m.score, reverse=True)
            return results

        except (httpx.HTTPError, orjson.JSONDecodeError, RuntimeError) as exc:
            LOG.debug(f"BetaSeries search error for {video_meta.title}: {exc}")
            return []

    def _search_episode(self, video_meta: VideoMetadata, language: str) -> list[dict[str, Any]]:
        """Search subtitles for a TV episode."""
        endpoint = f"{BETASERIES_API_URL}/subtitles/last"
        params: dict[str, Any] = {
            "number": 50,
            "v": "3.0",
        }
        if language in ("en", "eng"):
            params["language"] = "vo"
        elif language in ("fr", "fra"):
            params["language"] = "vf"

        # Attempt targeted show lookup if IMDb ID or title is available
        if video_meta.imdb_id:
            params["imdb_id"] = video_meta.imdb_id

        resp = self._send_request("GET", endpoint, params=params)
        if resp.status_code == 400:
            try:
                error_payload = orjson.loads(resp.content)
                for err in error_payload.get("errors", []):
                    code = err.get("code")
                    if code == 1001:
                        LOG.warning("BetaSeries API key is invalid.")
                    elif code == 4001:
                        LOG.debug(f"BetaSeries episode not found: {err.get('text')}")
            except orjson.JSONDecodeError as exc:
                LOG.debug(f"Failed decoding BetaSeries error response: {exc}")
            return []
        if resp.status_code != 200:
            return []

        response_payload = orjson.loads(resp.content)
        raw_subtitles = response_payload.get("subtitles", [])
        if not isinstance(raw_subtitles, list):
            return []

        # Filter strictly by season and episode
        filtered: list[dict[str, Any]] = []
        for sub_entry in raw_subtitles:
            season_num = sub_entry.get("season")
            episode_num = sub_entry.get("episode")
            if (
                season_num is not None
                and episode_num is not None
                and int(season_num) == video_meta.season
                and int(episode_num) == video_meta.episode
            ):
                filtered.append(sub_entry)
            elif not season_num and not episode_num:
                filtered.append(sub_entry)

        return filtered

    def _search_movie(self, language: str) -> list[dict[str, Any]]:
        """Search subtitles for a movie."""
        endpoint = f"{BETASERIES_API_URL}/subtitles/last"
        params: dict[str, Any] = {
            "number": 50,
            "v": "3.0",
        }
        if language in ("en", "eng"):
            params["language"] = "vo"
        elif language in ("fr", "fra"):
            params["language"] = "vf"

        resp = self._send_request("GET", endpoint, params=params)
        if resp.status_code == 400:
            try:
                error_payload = orjson.loads(resp.content)
                for err in error_payload.get("errors", []):
                    code = err.get("code")
                    if code == 1001:
                        LOG.warning("BetaSeries API key is invalid.")
                    elif code == 4001:
                        LOG.debug(f"BetaSeries movie not found: {err.get('text')}")
            except orjson.JSONDecodeError as exc:
                LOG.debug(f"Failed decoding BetaSeries error response: {exc}")
            return []
        if resp.status_code != 200:
            return []

        response_payload = orjson.loads(resp.content)
        raw_subtitles = response_payload.get("subtitles", [])
        if not isinstance(raw_subtitles, list):
            return []

        return raw_subtitles
