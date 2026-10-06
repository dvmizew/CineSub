"""The Movie Database (TMDb) API v3/v4 Client."""

from __future__ import annotations

import os
from typing import Any

import httpx
import orjson

from cinesub.core.constants import TMDB_API_URL, USER_AGENT
from cinesub.core.http import SESSION
from cinesub.core.logger import LOG
from cinesub.core.models import VideoMetadata
from cinesub.core.ratelimit import TMDB_LIMITER


class TmdbService:
    """Service client for The Movie Database (TMDb) REST API v3/v4."""

    def __init__(
        self,
        access_token: str | None = None,
        api_key: str | None = None,
        account_id: str | int | None = None,
    ) -> None:
        self.access_token = (
            access_token
            or os.getenv("TMDB_READ_ACCESS_TOKEN", "").strip()
            or os.getenv("TMDB_ACCESS_TOKEN", "").strip()
            or None
        )
        self.api_key = api_key or os.getenv("TMDB_API_KEY", "").strip() or None
        self.account_id = account_id or os.getenv("TMDB_ACCOUNT_ID", "").strip() or None

    @property
    def is_configured(self) -> bool:
        return bool(self.access_token or self.api_key)

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _send_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        content: bytes | None = None,
        max_retries: int = 3,
    ) -> httpx.Response:
        """Send HTTP request with token-bucket rate limiting and 429 backoff."""
        url = f"{TMDB_API_URL}/{path.lstrip('/')}"
        query_params: dict[str, Any] = dict(params or {})
        if not self.access_token and self.api_key:
            query_params["api_key"] = self.api_key

        for attempt in range(max_retries):
            TMDB_LIMITER.acquire()
            try:
                resp = SESSION.request(
                    method=method,
                    url=url,
                    params=query_params,
                    content=content,
                    headers=self._headers(),
                )
                if resp.status_code == 429:
                    retry_after = resp.headers.get("retry-after")
                    wait_sec = float(retry_after) if retry_after and retry_after.isdigit() else 5.0
                    LOG.warning(f"TMDb 429 rate limit received. Pausing for {wait_sec:.1f}s.")
                    TMDB_LIMITER.trigger_cooldown(wait_sec)
                    continue

                return resp
            except httpx.RequestError as exc:
                if attempt == max_retries - 1:
                    raise
                LOG.debug(f"TMDb network error ({exc}), retrying...")

        raise RuntimeError("TMDb request failed after retries.")

    def search_movie(self, title: str, year: int | None = None) -> dict[str, Any] | None:
        """Search TMDb for a movie by title and optional release year."""
        if not self.is_configured:
            return None

        params: dict[str, Any] = {"query": title}
        if year:
            params["primary_release_year"] = year

        try:
            resp = self._send_request("GET", "search/movie", params=params)
            if resp.status_code != 200:
                return None

            response_payload = orjson.loads(resp.content)
            results = response_payload.get("results", [])
            if not results or not isinstance(results, list):
                return None

            return results[0]
        except (httpx.HTTPError, orjson.JSONDecodeError, RuntimeError) as exc:
            LOG.debug(f"TMDb movie search error: {exc}")
            return None

    def search_tv(self, title: str, year: int | None = None) -> dict[str, Any] | None:
        """Search TMDb for a TV series by title and optional premiere year."""
        if not self.is_configured:
            return None

        params: dict[str, Any] = {"query": title}
        if year:
            params["first_air_date_year"] = year

        try:
            resp = self._send_request("GET", "search/tv", params=params)
            if resp.status_code != 200:
                return None

            response_payload = orjson.loads(resp.content)
            results = response_payload.get("results", [])
            if not results or not isinstance(results, list):
                return None

            return results[0]
        except (httpx.HTTPError, orjson.JSONDecodeError, RuntimeError) as exc:
            LOG.debug(f"TMDb TV search error: {exc}")
            return None

    def get_external_ids(self, media_id: int, is_tv: bool = False) -> dict[str, Any]:
        """Fetch external IDs (such as IMDb ID) for a movie or TV show."""
        if not self.is_configured:
            return {}

        endpoint = f"tv/{media_id}/external_ids" if is_tv else f"movie/{media_id}/external_ids"
        try:
            resp = self._send_request("GET", endpoint)
            if resp.status_code != 200:
                return {}
            return orjson.loads(resp.content)
        except (httpx.HTTPError, orjson.JSONDecodeError, RuntimeError) as exc:
            LOG.debug(f"TMDb external IDs error: {exc}")
            return {}

    def enrich_video_metadata(self, video_meta: VideoMetadata) -> VideoMetadata:
        """Enrich VideoMetadata with official TMDb ID and verified IMDb ID if missing."""
        if not self.is_configured or (video_meta.imdb_id and video_meta.tmdb_id):
            return video_meta

        media_info = (
            self.search_tv(video_meta.title, video_meta.year)
            if video_meta.is_episode
            else self.search_movie(video_meta.title, video_meta.year)
        )
        if not media_info:
            return video_meta

        tmdb_id = media_info.get("id")
        if tmdb_id:
            video_meta.tmdb_id = int(tmdb_id)
            ext_ids = self.get_external_ids(int(tmdb_id), is_tv=video_meta.is_episode)
            imdb_id = ext_ids.get("imdb_id")
            if imdb_id:
                video_meta.imdb_id = str(imdb_id)

        return video_meta

    def add_to_favorite(
        self,
        media_id: int,
        is_tv: bool = False,
        favorite: bool = True,
        account_id: str | int | None = None,
    ) -> bool:
        """Add or remove media from user's TMDb Favorite list."""
        target_account = account_id or self.account_id or "null"
        payload = {
            "media_type": "tv" if is_tv else "movie",
            "media_id": int(media_id),
            "favorite": bool(favorite),
        }
        try:
            resp = self._send_request(
                "POST",
                f"account/{target_account}/favorite",
                content=orjson.dumps(payload),
            )
            return resp.status_code in (200, 201)
        except (httpx.HTTPError, orjson.JSONDecodeError, RuntimeError) as exc:
            LOG.error(f"Failed adding media {media_id} to TMDb favorites: {exc}")
            return False

    def add_to_watchlist(
        self,
        media_id: int,
        is_tv: bool = False,
        watchlist: bool = True,
        account_id: str | int | None = None,
    ) -> bool:
        """Add or remove media from user's TMDb Watchlist."""
        target_account = account_id or self.account_id or "null"
        payload = {
            "media_type": "tv" if is_tv else "movie",
            "media_id": int(media_id),
            "watchlist": bool(watchlist),
        }
        try:
            resp = self._send_request(
                "POST",
                f"account/{target_account}/watchlist",
                content=orjson.dumps(payload),
            )
            return resp.status_code in (200, 201)
        except (httpx.HTTPError, orjson.JSONDecodeError, RuntimeError) as exc:
            LOG.error(f"Failed adding media {media_id} to TMDb watchlist: {exc}")
            return False
