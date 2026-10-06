from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
import orjson

from cinesub.core.constants import (
    SUBSOURCE_API_URL,
    USER_AGENT,
)
from cinesub.core.http import SESSION
from cinesub.core.logger import LOG
from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.core.ratelimit import SUBSOURCE_LIMITER
from cinesub.core.utils import (
    save_subtitle_to_disk,
    score_subtitle_candidate,
)


class SubsourceService:
    """Service client for SubSource.net REST API v1."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("SUBSOURCE_API_KEY", "").strip() or None

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def is_available(self) -> bool:
        """Check if SubSource is not currently circuit-broken due to connection outage."""
        return SUBSOURCE_LIMITER.is_available

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self.api_key or "",
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
            SUBSOURCE_LIMITER.acquire()
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
                    LOG.warning(f"SubSource 429 rate limit received. Pausing for {wait_sec:.1f}s.")
                    SUBSOURCE_LIMITER.trigger_cooldown(wait_sec)
                    continue

                remaining_hdr = resp.headers.get("x-ratelimit-remaining")
                if remaining_hdr and remaining_hdr.isdigit() and int(remaining_hdr) == 0:
                    reset_hdr = resp.headers.get("x-ratelimit-reset")
                    wait_sec = float(reset_hdr) if reset_hdr and reset_hdr.isdigit() else 1.0
                    SUBSOURCE_LIMITER.trigger_cooldown(wait_sec)

                return resp
            except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
                SUBSOURCE_LIMITER.mark_unreachable(60.0)
                LOG.warning(f"SubSource server unreachable ({exc}). Skipping for 60s.")
                raise
            except httpx.RequestError as exc:
                if attempt == max_retries - 1:
                    raise
                backoff_delay = 0.5 * (2**attempt)
                LOG.debug(f"SubSource network error ({exc}), retrying in {backoff_delay:.1f}s...")
                time.sleep(backoff_delay)

        raise RuntimeError("SubSource request failed after retries.")

    def search(self, video_meta: VideoMetadata, language: str) -> list[SubtitleMatch]:
        """Search SubSource for matching subtitles by media lookup and candidate scoring."""
        if not self.is_configured or not self.is_available:
            return []

        search_url = f"{SUBSOURCE_API_URL}/movies/search"
        search_params: dict[str, Any] = {
            "q": video_meta.title,
            "query": video_meta.title,
            "type": "tv" if video_meta.is_episode else "movie",
        }
        if video_meta.year:
            search_params["year"] = video_meta.year

        try:
            search_resp = self._send_request("GET", search_url, params=search_params)
            if search_resp.status_code != 200:
                return []

            search_payload = orjson.loads(search_resp.content)
            movies_list = search_payload.get("data", [])
            if not movies_list or not isinstance(movies_list, list):
                return []

            target_movie = None
            if video_meta.imdb_id:
                for movie_entry in movies_list:
                    if str(movie_entry.get("imdb_id", "")).lower() == video_meta.imdb_id.lower():
                        target_movie = movie_entry
                        break

            if target_movie is None:
                target_movie = movies_list[0]

            movie_id = target_movie.get("id")
            if not movie_id:
                return []

            subs_url = f"{SUBSOURCE_API_URL}/subtitles"
            subs_params: dict[str, Any] = {
                "movie_id": movie_id,
                "lang": language.lower(),
            }
            if video_meta.is_episode:
                if video_meta.season:
                    subs_params["season"] = video_meta.season
                if video_meta.episode:
                    subs_params["episode"] = video_meta.episode

            subs_resp = self._send_request("GET", subs_url, params=subs_params)
            if subs_resp.status_code != 200:
                return []

            subtitles_payload = orjson.loads(subs_resp.content)
            raw_subtitles = subtitles_payload.get("data", [])
            if not isinstance(raw_subtitles, list):
                return []

            results: list[SubtitleMatch] = []
            for idx, sub_item in enumerate(raw_subtitles):
                sub_id = str(sub_item.get("id") or f"subsource_{idx}")
                release_name = (
                    sub_item.get("release_name")
                    or sub_item.get("name")
                    or f"{video_meta.title} SubSource {idx + 1}"
                )
                dl_path = (
                    sub_item.get("download_url")
                    or sub_item.get("url")
                    or f"/subtitles/{sub_id}/download"
                )
                if dl_path.startswith("http"):
                    dl_url = dl_path
                else:
                    dl_url = urljoin(f"{SUBSOURCE_API_URL}/", dl_path.lstrip("/"))

                downloads = sub_item.get("downloads")
                rating = sub_item.get("rating")
                fps = sub_item.get("fps")
                is_hearing_impaired = bool(sub_item.get("hearing_impaired", False))

                score = score_subtitle_candidate(
                    video_meta=video_meta,
                    release_name=release_name,
                    matched_by_hash=False,
                    downloads=downloads,
                )

                results.append(
                    SubtitleMatch(
                        id=sub_id,
                        provider="subsource",
                        language=str(sub_item.get("lang") or language).lower(),
                        release_name=release_name,
                        matched_by_hash=False,
                        download_url=dl_url,
                        file_id=sub_id,
                        rating=float(rating) if rating is not None else None,
                        download_count=downloads,
                        hearing_impaired=is_hearing_impaired,
                        fps=fps,
                        score=score,
                    )
                )

            results.sort(key=lambda match: match.score, reverse=True)
            return results

        except (httpx.HTTPError, orjson.JSONDecodeError, RuntimeError) as exc:
            LOG.debug(f"SubSource search error: {exc}")
            return []

    def download(self, subtitle: SubtitleMatch, destination: Path) -> Path:
        """Download subtitle from SubSource, unpack if needed, and normalize encoding to UTF-8."""
        if not subtitle.download_url:
            raise ValueError("SubSource match missing download URL.")

        resp = self._send_request("GET", subtitle.download_url)
        resp.raise_for_status()

        return save_subtitle_to_disk(resp.content, destination)
