from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx
import orjson

from cinesub.core.constants import BETASERIES_API_URL, USER_AGENT
from cinesub.core.http import SESSION
from cinesub.core.logger import LOG
from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.core.ratelimit import BETASERIES_LIMITER
from cinesub.core.utils import (
    normalize_language,
    save_subtitle_to_disk,
    score_subtitle_candidate,
)
from cinesub.services.tmdb import TmdbService


class BetaSeriesService:
    """Service client for BetaSeries REST API v3.0."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = (
            api_key
            or os.getenv("BETASERIES_API_KEY", "").strip()
            or os.getenv("BETA_SERIES_API_KEY", "").strip()
            or None
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def is_available(self) -> bool:
        """Check if BetaSeries is not currently circuit-broken due to connection outage."""
        return BETASERIES_LIMITER.is_available

    def _headers(self) -> dict[str, str]:
        return {
            "X-BetaSeries-Key": self.api_key or "",
            "X-BetaSeries-Version": "3.0",
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
            BETASERIES_LIMITER.acquire()
            try:
                resp = SESSION.request(
                    method=method,
                    url=url,
                    params=params,
                    headers=self._headers(),
                )
                if resp.status_code == 429:
                    retry_after = resp.headers.get("retry-after")
                    wait_sec = float(retry_after) if retry_after and retry_after.isdigit() else 5.0
                    LOG.warning(f"BetaSeries 429 rate limit received. Pausing for {wait_sec:.1f}s.")
                    BETASERIES_LIMITER.trigger_cooldown(wait_sec)
                    continue

                remaining_hdr = resp.headers.get("x-ratelimit-remaining")
                if remaining_hdr and remaining_hdr.isdigit() and int(remaining_hdr) == 0:
                    reset_hdr = resp.headers.get("x-ratelimit-reset")
                    wait_sec = float(reset_hdr) if reset_hdr and reset_hdr.isdigit() else 1.0
                    BETASERIES_LIMITER.trigger_cooldown(wait_sec)

                return resp
            except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
                BETASERIES_LIMITER.mark_unreachable(60.0)
                LOG.warning(f"BetaSeries server unreachable ({exc}). Skipping for 60s.")
                raise
            except httpx.RequestError as exc:
                if attempt == max_retries - 1:
                    raise
                backoff_delay = 0.5 * (2**attempt)
                LOG.debug(f"BetaSeries network error ({exc}), retrying in {backoff_delay:.1f}s...")
                time.sleep(backoff_delay)

        raise RuntimeError("BetaSeries request failed after retries.")

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
                subs_list = self._search_movie(video_meta, target_lang)

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

        # Filter by season and episode
        filtered: list[dict[str, Any]] = []
        for s in raw_subtitles:
            season_num = s.get("season")
            episode_num = s.get("episode")
            if (
                season_num is not None
                and episode_num is not None
                and int(season_num) == video_meta.season
                and int(episode_num) == video_meta.episode
            ):
                filtered.append(s)
            elif not season_num and not episode_num:
                filtered.append(s)

        return filtered if filtered else raw_subtitles

    def _search_movie(self, video_meta: VideoMetadata, language: str) -> list[dict[str, Any]]:
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

    def download(self, subtitle: SubtitleMatch, destination: Path) -> Path:
        """Download subtitle from BetaSeries, extract matching SRT, and normalize to UTF-8."""
        if not subtitle.download_url:
            raise ValueError("BetaSeries match missing download URL.")

        resp = self._send_request("GET", subtitle.download_url)
        if resp.status_code != 200:
            raise RuntimeError(
                f"BetaSeries download failed with status {resp.status_code}: {resp.text[:100]}"
            )

        return save_subtitle_to_disk(resp.content, destination)
