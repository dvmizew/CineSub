"""SubDL.com REST API Client."""

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
from cinesub.core.http import SESSION
from cinesub.core.logger import LOG
from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.core.ratelimit import SUBDL_LIMITER
from cinesub.core.utils import (
    decode_and_normalize_subtitle_content,
    extract_best_subtitle_from_archive,
    score_subtitle_candidate,
)


class SubdlService:
    """Service client for SubDL API."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("SUBDL_API_KEY", "").strip() or None

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _send_request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> httpx.Response:
        """Send HTTP request with token-bucket rate limiting and 429 backoff."""
        for attempt in range(max_retries):
            SUBDL_LIMITER.acquire()
            try:
                resp = SESSION.request(
                    method=method,
                    url=url,
                    params=params,
                )
                if resp.status_code == 429:
                    retry_after = resp.headers.get("retry-after")
                    wait_sec = float(retry_after) if retry_after and retry_after.isdigit() else 5.0
                    LOG.warning(f"SubDL 429 rate limit received. Pausing for {wait_sec:.1f}s.")
                    SUBDL_LIMITER.trigger_cooldown(wait_sec)
                    continue

                return resp
            except httpx.RequestError as exc:
                if attempt == max_retries - 1:
                    raise
                LOG.debug(f"SubDL network error ({exc}), retrying...")

        raise RuntimeError("SubDL request failed after retries.")

    def search(self, video_meta: VideoMetadata, language: str) -> list[SubtitleMatch]:
        """Search SubDL for matching subtitles."""
        if not self.is_configured:
            LOG.debug("SubDL API key not configured. Skipping.")
            return []

        url = f"{SUBDL_API_URL}/subtitles"
        params: dict[str, Any] = {
            "api_key": self.api_key,
            "film_name": video_meta.title,
            "file_name": video_meta.file_path.name,
            "languages": language.upper(),
            "releases": 1,
            "hi": 1,
        }

        if video_meta.imdb_id:
            params["imdb_id"] = video_meta.imdb_id

        if video_meta.is_episode:
            params["type"] = "tv"
            if video_meta.season:
                params["season_number"] = video_meta.season
            if video_meta.episode:
                params["episode_number"] = video_meta.episode
        else:
            params["type"] = "movie"
            if video_meta.year:
                params["year"] = video_meta.year

        try:
            resp = self._send_request("GET", url, params=params)
            if resp.status_code != 200:
                return []

            response_payload = orjson.loads(resp.content)
            if not response_payload.get("status"):
                return []

            results: list[SubtitleMatch] = []
            for idx, sub_item in enumerate(response_payload.get("subtitles", [])):
                release_name = (
                    sub_item.get("release_name")
                    or sub_item.get("name")
                    or f"{video_meta.title} Subtitle {idx + 1}"
                )
                raw_url = sub_item.get("download_link") or sub_item.get("url") or ""
                if raw_url.startswith("http"):
                    dl_url = raw_url
                else:
                    dl_url = urljoin(SUBDL_DL_URL, raw_url)

                score = score_subtitle_candidate(
                    video_meta=video_meta,
                    release_name=release_name,
                    matched_by_hash=False,
                )

                sub_id = str(sub_item.get("id") or sub_item.get("file_n_id") or f"subdl_{idx}")
                results.append(
                    SubtitleMatch(
                        id=sub_id,
                        provider="subdl",
                        language=(sub_item.get("lang") or language).lower(),
                        release_name=release_name,
                        matched_by_hash=False,
                        download_url=dl_url,
                        file_id=sub_id,
                        hearing_impaired=bool(sub_item.get("hi", False)),
                        fps=sub_item.get("fps") or sub_item.get("framerate"),
                        score=score,
                    )
                )

            results.sort(key=lambda match: match.score, reverse=True)
            return results

        except Exception as exc:
            LOG.debug(f"SubDL search error: {exc}")
            return []

    def download(self, subtitle: SubtitleMatch, destination: Path) -> Path:
        """Download subtitle from SubDL, unpack if needed, and normalize encoding to UTF-8."""
        if not subtitle.download_url:
            raise ValueError("SubDL match missing download URL.")

        download_params = {"api_key": self.api_key} if self.api_key else None
        resp = self._send_request("GET", subtitle.download_url, params=download_params)
        resp.raise_for_status()

        raw_bytes = extract_best_subtitle_from_archive(resp.content, destination.stem)
        clean_bytes = decode_and_normalize_subtitle_content(raw_bytes)

        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_dest = destination.parent / f".{destination.name}.tmp"
        temp_dest.write_bytes(clean_bytes)
        temp_dest.replace(destination)
        return destination
