from __future__ import annotations

import contextlib
import datetime
import gzip
import io
import lzma
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import orjson
import srt
from babelfish import Error as BabelfishError
from babelfish import Language
from charset_normalizer import from_bytes
from guessit import guessit
from rapidfuzz import fuzz

from cinesub.core.constants import (
    GZIP_MAGIC_BYTES,
    IGNORED_DIRS,
    MAX_SUBTITLE_DURATION_TOLERANCE_SECONDS,
    MIN_SUBTITLE_DURATION_RATIO,
    SCORE_BASE,
    SCORE_CODEC_MATCH,
    SCORE_HASH_MATCH_BASE,
    SCORE_MAX_DOWNLOAD_BONUS,
    SCORE_RELEASE_GROUP_THRESHOLD,
    SCORE_RELEASE_GROUP_WEIGHT,
    SCORE_RESOLUTION_MATCH,
    SCORE_SOURCE_MATCH,
    SHORT_TITLE_MAX_LENGTH,
    SUBTITLE_EXTENSIONS,
    SUPPORTED_VIDEO_EXTS,
    XZ_MAGIC_BYTES,
    ZIP_MAGIC_BYTES,
)
from cinesub.core.hasher import calculate_movie_hash
from cinesub.core.logger import LOG
from cinesub.core.models import VideoMetadata


class InterruptedOperationError(KeyboardInterrupt):
    """Raised when an operation is cancelled via SIGINT / KeyboardInterrupt,
    carrying partial progress.
    """

    def __init__(self, partial_result: Any = None) -> None:
        super().__init__()
        self.partial_result = partial_result


def is_interruption(exc: BaseException) -> bool:
    """Return True if an exception represents a SIGINT/Ctrl+C user cancellation,
    including threading Condition lock release errors triggered during signal interrupts.
    """
    if isinstance(exc, (KeyboardInterrupt, InterruptedOperationError)):
        return True
    if isinstance(exc, RuntimeError) and "release unlocked lock" in str(exc):
        return True
    context = getattr(exc, "__context__", None)
    return bool(context and isinstance(context, KeyboardInterrupt))


def normalize_language(lang: str) -> str:
    """Normalize language code or name to standard 2-letter ISO 639-1 code."""
    cleaned = lang.strip().lower()
    converters = (Language.fromalpha2, Language.fromalpha3b, Language.fromname)
    for converter in converters:
        try:
            converted = converter(cleaned)
            alpha2_code = getattr(converted, "alpha2", None)
            if alpha2_code:
                return str(alpha2_code)
        except (ValueError, LookupError, BabelfishError):
            pass

    return cleaned[:2]


def normalize_language_alpha3(lang: str, bibliographic: bool = False) -> str:
    """Normalize language code or name to standard 3-letter ISO 639-2 code."""
    alpha2 = normalize_language(lang)
    try:
        lang_obj = Language.fromalpha2(alpha2)
        if bibliographic:
            alpha3b = getattr(lang_obj, "alpha3b", None)
            if alpha3b:
                return str(alpha3b)
        return str(lang_obj.alpha3)
    except (ValueError, LookupError, BabelfishError):
        return alpha2


def languages_match(lang_a: str, lang_b: str) -> bool:
    """Check if two language codes/names refer to the same language."""
    norm_a = normalize_language(lang_a)
    norm_b = normalize_language(lang_b)
    if norm_a == norm_b:
        return True
    try:
        return Language.fromalpha2(norm_a).alpha3 == Language.fromalpha2(norm_b).alpha3
    except (ValueError, LookupError, BabelfishError):
        return False


def decode_and_normalize_subtitle_content(raw_bytes: bytes) -> bytes:
    """Decode raw subtitle bytes and re-encode as clean, normalized UTF-8 SRT."""
    if not raw_bytes:
        return b""
    if len(raw_bytes) > 10 * 1024 * 1024:
        raise ValueError(f"Subtitle payload too large ({len(raw_bytes)} bytes, maximum 10MB).")

    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        match = from_bytes(raw_bytes).best()
        text = str(match) if match is not None else raw_bytes.decode("utf-8", errors="replace")

    if text.startswith("\ufeff"):
        text = text[1:]

    try:
        subtitles = list(srt.parse(text))
        if subtitles:
            text = srt.compose(subtitles, reindex=True)
    except srt.SRTParseError as exc:
        LOG.debug(f"Non-standard SRT structure, keeping decoded text: {exc}")

    return text.encode("utf-8")


