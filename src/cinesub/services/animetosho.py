from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import orjson

from cinesub.core.constants import (
    ANIMETOSHO_FEED_URL,
    ANIMETOSHO_STORAGE_URL,
    DEFAULT_TIMEOUT,
    USER_AGENT,
)
from cinesub.core.http import SESSION
from cinesub.core.logger import LOG
from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.core.ratelimit import ANIMETOSHO_LIMITER
from cinesub.core.utils import (
    convert_ass_to_srt_bytes,
    languages_match,
    normalize_language,
    save_subtitle_to_disk,
    score_subtitle_candidate,
)

_convert_ass_to_srt_bytes = convert_ass_to_srt_bytes


class AnimeToshoService:
    """Service client for AnimeTosho JSON feed and storage attachments."""

    def __init__(
        self,
        feed_url: str = ANIMETOSHO_FEED_URL,
        storage_url: str = ANIMETOSHO_STORAGE_URL,
    ) -> None:
        self.feed_url = feed_url
        self.storage_url = storage_url.rstrip("/")

    @property
    def is_configured(self) -> bool:
        """AnimeTosho is a public service requiring no API key."""
        return True

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

    def _send_request(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> httpx.Response:
        """Send HTTP GET request with token-bucket rate limiting and retries."""
        for attempt in range(max_retries):
            ANIMETOSHO_LIMITER.acquire()
            try:
                response = SESSION.get(
                    url,
                    params=params,
                    headers=self._headers(),
                    timeout=DEFAULT_TIMEOUT,
                )
                if response.status_code == 429:
                    retry_after = response.headers.get("retry-after")
                    wait_sec = float(retry_after) if retry_after and retry_after.isdigit() else 5.0
                    LOG.warning(f"AnimeTosho 429 received. Pausing for {wait_sec:.1f}s.")
                    ANIMETOSHO_LIMITER.trigger_cooldown(wait_sec)
                    continue

                return response
            except httpx.RequestError as exc:
                if attempt == max_retries - 1:
                    raise
                LOG.debug(f"AnimeTosho network error ({exc}), retrying...")

        raise RuntimeError("AnimeTosho request failed after retries.")

    def _match_language(self, track_lang: str, target_lang: str) -> bool:
        """Verify whether candidate track language matches requested language code."""
        if not track_lang:
            return target_lang in ("en", "eng")
        return languages_match(track_lang, target_lang)

    def search(self, video_meta: VideoMetadata, language: str) -> list[SubtitleMatch]:
        """Search AnimeTosho feed for releases and extract matching subtitle attachments."""
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
            feed_response = self._send_request(self.feed_url, params=params)
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

            # Query detail endpoint to inspect attachments
            try:
                detail_response = self._send_request(
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

                    track_info: dict[str, Any] = attachment.get("info", {})
                    codec_name = str(track_info.get("codec", "")).upper()
                    if codec_name not in ("ASS", "SRT"):
                        continue

                    track_lang = str(track_info.get("lang", "eng")).lower()
                    if not self._match_language(track_lang, target_lang):
                        continue

                    attachment_id = attachment.get("id")
                    if not attachment_id:
                        continue

                    hex_id = f"{int(attachment_id):08x}"
                    download_url = f"{self.storage_url}/{hex_id}/{attachment_id}.xz"
                    is_forced = bool(track_info.get("forced", 0))

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

        matches.sort(key=lambda match_item: match_item.score, reverse=True)
        return matches

    def download(self, subtitle: SubtitleMatch, destination: Path) -> Path:
        """Download subtitle attachment from AnimeTosho, unpack XZ, and normalize to SRT."""
        if not subtitle.download_url:
            raise ValueError("AnimeTosho match missing download URL.")

        response = self._send_request(subtitle.download_url)
        if response.status_code != 200:
            status = response.status_code
            snippet = response.text[:100]
            raise RuntimeError(f"AnimeTosho download failed with status {status}: {snippet}")

        return save_subtitle_to_disk(response.content, destination)
