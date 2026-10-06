from __future__ import annotations

import secrets
from pathlib import Path

import defusedxml.ElementTree as ET
from babelfish import Error as BabelfishError
from babelfish import Language

from cinesub.core.constants import DEFAULT_TIMEOUT
from cinesub.core.http import SESSION
from cinesub.core.logger import LOG
from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.core.ratelimit import BSPLAYER_LIMITER
from cinesub.core.utils import (
    normalize_language,
    save_subtitle_to_disk,
    score_subtitle_candidate,
)

_SOAP_ENVELOPE_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
    'xmlns:ns1="{url}">'
    '<SOAP-ENV:Body SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    "<ns1:{action}>{params}</ns1:{action}>"
    "</SOAP-ENV:Body>"
    "</SOAP-ENV:Envelope>"
)

_SUBDOMAINS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8, 101, 102, 103, 104, 105, 106, 107, 108, 109)


class BsplayerService:
    """Service client for BSPlayer SOAP/XML subtitle API."""

    def __init__(self, endpoint_url: str | None = None) -> None:
        self.endpoint_url = endpoint_url

    @property
    def is_configured(self) -> bool:
        """BSPlayer is a free service requiring no user credentials."""
        return True

    def _resolve_endpoint(self) -> str:
        if self.endpoint_url:
            return self.endpoint_url
        chosen_subdomain = secrets.choice(_SUBDOMAINS)
        return f"http://s{chosen_subdomain}.api.bsplayer-subtitles.com/v1.php"

    def _send_soap_call(self, action: str, params: str) -> ET.Element:
        """Execute a SOAP action and return the parsed <return> XML element."""
        endpoint = self._resolve_endpoint()
        headers = {
            "User-Agent": "BSPlayer/2.x (1022.12362)",
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{endpoint}#{action}"',
            "Connection": "close",
        }
        body = _SOAP_ENVELOPE_TEMPLATE.format(url=endpoint, action=action, params=params)

        BSPLAYER_LIMITER.acquire()
        response = SESSION.post(
            endpoint,
            content=body.encode("utf-8"),
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code == 429:
            BSPLAYER_LIMITER.trigger_cooldown(5.0)
            raise RuntimeError("BSPlayer rate limit reached.")

        response.raise_for_status()

        try:
            tree = ET.fromstring(response.text.strip())
        except (ET.ParseError, ET.DefusedXmlException) as exc:
            raise ValueError(f"Failed parsing BSPlayer XML response: {exc}") from exc

        return_node = tree.find(".//return")
        if return_node is None:
            raise ValueError("BSPlayer SOAP response missing <return> element.")

        return return_node

    def _login(self) -> str:
        """Authenticate anonymously and acquire a session handle."""
        params = "<username></username><password></password><AppID>BSPlayer v2.72</AppID>"
        return_node = self._send_soap_call("logIn", params)

        data_node = return_node.find("data")
        status_node = return_node.find("status")
        result_node = return_node.find("result")

        is_success = (status_node is not None and status_node.text == "OK") or (
            result_node is not None and result_node.text == "200"
        )
        if not is_success or data_node is None or not data_node.text:
            raise ConnectionError("BSPlayer anonymous login failed.")

        return data_node.text.strip()

    def _logout(self, token: str) -> None:
        """Close active session handle."""
        if not token:
            return
        try:
            self._send_soap_call("logOut", f"<handle>{token}</handle>")
        except Exception as exc:
            LOG.debug(f"BSPlayer logout ignored: {exc}")

    def _get_language_ids(self, language_code: str) -> str:
        """Generate comma-separated 3-letter language IDs for BSPlayer query."""
        norm_lang = normalize_language(language_code)
        lang_set: set[str] = {norm_lang}

        try:
            lang_obj = Language.fromalpha2(norm_lang)
            if lang_obj.alpha3:
                lang_set.add(str(lang_obj.alpha3))
            if getattr(lang_obj, "alpha3b", None):
                lang_set.add(str(lang_obj.alpha3b))
            if getattr(lang_obj, "alpha3t", None):
                lang_set.add(str(lang_obj.alpha3t))
        except (ValueError, LookupError, BabelfishError):
            pass

        return ",".join(sorted(lang_set))

    def search(self, video_meta: VideoMetadata, language: str) -> list[SubtitleMatch]:
        """Search BSPlayer database by 64-bit OSHash or IMDb ID."""
        if not video_meta.moviehash and not video_meta.imdb_id:
            LOG.debug("BSPlayer requires movie hash or IMDb ID for lookup. Skipping.")
            return []

        target_lang = normalize_language(language)
        language_ids = self._get_language_ids(target_lang)

        movie_hash = video_meta.moviehash or "0"
        movie_size = str(video_meta.file_size) if video_meta.file_size > 0 else "0"
        clean_imdb_id = video_meta.imdb_id.lstrip("t") if video_meta.imdb_id else "*"

        try:
            session_token = self._login()
        except Exception as exc:
            LOG.debug(f"BSPlayer login failed: {exc}")
            return []

        search_params = (
            f"<handle>{session_token}</handle>"
            f"<movieHash>{movie_hash}</movieHash>"
            f"<movieSize>{movie_size}</movieSize>"
            f"<languageId>{language_ids}</languageId>"
            f"<imdbId>{clean_imdb_id}</imdbId>"
        )

        try:
            return_node = self._send_soap_call("searchSubtitles", search_params)
        except Exception as exc:
            LOG.debug(f"BSPlayer searchSubtitles request failed: {exc}")
            return []
        finally:
            self._logout(session_token)

        status_node = return_node.find("result/status")
        result_node = return_node.find("result/result")
        if result_node is None:
            result_node = return_node.find("result")
        is_success = (status_node is not None and status_node.text == "OK") or (
            result_node is not None and result_node.text == "200"
        )
        if not is_success:
            return []

        matches: list[SubtitleMatch] = []
        for item_node in return_node.findall("data/item"):
            sub_id = item_node.findtext("subID", "").strip()
            release_name = item_node.findtext("subName", "").strip()
            download_url = item_node.findtext("subDownloadLink", "").strip()
            if not download_url:
                continue

            raw_rating = item_node.findtext("subRating", "0").strip()
            try:
                rating = float(raw_rating)
            except ValueError:
                rating = 0.0

            matched_by_hash = bool(video_meta.moviehash and movie_hash != "0")

            candidate_score = score_subtitle_candidate(
                video_meta=video_meta,
                release_name=release_name,
                matched_by_hash=matched_by_hash,
            )

            matches.append(
                SubtitleMatch(
                    id=sub_id,
                    provider="bsplayer",
                    language=target_lang,
                    release_name=release_name,
                    matched_by_hash=matched_by_hash,
                    download_url=download_url,
                    rating=rating,
                    score=candidate_score,
                )
            )

        matches.sort(key=lambda match_item: match_item.score, reverse=True)
        return matches

    def download(self, subtitle: SubtitleMatch, destination: Path) -> Path:
        """Download subtitle from BSPlayer, decompress Gzip, and write atomically."""
        if not subtitle.download_url:
            raise ValueError("BSPlayer match missing download URL.")

        headers = {
            "User-Agent": "Mozilla/4.0 (compatible; Synapse)",
            "Connection": "close",
        }

        BSPLAYER_LIMITER.acquire()
        response = SESSION.get(subtitle.download_url, headers=headers, timeout=DEFAULT_TIMEOUT)
        if response.status_code != 200:
            status = response.status_code
            snippet = response.text[:100]
            raise RuntimeError(f"BSPlayer download failed with status {status}: {snippet}")

        content_bytes = response.content
        if not content_bytes or response.text.strip() == "500":
            raise ValueError("BSPlayer returned invalid or empty subtitle archive.")

        return save_subtitle_to_disk(content_bytes, destination)
