from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx
import orjson

from cinesub.core.constants import OPENSUBTITLES_API_URL, USER_AGENT
from cinesub.core.http import SESSION
from cinesub.core.logger import LOG
from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.core.ratelimit import OPENSUBTITLES_LIMITER
from cinesub.core.utils import save_subtitle_to_disk, score_subtitle_candidate


class OpenSubtitlesService:
    """Service client for OpenSubtitles.com REST API v1."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("OPENSUBTITLES_API_KEY", "").strip() or None

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def is_available(self) -> bool:
        """Check if OpenSubtitles is not currently circuit-broken due to connection outage."""
        return OPENSUBTITLES_LIMITER.is_available

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

                remaining_hdr = resp.headers.get("x-ratelimit-remaining-second")
                if remaining_hdr and remaining_hdr.isdigit() and int(remaining_hdr) == 0:
                    reset_hdr = resp.headers.get("ratelimit-reset")
                    wait_sec = float(reset_hdr) if reset_hdr and reset_hdr.isdigit() else 1.0
                    OPENSUBTITLES_LIMITER.trigger_cooldown(wait_sec)

                return resp
            except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
                OPENSUBTITLES_LIMITER.mark_unreachable(60.0)
                LOG.warning(f"OpenSubtitles server unreachable ({exc}). Skipping for 60s.")
                raise
            except httpx.RequestError as exc:
                if attempt == max_retries - 1:
                    raise
                backoff_delay = 0.5 * (2**attempt)
                LOG.debug(f"OpenSubtitles network error ({exc}), retrying in {backoff_delay:.1f}s")
                time.sleep(backoff_delay)

        raise RuntimeError("OpenSubtitles request failed after retries.")

    def search(self, video_meta: VideoMetadata, language: str) -> list[SubtitleMatch]:
        """Search OpenSubtitles by moviehash first, then fallback to text query."""
        if not self.is_configured or not self.is_available:
            return []

        matches: list[SubtitleMatch] = []
        seen_file_ids: set[str | int | None] = set()

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
            except (httpx.HTTPError, orjson.JSONDecodeError, RuntimeError) as exc:
                LOG.debug(f"OpenSubtitles hash search error: {exc}")

        try:
            params: dict[str, Any] = {"query": video_meta.title, "languages": language}
            if video_meta.imdb_id:
                clean_imdb = video_meta.imdb_id.lower().replace("tt", "")
                if clean_imdb.isdigit():
                    params["imdb_id"] = int(clean_imdb)

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
        except (httpx.HTTPError, orjson.JSONDecodeError, RuntimeError) as exc:
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

        response_payload = orjson.loads(resp.content)
        results: list[SubtitleMatch] = []

        for candidate_entry in response_payload.get("data", []):
            attr = candidate_entry.get("attributes", {})
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
                    id=str(candidate_entry.get("id", file_id)),
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
        if ticket_resp.status_code == 406:
            try:
                msg = orjson.loads(ticket_resp.content).get("message", "Download quota exceeded")
            except orjson.JSONDecodeError:
                msg = "Download quota exceeded"
            raise RuntimeError(f"OpenSubtitles download quota exhausted: {msg}")

        ticket_resp.raise_for_status()

        ticket_payload = orjson.loads(ticket_resp.content)
        download_url = ticket_payload.get("link")
        if not download_url:
            raise RuntimeError("OpenSubtitles returned no download link.")

        file_resp = self._send_request("GET", download_url)
        file_resp.raise_for_status()

        return save_subtitle_to_disk(file_resp.content, destination)
