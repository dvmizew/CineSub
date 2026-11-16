from __future__ import annotations

import os
from typing import Any
from urllib.parse import urljoin

import httpx
import orjson
from rapidfuzz import fuzz

from cinesub.core.constants import (
    SUBSOURCE_API_URL,
    USER_AGENT,
)
from cinesub.core.logger import LOG
from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.core.ratelimit import SUBSOURCE_LIMITER
from cinesub.core.utils import score_subtitle_candidate
from cinesub.services.base import BaseHttpService


class SubsourceService(BaseHttpService):
    """Service client for SubSource.net REST API v1."""

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(service_name="SubSource", limiter=SUBSOURCE_LIMITER)
        self.api_key = api_key or os.getenv("SUBSOURCE_API_KEY", "").strip() or None

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self.api_key or "",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

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
                clean_video_title = video_meta.title.strip().lower()
                best_similarity = 0.0
                for movie_entry in movies_list:
                    candidate_title = (
                        str(movie_entry.get("title") or movie_entry.get("name") or "")
                        .strip()
                        .lower()
                    )
                    if candidate_title == clean_video_title:
                        target_movie = movie_entry
                        break
                    similarity = fuzz.ratio(candidate_title, clean_video_title)
                    if similarity > best_similarity:
                        best_similarity = similarity
                        if similarity >= 70.0:
                            target_movie = movie_entry

            if target_movie is None:
                if len(movies_list) == 1:
                    target_movie = movies_list[0]
                else:
                    return []

            movie_id = target_movie.get("id")
            if not movie_id:
                return []

            subs_url = f"{SUBSOURCE_API_URL}/subtitles"
            subs_params: dict[str, Any] = {
                "movie_id": movie_id,
                "lang": language.lower(),
            }
            if video_meta.is_episode:
                if video_meta.season is not None:
                    subs_params["season"] = video_meta.season
                if video_meta.episode is not None:
                    subs_params["episode"] = video_meta.episode

            subs_response = self._send_request("GET", subs_url, params=subs_params)
            if subs_response.status_code != 200:
                return []

            subtitles_payload = orjson.loads(subs_response.content)
            raw_subtitles = subtitles_payload.get("data", [])
            if not isinstance(raw_subtitles, list):
                return []

            subtitle_matches: list[SubtitleMatch] = []
            for idx, candidate_entry in enumerate(raw_subtitles):
                sub_id = str(candidate_entry.get("id") or f"subsource_{idx}")
                release_name = (
                    candidate_entry.get("release_name")
                    or candidate_entry.get("name")
                    or f"{video_meta.title} SubSource {idx + 1}"
                )
                dl_path = (
                    candidate_entry.get("download_url")
                    or candidate_entry.get("url")
                    or f"/subtitles/{sub_id}/download"
                )
                if dl_path.startswith("http"):
                    dl_url = dl_path
                else:
                    dl_url = urljoin(f"{SUBSOURCE_API_URL}/", dl_path.lstrip("/"))

                downloads = candidate_entry.get("downloads")
                rating = candidate_entry.get("rating")
                fps = candidate_entry.get("fps")
                is_hearing_impaired = bool(candidate_entry.get("hearing_impaired", False))

                score = score_subtitle_candidate(
                    video_meta=video_meta,
                    release_name=release_name,
                    matched_by_hash=False,
                    downloads=downloads,
                )

                subtitle_matches.append(
                    SubtitleMatch(
                        id=sub_id,
                        provider="subsource",
                        language=str(candidate_entry.get("lang") or language).lower(),
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

            subtitle_matches.sort(key=lambda match: match.score, reverse=True)
            return subtitle_matches

        except (httpx.HTTPError, orjson.JSONDecodeError, RuntimeError) as exc:
            LOG.debug(f"SubSource search error: {exc}")
            return []
