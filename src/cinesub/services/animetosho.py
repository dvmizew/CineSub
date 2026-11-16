from __future__ import annotations

from typing import Any

import httpx
import orjson

from cinesub.core.constants import (
    ANIMETOSHO_FEED_URL,
    ANIMETOSHO_STORAGE_URL,
)
from cinesub.core.logger import LOG
from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.core.ratelimit import ANIMETOSHO_LIMITER
from cinesub.core.utils import (
    languages_match,
    normalize_language,
    score_subtitle_candidate,
)
from cinesub.services.base import BaseHttpService


class AnimeToshoService(BaseHttpService):
    """Service client for AnimeTosho JSON feed and storage attachments."""

    def __init__(
        self,
        feed_url: str = ANIMETOSHO_FEED_URL,
        storage_url: str = ANIMETOSHO_STORAGE_URL,
    ) -> None:
        super().__init__(service_name="AnimeTosho", limiter=ANIMETOSHO_LIMITER)
        self.feed_url = feed_url
        self.storage_url = storage_url.rstrip("/")

    def _match_language(self, track_lang: str, target_lang: str) -> bool:
        """Verify whether candidate track language matches requested language code."""
        if not track_lang:
            return target_lang in ("en", "eng")
        return languages_match(track_lang, target_lang)

    def search(self, video_meta: VideoMetadata, language: str) -> list[SubtitleMatch]:
        """Search AnimeTosho feed for releases and extract matching subtitle attachments."""
        if not self.is_available:
            return []
        target_lang = normalize_language(language)

        if video_meta.season is not None and video_meta.episode is not None:
            search_query = f"{video_meta.title} {video_meta.episode:02d}"
        elif video_meta.episode is not None:
            search_query = f"{video_meta.title} {video_meta.episode}"
        else:
            search_query = video_meta.title

        params: dict[str, Any] = {
            "q": search_query,
            "order": "date-d",
            "limit": 10,
        }

        try:
            feed_response = self._send_request("GET", self.feed_url, params=params)
            if feed_response.status_code != 200:
                return []
            releases = orjson.loads(feed_response.content)
        except (httpx.HTTPError, orjson.JSONDecodeError) as exc:
            LOG.debug(f"AnimeTosho feed search failed: {exc}")
            return []

        if not isinstance(releases, list):
            return []

        matches: list[SubtitleMatch] = []
        for release in releases[:8]:
            status = release.get("status")
            if status not in ("complete", "complete_partial"):
                continue

            release_id = release.get("id")
            release_title = str(release.get("title", ""))
            if not release_id or not release_title:
                continue

            # Feed items omit attachment metadata, requiring secondary detail query
            try:
                detail_response = self._send_request(
                    "GET",
                    self.feed_url,
                    params={"show": "torrent", "id": release_id},
                )
                if detail_response.status_code != 200:
                    continue
                torrent_details = orjson.loads(detail_response.content)
            except (httpx.HTTPError, orjson.JSONDecodeError):
                continue

            media_files: list[dict[str, Any]] = torrent_details.get("files", [])
            for media_file in media_files:
                attachments: list[dict[str, Any]] = media_file.get("attachments", [])
                for attachment in attachments:
                    if attachment.get("type") != "subtitle":
                        continue

                    track_metadata: dict[str, Any] = attachment.get("info", {})
                    codec_name = str(track_metadata.get("codec", "")).upper()
                    if codec_name not in ("ASS", "SRT"):
                        continue

                    track_lang = str(track_metadata.get("lang", "eng")).lower()
                    if not self._match_language(track_lang, target_lang):
                        continue

                    attachment_id = attachment.get("id")
                    if not attachment_id:
                        continue

                    hex_id = f"{int(attachment_id):08x}"
                    download_url = f"{self.storage_url}/{hex_id}/{attachment_id}.xz"
                    is_forced = bool(track_metadata.get("forced", 0))

                    candidate_score = score_subtitle_candidate(
                        video_meta=video_meta,
                        release_name=release_title,
                    )

                    matches.append(
                        SubtitleMatch(
                            id=f"{release_id}:{attachment_id}",
                            provider="animetosho",
                            language=target_lang,
                            release_name=release_title,
                            download_url=download_url,
                            hearing_impaired=is_forced,
                            score=candidate_score,
                        )
                    )

        matches.sort(key=lambda match_candidate: match_candidate.score, reverse=True)
        return matches
