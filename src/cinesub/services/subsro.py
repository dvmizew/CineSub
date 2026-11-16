from __future__ import annotations

import os

import httpx
import orjson

from cinesub.core.constants import SUBSRO_API_URL, USER_AGENT
from cinesub.core.logger import LOG
from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.core.ratelimit import SUBSRO_LIMITER
from cinesub.core.utils import (
    normalize_language,
    score_subtitle_candidate,
)
from cinesub.services.base import BaseHttpService
from cinesub.services.tmdb import TmdbService


class SubsRoService(BaseHttpService):
    """Service client for Subs.ro REST API v1.0."""

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(service_name="Subs.ro", limiter=SUBSRO_LIMITER)
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

    def search(self, video_meta: VideoMetadata, language: str) -> list[SubtitleMatch]:
        """Search Subs.ro for matching subtitles by IMDb ID and candidate scoring."""
        if not self.is_configured or not self.is_available:
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
            subtitles_list = response_payload.get("items", [])
            if not isinstance(subtitles_list, list):
                return []

            results: list[SubtitleMatch] = []
            for sub_entry in subtitles_list:
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

        except (httpx.HTTPError, orjson.JSONDecodeError, RuntimeError) as exc:
            LOG.debug(f"Subs.ro search error for {video_meta.title}: {exc}")
            return []
