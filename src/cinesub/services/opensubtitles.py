"""OpenSubtitles.com REST API v1 Client."""

from __future__ import annotations

import gzip
import os
from pathlib import Path
from typing import Any

import httpx
import orjson

from cinesub.core.constants import OPENSUBTITLES_API_URL, USER_AGENT
from cinesub.core.http import SESSION
from cinesub.core.logger import LOG
from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.core.ratelimit import OPENSUBTITLES_LIMITER
from cinesub.core.utils import decode_and_normalize_subtitle_content, score_subtitle_candidate


class OpenSubtitlesService:
    """Service client for OpenSubtitles.com REST API v1."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("OPENSUBTITLES_API_KEY", "").strip() or None

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Api-Key": self.api_key or "",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _send_request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        content: bytes | None = None,
        max_retries: int = 3,
    ) -> httpx.Response:
        """Send HTTP request with token-bucket rate limiting and 429 backoff."""
        for attempt in range(max_retries):
            OPENSUBTITLES_LIMITER.acquire()
            try:
                resp = SESSION.request(
                    method=method,
                    url=url,
                    params=params,
                    content=content,
                    headers=self._headers(),
                )
                if resp.status_code == 429:
                    retry_after = resp.headers.get("retry-after")
                    wait_sec = float(retry_after) if retry_after and retry_after.isdigit() else 5.0
                    LOG.warning(
                        f"OpenSubtitles 429 rate limit received. Pausing for {wait_sec:.1f}s."
                    )
                    OPENSUBTITLES_LIMITER.trigger_cooldown(wait_sec)
                    continue

                return resp
            except httpx.RequestError as exc:
                if attempt == max_retries - 1:
                    raise
                LOG.debug(f"OpenSubtitles network error ({exc}), retrying...")

        raise RuntimeError("OpenSubtitles request failed after retries.")

    def search(self, video_meta: VideoMetadata, language: str) -> list[SubtitleMatch]:
        """Search OpenSubtitles by moviehash first, then fallback to text query."""
        if not self.is_configured:
            LOG.debug("OpenSubtitles API key not configured. Skipping.")
            return []

        matches: list[SubtitleMatch] = []
        seen_file_ids: set[str | int | None] = set()

        # Step 1: Exact Hash Search
        if video_meta.moviehash:
            try:
                hash_matches = self._search_request(
                    params={"moviehash": video_meta.moviehash, "languages": language},
                    video_meta=video_meta,
                    language=language,
                    matched_by_hash=True,
                )
                for m in hash_matches:
                    if m.file_id not in seen_file_ids:
                        seen_file_ids.add(m.file_id)
                        matches.append(m)
            except Exception as exc:
                LOG.debug(f"OpenSubtitles hash search error: {exc}")

        # Step 2: Fallback query search
        try:
            params: dict[str, Any] = {"query": video_meta.title, "languages": language}
            if video_meta.is_episode:
                params["type"] = "episode"
                if video_meta.season:
                    params["season_number"] = video_meta.season
                if video_meta.episode:
                    params["episode_number"] = video_meta.episode
            else:
                params["type"] = "movie"
                if video_meta.year:
                    params["year"] = video_meta.year

            query_matches = self._search_request(
                params=params,
                video_meta=video_meta,
                language=language,
                matched_by_hash=False,
            )
            for m in query_matches:
                if m.file_id not in seen_file_ids:
                    seen_file_ids.add(m.file_id)
                    matches.append(m)
        except Exception as exc:
            LOG.debug(f"OpenSubtitles query search error: {exc}")

        matches.sort(key=lambda m: m.score, reverse=True)
        return matches

    def _search_request(
        self,
        params: dict[str, Any],
        video_meta: VideoMetadata,
        language: str,
        matched_by_hash: bool,
    ) -> list[SubtitleMatch]:
        url = f"{OPENSUBTITLES_API_URL}/subtitles"
        resp = self._send_request("GET", url, params=params)
        if resp.status_code != 200:
            return []

        data = orjson.loads(resp.content)
        results: list[SubtitleMatch] = []

        for item in data.get("data", []):
            attr = item.get("attributes", {})
            files = attr.get("files", [])
            if not files:
                continue

            file_id = files[0].get("file_id")
            release_name = attr.get("release") or files[0].get("file_name") or video_meta.title
            rating = attr.get("ratings")
            downloads = attr.get("download_count")
            hi = bool(attr.get("hearing_impaired", False))
            fps = attr.get("fps")

            score = score_subtitle_candidate(
                video_meta=video_meta,
                release_name=release_name,
                matched_by_hash=matched_by_hash,
                downloads=downloads,
            )

            results.append(
                SubtitleMatch(
                    id=str(item.get("id", file_id)),
                    provider="opensubtitles",
                    language=attr.get("language", language),
                    release_name=release_name,
                    matched_by_hash=matched_by_hash,
                    file_id=file_id,
                    rating=float(rating) if rating is not None else None,
                    download_count=downloads,
                    hearing_impaired=hi,
                    fps=fps,
                    score=score,
                    raw_data=item,
                )
            )
        return results

    def download(self, subtitle: SubtitleMatch, destination: Path) -> Path:
        """Download subtitle from OpenSubtitles and normalize encoding to UTF-8."""
        if not subtitle.file_id:
            raise ValueError("OpenSubtitles match missing file_id.")

        ticket_url = f"{OPENSUBTITLES_API_URL}/download"
        payload = {"file_id": int(subtitle.file_id)}

        ticket_resp = self._send_request(
            "POST",
            ticket_url,
            content=orjson.dumps(payload),
        )
        ticket_resp.raise_for_status()

        ticket_data = orjson.loads(ticket_resp.content)
        download_url = ticket_data.get("link")
        if not download_url:
            raise RuntimeError("OpenSubtitles returned no download link.")

        file_resp = self._send_request("GET", download_url)
        file_resp.raise_for_status()

        content = file_resp.content
        if content.startswith(b"\x1f\x8b"):
            content = gzip.decompress(content)

        clean_bytes = decode_and_normalize_subtitle_content(content)

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(clean_bytes)
        return destination