def extract_best_subtitle_from_archive(
    archive_bytes: bytes,
    target_stem: str,
    max_uncompressed_bytes: int = 10 * 1024 * 1024,
) -> bytes:
    """Safely unpack an in-memory subtitle archive and return best matching SRT content bytes."""
    if not archive_bytes:
        raise ValueError("Archive payload is empty.")

    if not archive_bytes.startswith(ZIP_MAGIC_BYTES):
        return archive_bytes

    archive_buffer = io.BytesIO(archive_bytes)
    with zipfile.ZipFile(archive_buffer) as zip_archive:
        total_uncompressed = sum(zip_member.file_size for zip_member in zip_archive.infolist())
        if total_uncompressed > max_uncompressed_bytes:
            raise ValueError(
                f"Archive payload exceeds safe uncompressed threshold "
                f"({total_uncompressed} bytes, max {max_uncompressed_bytes} bytes)."
            )

        valid_candidates = [
            member_name
            for member_name in zip_archive.namelist()
            if not os.path.basename(member_name).startswith(".")
            and any(member_name.lower().endswith(sub_ext) for sub_ext in SUBTITLE_EXTENSIONS)
        ]
        if not valid_candidates:
            raise ValueError("No valid subtitle file found inside archive.")

        chosen_candidate = valid_candidates[0]
        if len(valid_candidates) > 1:
            clean_target_stem = target_stem.lower()
            best_ratio = -1.0
            for candidate_name in valid_candidates:
                entry_filename = os.path.basename(candidate_name).lower()
                shared_chars = sum(1 for char in entry_filename if char in clean_target_stem)
                ratio = shared_chars / max(1, len(entry_filename))
                if ratio > best_ratio:
                    best_ratio = ratio
                    chosen_candidate = candidate_name

        return zip_archive.read(chosen_candidate)


_DIALOGUE_REGEX = re.compile(
    r"^Dialogue:\s*[^,]*,(\d+:\d+:\d+[\.,]\d+),(\d+:\d+:\d+[\.,]\d+),.*?,.*?,.*?,.*?,.*?,.*?,(.*)$"
)


def _parse_ass_timestamp(timestamp_str: str) -> datetime.timedelta:
    """Parse ASS timestamp H:MM:SS.cc into datetime.timedelta."""
    parts = timestamp_str.replace(",", ".").split(":")
    hours = int(parts[0])
    minutes = int(parts[1])
    sec_parts = parts[2].split(".")
    seconds = int(sec_parts[0])
    fraction_str = sec_parts[1] if len(sec_parts) > 1 else "0"
    milliseconds = int(fraction_str.ljust(3, "0")[:3])
    return datetime.timedelta(
        hours=hours,
        minutes=minutes,
        seconds=seconds,
        milliseconds=milliseconds,
    )


def convert_ass_to_srt_bytes(ass_bytes: bytes) -> bytes:
    """Convert ASS/SSA content to standard UTF-8 SRT bytes."""
    try:
        ass_text = ass_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        ass_text = ass_bytes.decode("utf-8", errors="replace")

    subtitles: list[srt.Subtitle] = []
    current_index = 1

    for line in ass_text.splitlines():
        trimmed = line.strip()
        if not trimmed.startswith("Dialogue:"):
            continue
        match = _DIALOGUE_REGEX.match(trimmed)
        if not match:
            continue
        start_str, end_str, raw_text = match.groups()

        clean_text = re.sub(r"\{.*?\}", "", raw_text)
        clean_text = (
            clean_text.replace(r"\N", "\n").replace(r"\n", "\n").replace(r"\h", " ").strip()
        )
        if not clean_text:
            continue

        try:
            start_delta = _parse_ass_timestamp(start_str)
            end_delta = _parse_ass_timestamp(end_str)
            if end_delta <= start_delta:
                continue

            subtitles.append(
                srt.Subtitle(
                    index=current_index,
                    start=start_delta,
                    end=end_delta,
                    content=clean_text,
                )
            )
            current_index += 1
        except (ValueError, IndexError):
            continue

    if not subtitles:
        return ass_bytes

    composed_srt = srt.compose(subtitles, reindex=True)
    return composed_srt.encode("utf-8")


