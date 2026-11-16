from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import orjson

from cinesub.core.constants import OPENSUBTITLES_API_URL, USER_AGENT
from cinesub.core.logger import LOG
from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.core.ratelimit import OPENSUBTITLES_LIMITER
from cinesub.core.utils import save_subtitle_to_disk, score_subtitle_candidate
from cinesub.services.base import BaseHttpService


class OpenSubtitlesService(BaseHttpService):
    """Service client for OpenSubtitles.com REST API v1."""

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(service_name="OpenSubtitles", limiter=OPENSUBTITLES_LIMITER)
        self.api_key = api_key or os.getenv("OPENSUBTITLES_API_KEY", "").strip() or None
        self.user_token = (
            os.getenv("OPENSUBTITLES_USER_TOKEN", "").strip()
            or os.getenv("OPENSUBTITLES_TOKEN", "").strip()
            or None
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Api-Key": self.api_key or "",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.user_token:
            headers["Authorization"] = f"Bearer {self.user_token}"
        return headers

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
                for subtitle_match in hash_matches:
                    if subtitle_match.file_id not in seen_file_ids:
                        seen_file_ids.add(subtitle_match.file_id)
                        matches.append(subtitle_match)
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
            for subtitle_match in query_matches:
                if subtitle_match.file_id not in seen_file_ids:
                    seen_file_ids.add(subtitle_match.file_id)
                    matches.append(subtitle_match)
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
        response = self._send_request("GET", url, params=params)
        if response.status_code != 200:
            return []

        response_payload = orjson.loads(response.content)
        subtitle_matches: list[SubtitleMatch] = []

        for candidate_entry in response_payload.get("data", []):
            candidate_attributes = candidate_entry.get("attributes", {})
            files = candidate_attributes.get("files", [])
            if not files:
                continue

            file_id = files[0].get("file_id")
            release_name = (
                candidate_attributes.get("release") or files[0].get("file_name") or video_meta.title
            )
            rating = candidate_attributes.get("ratings")
            downloads = candidate_attributes.get("download_count")
            hearing_impaired = bool(candidate_attributes.get("hearing_impaired", False))
            fps = candidate_attributes.get("fps")

            score = score_subtitle_candidate(
                video_meta=video_meta,
                release_name=release_name,
                matched_by_hash=matched_by_hash,
                downloads=downloads,
            )

            subtitle_matches.append(
                SubtitleMatch(
                    id=str(candidate_entry.get("id", file_id)),
                    provider="opensubtitles",
                    language=candidate_attributes.get("language", language),
                    release_name=release_name,
                    matched_by_hash=matched_by_hash,
                    file_id=file_id,
                    rating=float(rating) if rating is not None else None,
                    download_count=downloads,
                    hearing_impaired=hearing_impaired,
                    fps=fps,
                    score=score,
                )
            )
        return subtitle_matches

    def download(self, subtitle: SubtitleMatch, destination: Path) -> Path:
        """Download subtitle from OpenSubtitles and normalize encoding to UTF-8."""
        if not subtitle.file_id:
            raise ValueError("OpenSubtitles match missing file_id.")

        ticket_url = f"{OPENSUBTITLES_API_URL}/download"
        payload = {"file_id": int(subtitle.file_id)}

        ticket_response = self._send_request(
            "POST",
            ticket_url,
            content=orjson.dumps(payload),
        )
        if ticket_response.status_code == 406:
            try:
                msg = orjson.loads(ticket_response.content).get(
                    "message", "Download quota exceeded"
                )
            except orjson.JSONDecodeError:
                msg = "Download quota exceeded"
            raise RuntimeError(f"OpenSubtitles download quota exhausted: {msg}")

        ticket_response.raise_for_status()

        ticket_payload = orjson.loads(ticket_response.content)
        download_url = ticket_payload.get("link")
        if not download_url:
            raise RuntimeError("OpenSubtitles returned no download link.")

        download_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
        }
        file_response = self._send_request("GET", download_url, headers=download_headers)
        file_response.raise_for_status()

        return save_subtitle_to_disk(file_response.content, destination)
