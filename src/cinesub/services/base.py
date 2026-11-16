from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from cinesub.core.constants import USER_AGENT
from cinesub.core.http import SESSION
from cinesub.core.logger import LOG
from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.core.ratelimit import RateLimiter
from cinesub.core.utils import save_subtitle_to_disk


class BaseHttpService:
    """Base class for subtitle services providing rate limiting and atomic download."""

    service_name: str
    limiter: RateLimiter

    def __init__(self, service_name: str, limiter: RateLimiter) -> None:
        self.service_name = service_name
        self.limiter = limiter

    @property
    def is_configured(self) -> bool:
        """Return True if service credentials or requirements are satisfied."""
        return True

    @property
    def is_available(self) -> bool:
        """Check if service is not currently circuit-broken due to connection outage."""
        return self.limiter.is_available

    def _headers(self) -> dict[str, str]:
        """Default HTTP headers."""
        return {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

    def _send_request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        max_retries: int = 3,
    ) -> httpx.Response:
        """Send HTTP request with token-bucket rate limiting, 429 backoff, and circuit breaking."""
        request_headers = headers if headers is not None else self._headers()

        for attempt in range(max_retries):
            self.limiter.acquire()
            try:
                if method.upper() == "GET" and content is None:
                    response = SESSION.get(url, params=params, headers=request_headers)
                elif method.upper() == "POST" and params is None:
                    response = SESSION.post(
                        url,
                        content=content,
                        headers=request_headers,
                    )
                else:
                    response = SESSION.request(
                        method=method,
                        url=url,
                        params=params,
                        content=content,
                        headers=request_headers,
                    )

                if response.status_code in (429, 493):
                    retry_after = response.headers.get("retry-after")
                    wait_sec = float(retry_after) if retry_after and retry_after.isdigit() else 5.0
                    LOG.warning(
                        f"{self.service_name} 429 rate limit received. Pausing for {wait_sec:.1f}s."
                    )
                    self.limiter.trigger_cooldown(wait_sec)
                    continue

                remaining_hdr = response.headers.get(
                    "x-ratelimit-remaining"
                ) or response.headers.get("x-ratelimit-remaining-second")
                if remaining_hdr and remaining_hdr.isdigit() and int(remaining_hdr) == 0:
                    reset_hdr = response.headers.get("x-ratelimit-reset") or response.headers.get(
                        "ratelimit-reset"
                    )
                    wait_sec = float(reset_hdr) if reset_hdr and reset_hdr.isdigit() else 1.0
                    self.limiter.trigger_cooldown(wait_sec)

                self.limiter.mark_recovered()
                return response
            except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
                self.limiter.mark_unreachable(60.0)
                LOG.warning(f"{self.service_name} server unreachable ({exc}). Skipping for 60s.")
                raise
            except httpx.RequestError as exc:
                if attempt == max_retries - 1:
                    raise
                backoff_delay = 0.5 * (2**attempt)
                LOG.debug(
                    f"{self.service_name} network error ({exc}), "
                    f"retrying in {backoff_delay:.1f}s..."
                )
                time.sleep(backoff_delay)

        raise RuntimeError(f"{self.service_name} request failed after retries.")

    def search(self, video_meta: VideoMetadata, language: str) -> list[SubtitleMatch]:
        """Search provider for matching subtitles."""
        raise NotImplementedError("Subclasses must implement search.")

    def download(self, subtitle: SubtitleMatch, destination: Path) -> Path:
        """Download subtitle asset from provider, unpack if archive, and normalize to UTF-8 SRT."""
        if not subtitle.download_url:
            raise ValueError(f"{self.service_name} match missing download URL.")

        download_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
        }
        response = self._send_request("GET", subtitle.download_url, headers=download_headers)
        if response.status_code != 200:
            status = response.status_code
            snippet = response.text[:100]
            raise RuntimeError(
                f"{self.service_name} download failed with status {status}: {snippet}"
            )

        return save_subtitle_to_disk(response.content, destination)
