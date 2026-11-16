from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx
import orjson

from cinesub.core.constants import (
    ASSRT_API_URL,
    ASSRT_FALLBACK_URL,
)
from cinesub.core.logger import LOG
from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.core.ratelimit import ASSRT_LIMITER
from cinesub.core.utils import (
    normalize_language,
    score_subtitle_candidate,
)
from cinesub.services.base import BaseHttpService

_ASSRT_LANG_MAP: dict[str, str] = {
    "langchs": "zh",
    "langcht": "zh",
    "langeng": "en",
    "langdou": "zh",
    "langjpn": "ja",
    "langkor": "ko",
    "langfra": "fr",
    "langfre": "fr",
    "langger": "de",
    "langspa": "es",
    "langrus": "ru",
}


class AssrtService(BaseHttpService):
    """Service client for Assrt.net API v1."""

    def __init__(
        self,
        api_token: str | None = None,
        base_url: str = ASSRT_API_URL,
    ) -> None:
        super().__init__(service_name="Assrt", limiter=ASSRT_LIMITER)
        self.api_token = (
            api_token
            or os.getenv("ASSRT_API_TOKEN", "").strip()
            or os.getenv("ASSRT_TOKEN", "").strip()
            or None
        )
        self.base_url = base_url.rstrip("/")

    @property
    def is_configured(self) -> bool:
        """Returns True if Assrt API token is configured."""
        return bool(self.api_token)

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def _send_request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        max_retries: int = 3,
    ) -> httpx.Response:
        """Send HTTP request with token-bucket rate limiting and fallback domain support."""
        if not self.is_configured:
            raise RuntimeError("Assrt API token is not configured.")

        request_params = dict(params or {})
        if "token" not in request_params and self.api_token:
            request_params["token"] = self.api_token

        base_urls = [self.base_url, ASSRT_FALLBACK_URL] if not url.startswith("http") else [url]
        last_network_error: Exception | None = None
        for current_base in base_urls:
            target_url = url if url.startswith("http") else f"{current_base}{url}"
            for attempt in range(max_retries):
                try:
                    return super()._send_request(
                        method=method,
                        url=target_url,
                        params=request_params,
                        content=content,
                        headers=headers,
                        max_retries=1,
                    )
                except httpx.RequestError as exc:
                    last_network_error = exc
                    if attempt < max_retries - 1:
                        backoff_delay = 0.5 * (2**attempt)
                        LOG.debug(
                            f"Assrt network error ({exc}), retrying in {backoff_delay:.1f}s..."
                        )
                        time.sleep(backoff_delay)

        if last_network_error:
            raise last_network_error
        raise RuntimeError("Assrt request failed after retries.")

    def _extract_languages(self, lang_payload: Any) -> set[str]:
        """Extract set of normalized ISO 639-1 language codes from Assrt payload."""
        languages: set[str] = set()
        if isinstance(lang_payload, dict):
            langlist = lang_payload.get("langlist", {})
            if isinstance(langlist, dict):
                for lang_key, is_active in langlist.items():
                    if is_active and lang_key in _ASSRT_LANG_MAP:
                        languages.add(_ASSRT_LANG_MAP[lang_key])
            language_description = str(lang_payload.get("desc", ""))
            if (
                "简" in language_description
                or "繁" in language_description
                or "中" in language_description
            ):
                languages.add("zh")
            if "英" in language_description:
                languages.add("en")
        elif isinstance(lang_payload, list):
            for lang_token in lang_payload:
                languages.add(normalize_language(str(lang_token)))
        elif isinstance(lang_payload, str):
            languages.add(normalize_language(lang_payload))

        return languages

    def search(self, video_meta: VideoMetadata, language: str) -> list[SubtitleMatch]:
        """Search Assrt database by filename or title."""
        if not self.is_configured or not self.is_available:
            return []

        target_lang = normalize_language(language)

        # Assrt searches best with full release filename or title
        query_string = (
            video_meta.file_path.name
            if video_meta.file_path and video_meta.file_path.name
            else video_meta.title
        )
        if len(query_string) < 3:
            query_string = video_meta.title

        params: dict[str, Any] = {
            "q": query_string,
            "cnt": 15,
            "is_file": 1,
            "no_muxer": 1,
            "filelist": 1,
        }

        try:
            response = self._send_request("GET", "/sub/search", params=params)
            if response.status_code != 200:
                return []
            payload = orjson.loads(response.content)
        except (httpx.HTTPError, orjson.JSONDecodeError) as exc:
            LOG.debug(f"Assrt search request failed: {exc}")
            return []

        if payload.get("status", 0) != 0:
            LOG.debug(f"Assrt search error: {payload.get('errmsg') or payload.get('message')}")
            return []

        sub_list: list[dict[str, Any]] = payload.get("sub", {}).get("subs", [])
        if not sub_list and query_string != video_meta.title:
            # Fallback search with pure media title
            params["q"] = video_meta.title
            params["is_file"] = 0
            try:
                fallback_resp = self._send_request("GET", "/sub/search", params=params)
                if fallback_resp.status_code == 200:
                    fallback_payload = orjson.loads(fallback_resp.content)
                    if fallback_payload.get("status", 0) == 0:
                        sub_list = fallback_payload.get("sub", {}).get("subs", [])
            except (httpx.HTTPError, orjson.JSONDecodeError):
                pass

        matches: list[SubtitleMatch] = []
        for candidate_sub in sub_list:
            sub_id = str(candidate_sub.get("id", ""))
            if not sub_id:
                continue

            available_langs = self._extract_languages(candidate_sub.get("lang"))
            if available_langs and target_lang not in available_langs:
                continue

            release_name = (
                str(candidate_sub.get("videoname") or "")
                or str(candidate_sub.get("filename") or "")
                or str(candidate_sub.get("native_name") or "")
                or str(candidate_sub.get("title") or "")
            )

            download_url = candidate_sub.get("url")
            # Multi-file archives on Assrt embed individual file download URLs in filelist
            if not download_url and candidate_sub.get("filelist"):
                file_records: list[dict[str, Any]] = candidate_sub["filelist"]
                for file_record in file_records:
                    if file_record.get("url"):
                        download_url = file_record["url"]
                        break

            vote_score = candidate_sub.get("vote_score")
            rating = float(vote_score) if vote_score is not None else None
            down_count = candidate_sub.get("down_count")
            download_count = int(down_count) if down_count is not None else None

            candidate_score = score_subtitle_candidate(
                video_meta=video_meta,
                release_name=release_name,
                downloads=download_count,
            )

            matches.append(
                SubtitleMatch(
                    id=sub_id,
                    provider="assrt",
                    language=target_lang,
                    release_name=release_name,
                    download_url=download_url,
                    rating=rating,
                    download_count=download_count,
                    score=candidate_score,
                )
            )

        matches.sort(key=lambda match_candidate: match_candidate.score, reverse=True)
        return matches

    def _resolve_download_url(self, subtitle: SubtitleMatch) -> str:
        """Fetch download URL from sub/detail if not present in search match."""
        if subtitle.download_url:
            return subtitle.download_url

        response = self._send_request(
            "GET",
            "/sub/detail",
            params={"id": subtitle.id, "filelist": 1},
        )
        if response.status_code != 200:
            raise RuntimeError(f"Assrt sub/detail failed with status {response.status_code}")

        detail_payload = orjson.loads(response.content)
        if detail_payload.get("status", 0) != 0:
            raise RuntimeError(f"Assrt sub/detail error: {detail_payload.get('errmsg')}")

        subtitle_details = detail_payload.get("sub", {}).get("subs", [])
        if not subtitle_details:
            raise ValueError(f"Assrt subtitle detail for ID {subtitle.id} not found.")

        sub_detail = subtitle_details[0]
        url = sub_detail.get("url")
        if not url and sub_detail.get("filelist"):
            for file_entry in sub_detail["filelist"]:
                if file_entry.get("url"):
                    url = file_entry["url"]
                    break

        if not url:
            raise ValueError(f"Assrt subtitle {subtitle.id} contains no valid download URL.")

        return str(url)

    def download(self, subtitle: SubtitleMatch, destination: Path) -> Path:
        """Download subtitle from Assrt, unpack if archive, and normalize to UTF-8 SRT."""
        subtitle.download_url = self._resolve_download_url(subtitle)
        return super().download(subtitle, destination)