def atomic_write_file(destination: Path, content: bytes) -> Path:
    """Atomically write content bytes to destination path using a temporary hidden file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_file = destination.parent / f".{destination.name}.tmp"
    try:
        temp_file.write_bytes(content)
        os.replace(temp_file, destination)
        return destination
    finally:
        if temp_file.exists():
            with contextlib.suppress(OSError):
                temp_file.unlink()


def save_subtitle_to_disk(
    raw_payload: bytes,
    destination: Path,
    max_payload_bytes: int = 10 * 1024 * 1024,
) -> Path:
    """Safely unpack, convert, normalize, and atomically write subtitle content to disk.

    Handles:
    - In-memory Gzip decompression
    - In-memory LZMA/XZ decompression
    - In-memory ZIP archive unpacking
    - ASS/SSA dialogue conversion to standard SRT
    - Character encoding detection and normalization to UTF-8
    - SRT structural validation and reindexing
    - Atomic POSIX file replacement
    """
    if not raw_payload:
        raise ValueError("Subtitle payload is empty.")

    payload = raw_payload

    if payload.startswith(GZIP_MAGIC_BYTES):
        payload = gzip.decompress(payload)
    elif payload.startswith(XZ_MAGIC_BYTES):
        payload = lzma.decompress(payload)
    elif payload.startswith(ZIP_MAGIC_BYTES):
        payload = extract_best_subtitle_from_archive(
            payload,
            target_stem=destination.stem,
            max_uncompressed_bytes=max_payload_bytes,
        )

    if len(payload) > max_payload_bytes:
        raise ValueError(
            f"Subtitle payload exceeds safe threshold "
            f"({len(payload)} bytes, max {max_payload_bytes} bytes)."
        )

    if payload.startswith(b"[Script Info]") or b"Dialogue:" in payload[:4096]:
        payload = convert_ass_to_srt_bytes(payload)

    normalized_bytes = decode_and_normalize_subtitle_content(payload)
    return atomic_write_file(destination, normalized_bytes)


_GROUP_CLUSTERS: list[frozenset[str]] = [
    # YTS / YIFY ecosystem
    frozenset(
        {
            "yts",
            "yts.mx",
            "yts.lt",
            "yts.am",
            "yts.ag",
            "yts.gg",
            "yts.bz",
            "yts.vc",
            "yts.to",
            "yts.pm",
            "yify",
            "yify-torrents",
        }
    ),
    # QxR / UTR / Tigole ecosystem (x265 high-efficiency BluRay encodes)
    frozenset(
        {
            "qxr",
            "tigole",
            "utr",
            "rcvr",
            "judas",
            "rzero",
            "ghost",
            "silence",
            "vyndros",
            "samwise",
            "trom",
            "monkee",
        }
    ),
    # High-tier P2P WEB-DL groups
    frozenset(
        {
            "ntb",
            "flux",
            "cmrg",
            "kings",
            "lazy",
            "tepes",
            "smurf",
            "nosivid",
            "eclipse",
            "playweb",
            "glhf",
            "whoknows",
            "b2b",
            "ggwp",
            "alfa",
            "monolith",
            "cakes",
            "tbs",
        }
    ),
    # Compact / micro-encoders
    frozenset(
        {
            "psa",
            "psarips",
            "galaxyrg",
            "tgx",
            "galaxytv",
            "pahe",
            "minihd",
            "mkvcage",
            "bone",
            "rmteam",
            "subzer0",
            "dense",
            "nep",
            "fgt",
        }
    ),
    # Scene giants (HD/BluRay/WEB/TV)
    frozenset(
        {
            "sparks",
            "rovers",
            "drones",
            "geckos",
            "amiable",
            "shortbrehd",
            "blow",
            "depraved",
            "mayhem",
            "vethd",
            "sinners",
            "lost",
            "chd",
            "publichd",
            "dimension",
            "lol",
            "fleet",
            "immerse",
            "killers",
            "batv",
            "fqm",
            "river",
            "deflate",
            "rubix",
            "tla",
            "saints",
            "demand",
            "strife",
            "fum",
            "organic",
            "swag",
            "mind",
            "jyk",
        }
    ),
    # Top-tier Remux / Encode groups (PTP, HDB, BeyondHD, AHD)
    frozenset(
        {
            "framestor",
            "ctrlhd",
            "don",
            "d-z0n3",
            "ift",
            "w4nk3r",
            "epsilon",
            "hdchina",
            "hds",
            "hdwing",
            "chdbits",
            "ttg",
            "wiki",
            "ebp",
            "decibel",
            "ptp",
            "hdb",
            "beyondhd",
            "frazer",
            "termitermx",
            "kralimarko",
            "crapht",
            "fluxremux",
            "blurole",
        }
    ),
    # General scene / repackers / web-rip
    frozenset(
        {
            "evo",
            "evolution",
            "rarbg",
            "vxt",
            "etrg",
            "axxo",
            "klaxxon",
            "juggs",
            "proper",
            "repack",
            "real",
        }
    ),
    # Anime release groups & subbers
    frozenset(
        {
            "erai-raws",
            "subsplease",
            "horriblesubs",
            "commie",
            "asenshi",
            "coalgirls",
            "scy",
            "doki",
            "mezashite",
            "fff",
            "kametsu",
            "mtbb",
            "reinforce",
            "neikos",
            "ember",
            "smokey",
            "beetle",
            "vivid",
            "chyu",
        }
    ),
    # Automated / Micro WEB & TV Encoders
    frozenset(
        {
            "megusta",
            "surcode",
            "pignus",
            "tekno",
            "stuttershit",
            "edith",
            "tommy",
            "bipolar",
            "kimchi",
            "flame",
            "xebec",
            "codie",
            "trump",
            "bored",
            "asurada",
            "trivi4",
            "fused",
            "theonlyh3r0",
            "minx",
            "afg",
            "mkvking",
            "rm4k",
            "rartv",
            "rapidcow",
            "scenetime",
            "topaz",
            "yol0",
        }
    ),
    # Asian Trackers & Encoders (Chinese / Korean / Japanese)
    frozenset(
        {
            "mteam",
            "frds",
            "beast",
            "cmct",
            "chdbits",
            "pter",
            "ourbits",
            "hdsky",
            "lemon",
            "cine21",
            "appletor",
            "noma",
            "limo",
            "next",
            "hevc-dada",
            "totheglory",
            "ttg",
        }
    ),
    # Romanian & Eastern European Trackers (FileList, SuperBits, SpeedApp, Rutracker)
    frozenset(
        {
            "playhd",
            "rosub",
            "flshare",
            "speed",
            "superbits",
            "hdclub",
            "rutracker",
            "lostfilm",
            "newstudio",
            "hdrezka",
            "alexfilm",
            "baibako",
            "kuraj",
        }
    ),
    # European / Multi-language Scene & Encoders (French / German / Spanish / Italian)
    frozenset(
        {
            "vostfr",
            "multi",
            "jmt",
            "zt",
            "extreme",
            "titans",
            "waf",
            "tft",
            "fhd",
            "hdt",
            "cinexvid",
            "cine-hd",
            "hdlight",
            "popcon",
            "hd-area",
            "tscc",
        }
    ),
]

_GROUP_ALIASES: dict[str, frozenset[str]] = {}
for _cluster in _GROUP_CLUSTERS:
    for _member in _cluster:
        _GROUP_ALIASES[_member] = _cluster

_SOURCE_CLUSTERS: tuple[frozenset[str], ...] = (
    frozenset({"blu-ray", "bluray", "bdrip", "brrip", "uhd-bluray"}),
    frozenset({"web-dl", "webdl", "webrip", "web"}),
    frozenset({"hdtv", "pdtv", "dsr"}),
    frozenset({"dvd", "dvdrip"}),
    frozenset({"remux", "bdremux"}),
)

_SOURCE_ALIASES: dict[str, frozenset[str]] = {}
for _src_cluster in _SOURCE_CLUSTERS:
    for _src_member in _src_cluster:
        _SOURCE_ALIASES[_src_member] = _src_cluster

_CODEC_CLUSTERS: tuple[frozenset[str], ...] = (
    frozenset({"h.264", "h264", "x264", "avc"}),
    frozenset({"h.265", "h265", "x265", "hevc"}),
    frozenset({"xvid", "divx"}),
    frozenset({"av1"}),
)

_CODEC_ALIASES: dict[str, frozenset[str]] = {}
for _codec_cluster in _CODEC_CLUSTERS:
    for _codec_member in _codec_cluster:
        _CODEC_ALIASES[_codec_member] = _codec_cluster

_GENERIC_MEDIA_TITLES: frozenset[str] = frozenset(
    {
        "cd1",
        "cd2",
        "movie",
        "video",
        "film",
        "feature",
        "sample",
        "disk1",
        "disk2",
        "part1",
        "part2",
    }
)


def _extract_metadata_from_guess(guess: dict[str, Any], fallback_title: str) -> dict[str, Any]:
    raw_title = guess.get("title", fallback_title)
    if isinstance(raw_title, list):
        title = " ".join(str(title_token) for title_token in raw_title if title_token)
    else:
        title = str(raw_title)

    year = guess.get("year")
    if isinstance(year, list) and year:
        year = int(year[0])
    elif isinstance(year, (int, str)) and str(year).isdigit():
        year = int(year)
    else:
        year = None

    season = guess.get("season")
    if isinstance(season, list) and season:
        season = int(season[0])
    elif isinstance(season, (int, str)) and str(season).isdigit():
        season = int(season)
    else:
        season = None

    episode = guess.get("episode")
    if isinstance(episode, list) and episode:
        episode = int(episode[0])
    elif isinstance(episode, (int, str)) and str(episode).isdigit():
        episode = int(episode)
    else:
        episode = None

    is_episode = guess.get("type") == "episode" or (season is not None and episode is not None)

    if year and title.lower().endswith(str(year)):
        title = title[: -len(str(year))].strip(" -._")

    part = guess.get("part")
    if part and not is_episode:
        part_str = str(part)
        if f"part {part_str}" not in title.lower() and f"part.{part_str}" not in title.lower():
            title = f"{title} Part {part_str}"

    release_group = guess.get("release_group")
    if isinstance(release_group, list):
        release_group = str(release_group[0])
    elif release_group:
        release_group = str(release_group)

    screen_size = str(guess.get("screen_size")) if guess.get("screen_size") else None
    source = str(guess.get("format") or guess.get("source") or "") or None
    video_codec = str(guess.get("video_codec")) if guess.get("video_codec") else None
    audio_codec = str(guess.get("audio_codec")) if guess.get("audio_codec") else None

    return {
        "title": title,
        "year": year,
        "season": season,
        "episode": episode,
        "is_episode": is_episode,
        "release_group": release_group,
        "screen_size": screen_size,
        "source": source,
        "video_codec": video_codec,
        "audio_codec": audio_codec,
    }


def _parse_sexagesimal_seconds(duration_str: str) -> float | None:
    parts = duration_str.strip().split(":")
    try:
        if len(parts) == 3:
            return float(parts[0]) * 3600.0 + float(parts[1]) * 60.0 + float(parts[2])
        if len(parts) == 2:
            return float(parts[0]) * 60.0 + float(parts[1])
        return float(duration_str)
    except (ValueError, TypeError):
        return None


def get_video_duration(video_path: Path | str) -> float | None:
    """Extract total video duration in seconds using ffprobe across MP4, MKV, and AVI containers."""
    path = Path(video_path).resolve()
    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin or not path.is_file():
        return None

    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "format=duration:stream=duration:stream_tags=DURATION,DURATION-eng",
        "-of",
        "json",
        str(path),
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        payload = orjson.loads(proc.stdout)
    except (subprocess.SubprocessError, orjson.JSONDecodeError, OSError):
        return None

    format_dur = payload.get("format", {}).get("duration")
    if format_dur and format_dur != "N/A":
        try:
            return float(format_dur)
        except ValueError:
            pass

    streams = payload.get("streams", [])
    if streams and isinstance(streams, list):
        v_stream = streams[0]
        stream_dur = v_stream.get("duration")
        if stream_dur and stream_dur != "N/A":
            try:
                return float(stream_dur)
            except ValueError:
                pass

        tags = v_stream.get("tags") or {}
        tag_dur = tags.get("DURATION") or tags.get("DURATION-eng")
        if tag_dur:
            parsed = _parse_sexagesimal_seconds(str(tag_dur))
            if parsed is not None:
                return parsed

    return None


def get_subtitle_max_timestamp(subtitle_content: str | bytes) -> float:
    """Return maximum end timestamp in seconds across all cues in subtitle text, or 0.0 if empty."""
    if isinstance(subtitle_content, bytes):
        try:
            text = subtitle_content.decode("utf-8", errors="replace")
        except UnicodeDecodeError:
            return 0.0
    else:
        text = subtitle_content

    if not text.strip():
        return 0.0

    try:
        return max(
            (cue.end.total_seconds() for cue in srt.parse(text)),
            default=0.0,
        )
    except (srt.SRTParseError, ValueError, TypeError):
        return 0.0


def validate_subtitle_timing(
    subtitle_max_timestamp: float,
    video_duration: float | None,
    tolerance_seconds: float = MAX_SUBTITLE_DURATION_TOLERANCE_SECONDS,
) -> bool:
    """Validate subtitle timing against video container duration to prevent cross-movie collisions.

    Returns True if valid or if video duration is unknown.
    Returns False if subtitle terminates drastically early or extends past video duration.
    """
    if video_duration is None or video_duration <= 0.0:
        return True

    if subtitle_max_timestamp <= 0.0:
        return False

    discrepancy = subtitle_max_timestamp - video_duration
    if discrepancy > tolerance_seconds:
        return False

    min_acceptable_timestamp = max(
        video_duration * MIN_SUBTITLE_DURATION_RATIO,
        video_duration - 900.0,
    )
    if subtitle_max_timestamp < min_acceptable_timestamp:
        return False

    return True


def evaluate_local_subtitle_score(srt_path: Path, video_meta: VideoMetadata) -> float:
    """Evaluate quality score of an existing local companion subtitle for downgrade protection."""
    if not srt_path.is_file() or srt_path.stat().st_size == 0:
        return 0.0

    score = 75.0

    stem_lower = srt_path.stem.lower()
    if any(
        tag in stem_lower
        for tag in ("retail", "bluray", "web-dl", "netflix", "amazon", "ffsubsync", "alass")
    ):
        score += 15.0

    try:
        content_sample = srt_path.read_bytes()[:65536].decode("utf-8", errors="replace").lower()
        if any(
            marker in content_sample
            for marker in ("machine translated", "google translate", "deepl")
        ):
            score -= 50.0
        elif any(
            marker in content_sample
            for marker in ("retail", "blu-ray", "bluray", "subrip", "rosub")
        ):
            score += 10.0

        if video_meta.duration and video_meta.duration > 0.0:
            max_ts = get_subtitle_max_timestamp(content_sample)
            if validate_subtitle_timing(max_ts, video_meta.duration):
                score += 10.0
    except OSError:
        pass

    return max(0.0, score)


def parse_video_metadata(
    video_path: str | Path,
    compute_hash: bool = True,
    extract_duration: bool = False,
) -> VideoMetadata:
    """Extract structured media metadata from video filename and parent directory.

    Args:
        video_path: Path to target video file.
        compute_hash: Whether to calculate the 64-bit OpenSubtitles hash.
        extract_duration: Whether to extract video duration via ffprobe.

    Returns:
        Populated VideoMetadata object.

    Raises:
        FileNotFoundError: If the video file doesn't exist.
    """
    path = Path(video_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {path}")

    filename = path.name
    file_size = path.stat().st_size
    guess: dict[str, Any] = dict(guessit(filename))
    extracted = _extract_metadata_from_guess(guess, path.stem)

    parent_folder = path.parent.name
    if parent_folder and parent_folder.lower() not in IGNORED_DIRS:
        parent_guess: dict[str, Any] = dict(guessit(parent_folder))
        parent_extracted = _extract_metadata_from_guess(parent_guess, parent_folder)

        extracted_title = extracted["title"]
        if not extracted_title or extracted_title.lower() in _GENERIC_MEDIA_TITLES:
            if (
                parent_extracted["title"]
                and parent_extracted["title"].lower() not in _GENERIC_MEDIA_TITLES
            ):
                extracted["title"] = parent_extracted["title"]
        if extracted["year"] is None and parent_extracted["year"] is not None:
            extracted["year"] = parent_extracted["year"]
        if not extracted["release_group"] and parent_extracted["release_group"]:
            extracted["release_group"] = parent_extracted["release_group"]

    moviehash: str | None = None
    if compute_hash:
        try:
            moviehash = calculate_movie_hash(path)
        except (ValueError, OSError) as exc:
            LOG.debug(f"Could not calculate hash for {filename}: {exc}")

    duration: float | None = None
    if extract_duration:
        duration = get_video_duration(path)

    return VideoMetadata(
        file_path=path,
        title=extracted["title"],
        year=extracted["year"],
        season=extracted["season"],
        episode=extracted["episode"],
        release_group=extracted["release_group"],
        screen_size=extracted["screen_size"],
        source=extracted["source"],
        video_codec=extracted["video_codec"],
        audio_codec=extracted["audio_codec"],
        is_episode=extracted["is_episode"],
        moviehash=moviehash,
        file_size=file_size,
        duration=duration,
    )


def parse_directory_metadata(dir_path: str | Path) -> VideoMetadata:
    """Extract structured media metadata from a directory or folder name.

    Args:
        dir_path: Path to target directory.

    Returns:
        Populated VideoMetadata object for the directory.

    Raises:
        NotADirectoryError: If the path is not a directory.
    """
    path = Path(dir_path).resolve()
    if not path.is_dir():
        raise NotADirectoryError(f"Directory not found: {path}")

    guess: dict[str, Any] = dict(guessit(path.name))
    extracted = _extract_metadata_from_guess(guess, path.name)

    return VideoMetadata(
        file_path=path,
        title=extracted["title"],
        year=extracted["year"],
        season=extracted["season"],
        episode=extracted["episode"],
        release_group=extracted["release_group"],
        screen_size=extracted["screen_size"],
        source=extracted["source"],
        video_codec=extracted["video_codec"],
        audio_codec=extracted["audio_codec"],
        is_episode=extracted["is_episode"],
        moviehash=None,
        file_size=0,
    )


def score_subtitle_candidate(
    video_meta: VideoMetadata,
    release_name: str,
    matched_by_hash: bool = False,
    downloads: int | None = None,
) -> float:
    """Calculate candidate relevance score using rapidfuzz string matching.

    Exact hash matches receive top priority (score >= 100).
    Applies strict gatekeeping and penalties for short titles (<= 5 characters)
    to prevent false-positive fuzzy collisions.
    """
    download_bonus = min(float(downloads or 0) / 100.0, SCORE_MAX_DOWNLOAD_BONUS)

    if matched_by_hash:
        return SCORE_HASH_MATCH_BASE + download_bonus

    rel_lower = release_name.lower()

    # Short-string gatekeeper for titles <= 5 characters (e.g. Up, Her, It, 9, Abe, Coda, Hero)
    clean_title = video_meta.title.strip().lower()
    if clean_title and len(clean_title) <= SHORT_TITLE_MAX_LENGTH:
        boundary_pattern = rf"(?i)(?<![a-z0-9]){re.escape(clean_title)}(?![a-z0-9])"
        if not re.search(boundary_pattern, rel_lower):
            return 0.0

        # Anchor check: stem before the short title must not contain unrelated movie title words
        stem_without_brackets = re.sub(r"^\[[^\]]+\]\s*", "", rel_lower).strip()
        match_anchor = re.search(boundary_pattern, stem_without_brackets)
        if match_anchor:
            prefix = stem_without_brackets[: match_anchor.start()].strip(" ._-")
            prefix_tokens = [
                t for t in re.findall(r"[a-z0-9]+", prefix) if t not in ("the", "a", "an")
            ]
            if prefix_tokens:
                return 0.0

        # Short title release year alignment: 4-digit year must match video_meta.year
        if video_meta.year:
            candidate_years = [int(y) for y in re.findall(r"\b(19\d\d|20\d\d)\b", rel_lower)]
            if candidate_years and video_meta.year not in candidate_years:
                return 0.0

    # General release year consistency for movies: reject candidates with conflicting release years
    if video_meta.year and not video_meta.is_episode:
        all_candidate_years = [int(y) for y in re.findall(r"\b(19\d\d|20\d\d)\b", rel_lower)]
        if all_candidate_years and video_meta.year not in all_candidate_years:
            return 0.0

    score = SCORE_BASE

    if video_meta.release_group:
        target_release_group = video_meta.release_group.lower().strip()
        aliases: frozenset[str] | None = _GROUP_ALIASES.get(target_release_group)
        if aliases is None:
            for member, cluster in _GROUP_ALIASES.items():
                if member in target_release_group:
                    aliases = cluster
                    break

        if aliases and any(alias in rel_lower for alias in aliases):
            score += SCORE_RELEASE_GROUP_WEIGHT
        else:
            token_words = set(re.findall(r"[a-z0-9]+", rel_lower))
            if target_release_group in token_words:
                score += SCORE_RELEASE_GROUP_WEIGHT
            else:
                grp_ratio = fuzz.partial_ratio(target_release_group, rel_lower)
                if len(target_release_group) >= 4 and grp_ratio >= SCORE_RELEASE_GROUP_THRESHOLD:
                    score += SCORE_RELEASE_GROUP_WEIGHT * (grp_ratio / 100.0)

    if video_meta.screen_size and video_meta.screen_size.lower() in rel_lower:
        score += SCORE_RESOLUTION_MATCH

    if video_meta.source:
        target_source = video_meta.source.lower().strip()
        source_aliases = _SOURCE_ALIASES.get(target_source)
        if (source_aliases and any(alias in rel_lower for alias in source_aliases)) or (
            target_source in rel_lower
        ):
            score += SCORE_SOURCE_MATCH

    if video_meta.video_codec:
        target_codec = video_meta.video_codec.lower().strip()
        codec_aliases = _CODEC_ALIASES.get(target_codec)
        if (codec_aliases and any(alias in rel_lower for alias in codec_aliases)) or (
            target_codec in rel_lower
        ):
            score += SCORE_CODEC_MATCH

    score += download_bonus
    return score


def has_existing_subtitle(video_path: str | Path, language: str | None = None) -> Path | None:
    resolved_video_path = Path(video_path).resolve()
    parent_dir = resolved_video_path.parent
    video_stem = resolved_video_path.stem

    candidates: list[Path] = [
        parent_dir / f"{video_stem}.srt",
        parent_dir / f"{video_stem}.default.srt",
        parent_dir / f"{video_stem}.sdh.srt",
        parent_dir / f"{video_stem}.forced.srt",
    ]
    if language:
        lang_clean = normalize_language(language)
        candidates.extend(
            [
                parent_dir / f"{video_stem}.{lang_clean}.srt",
                parent_dir / f"{video_stem}.{lang_clean}.sdh.srt",
                parent_dir / f"{video_stem}.{lang_clean}.forced.srt",
                parent_dir / f"{video_stem}.{lang_clean}.default.srt",
            ]
        )
        lang_3b = normalize_language_alpha3(lang_clean, bibliographic=True)
        if lang_3b and lang_3b != lang_clean:
            candidates.extend(
                [
                    parent_dir / f"{video_stem}.{lang_3b}.srt",
                    parent_dir / f"{video_stem}.{lang_3b}.sdh.srt",
                    parent_dir / f"{video_stem}.{lang_3b}.forced.srt",
                ]
            )

    for candidate_path in candidates:
        if candidate_path.is_file() and candidate_path.stat().st_size > 0:
            return candidate_path

    for sub_ext in SUBTITLE_EXTENSIONS:
        potential_subtitle_path = parent_dir / f"{video_stem}{sub_ext}"
        if potential_subtitle_path.is_file() and potential_subtitle_path.stat().st_size > 0:
            return potential_subtitle_path

    return None


def find_video_files(path: str | Path, min_size: int = 0) -> list[Path]:
    resolved_search_path = Path(path).resolve()
    if not resolved_search_path.exists():
        raise FileNotFoundError(f"Path does not exist: {resolved_search_path}")

    if resolved_search_path.is_file():
        return [resolved_search_path]

    video_files: list[Path] = []
    str_path = str(resolved_search_path)

    for root, dirs, files in os.walk(str_path, topdown=True):
        dirs[:] = [
            directory_name
            for directory_name in dirs
            if not directory_name.startswith(".") and directory_name.lower() not in IGNORED_DIRS
        ]

        for fname in files:
            if fname.startswith("."):
                continue
            _, ext = os.path.splitext(fname)
            if ext.lower() in SUPPORTED_VIDEO_EXTS:
                full_path = Path(root) / fname
                try:
                    if min_size > 0 and full_path.stat().st_size < min_size:
                        continue
                except OSError:
                    continue
                video_files.append(full_path)

    video_files.sort(key=lambda video_file_path: str(video_file_path).lower())
    return video_files
