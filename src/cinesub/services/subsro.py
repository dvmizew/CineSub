"""Subs.ro REST API v1.0 Client for Romanian Subtitles."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import orjson

from cinesub.core.constants import SUBSRO_API_URL, USER_AGENT
from cinesub.core.http import SESSION
from cinesub.core.logger import LOG
from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.core.ratelimit import SUBSRO_LIMITER
from cinesub.core.utils import (
    decode_and_normalize_subtitle_content,
    extract_best_subtitle_from_archive,
    normalize_language,
    score_subtitle_candidate,
)
from cinesub.services.tmdb import TmdbService


class SubsRoService:
    """Service client for Subs.ro REST API v1.0."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = (
            api_key
            or os.getenv("SUBSRO_API_KEY", "").strip()
            or os.getenv("SUBS_RO_API_KEY", "").strip()
            or None
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "X-Subs-Api-Key": self.api_key or "",
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
            SUBSRO_LIMITER.acquire()
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
                    LOG.warning(f"Subs.ro 429 rate limit received. Pausing for {wait_sec:.1f}s.")
                    SUBSRO_LIMITER.trigger_cooldown(wait_sec)
                    continue

                remaining_hdr = resp.headers.get("x-ratelimit-remaining")
                if remaining_hdr and remaining_hdr.isdigit() and int(remaining_hdr) == 0:
                    reset_hdr = resp.headers.get("x-ratelimit-reset")
                    wait_sec = float(reset_hdr) if reset_hdr and reset_hdr.isdigit() else 1.0
                    SUBSRO_LIMITER.trigger_cooldown(wait_sec)

                return resp
            except httpx.RequestError as exc:
                if attempt == max_retries - 1:
                    raise
                LOG.debug(f"Subs.ro network error ({exc}), retrying...")

        raise RuntimeError("Subs.ro request failed after retries.")

    def search(self, video_meta: VideoMetadata, language: str) -> list[SubtitleMatch]:
        """Search Subs.ro for matching subtitles by IMDb ID and candidate scoring."""
        if not self.is_configured:
            LOG.debug("Subs.ro API key not configured. Skipping.")
            return []

        # Subs.ro queries are indexed by IMDb ID; enrich via TMDb if missing
        if not video_meta.imdb_id:
            tmdb_svc = TmdbService()
            if tmdb_svc.is_configured:
                video_meta = tmdb_svc.enrich_video_metadata(video_meta)

        if not video_meta.imdb_id:
            LOG.debug(f"Subs.ro requires IMDb ID, unavailable for {video_meta.title}. Skipping.")
            return []

        clean_imdb = video_meta.imdb_id.strip()
        if not clean_imdb.startswith("tt"):
            clean_imdb = f"tt{clean_imdb}"

        search_url = f"{SUBSRO_API_URL}/search/imdbid/{clean_imdb}"
        target_lang = normalize_language(language)

        try:
            resp = self._send_request("GET", search_url)
            if resp.status_code != 200:
                return []

            response_payload = orjson.loads(resp.content)
            items_list = response_payload.get("items", [])
            if not isinstance(items_list, list):
                return []

            results: list[SubtitleMatch] = []
            for sub_entry in items_list:
                raw_lang = str(sub_entry.get("language") or "ro")
                entry_lang = normalize_language(raw_lang)
                if entry_lang != target_lang:
                    continue

                sub_id = str(sub_entry.get("id", ""))
                if not sub_id:
                    continue

                release_title = str(
                    sub_entry.get("release")
                    or sub_entry.get("title")
                    or f"{video_meta.title} SubsRo {sub_id}"
                )
                download_url = f"{SUBSRO_API_URL}/subtitle/{sub_id}/download"
                translator = str(sub_entry.get("translator") or "")

                score = score_subtitle_candidate(
                    video_meta=video_meta,
                    release_name=release_title,
                    matched_by_hash=False,
                )

                # Bonus for verified retail or professional translation
                if "retail" in translator.lower() or "retail" in release_title.lower():
                    score += 5.0

                results.append(
                    SubtitleMatch(
                        id=sub_id,
                        provider="subsro",
                        language=entry_lang,
                        release_name=release_title,
                        matched_by_hash=False,
                        download_url=download_url,
                        file_id=sub_id,
                        score=score,
                    )
                )

            results.sort(key=lambda m: m.score, reverse=True)
            return results

        except Exception as exc:
            LOG.debug(f"Subs.ro search error for {video_meta.title}: {exc}")
            return []

    def download(self, subtitle: SubtitleMatch, destination: Path) -> Path:
        """Download subtitle archive from Subs.ro, extract matching SRT, and normalize to UTF-8."""
        if not subtitle.download_url:
            raise ValueError("Subs.ro match missing download URL.")

        resp = self._send_request("GET", subtitle.download_url)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Subs.ro download failed with status {resp.status_code}: {resp.text[:100]}"
            )

        content_bytes = resp.content
        if not content_bytes:
            raise ValueError("Subs.ro returned empty response payload.")

        raw_srt_bytes = extract_best_subtitle_from_archive(
            archive_bytes=content_bytes,
            target_stem=destination.stem,
        )
        normalized_bytes = decode_and_normalize_subtitle_content(raw_srt_bytes)

        # Atomic POSIX write directly into video's parent directory
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_file = destination.parent / f".{destination.name}.tmp"
        temp_file.write_bytes(normalized_bytes)
        temp_file.replace(destination)

        return destination
