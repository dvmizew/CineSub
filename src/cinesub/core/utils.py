from __future__ import annotations

from pathlib import Path
from typing import Any

from babelfish import Language
from guessit import guessit
from rapidfuzz import fuzz

from cinesub.core.hasher import calculate_movie_hash
from cinesub.core.logger import LOG
from cinesub.core.models import VideoMetadata


def normalize_language(lang: str) -> str:
    """Normalize a language code or name to a standard 2-letter ISO 639-1 code.

    Examples:
        'ro', 'rum', 'ron', 'romanian' -> 'ro'
        'en', 'eng', 'english' -> 'en'
    """
    cleaned = lang.strip().lower()
    for converter in (Language.fromalpha2, Language.fromalpha3b, Language.fromname):
        try:
            return str(converter(cleaned).alpha2)
        except (ValueError, LookupError, AttributeError):
            pass

    # Common manual fallback mappings
    fallbacks: dict[str, str] = {
        "ro": "ro",
        "rum": "ro",
        "ron": "ro",
        "romanian": "ro",
        "en": "en",
        "eng": "en",
        "english": "en",
        "es": "es",
        "spa": "es",
        "spanish": "es",
        "fr": "fr",
        "fre": "fr",
        "fra": "fr",
        "french": "fr",
        "de": "de",
        "ger": "de",
        "deu": "de",
        "german": "de",
        "it": "it",
        "ita": "it",
        "italian": "it",
    }
    return fallbacks.get(cleaned, cleaned[:2])


def decode_and_normalize_subtitle_content(raw_bytes: bytes) -> bytes:
    """Decode raw subtitle bytes from various encodings and re-encode as clean UTF-8.

    Handles UTF-8 with/without BOM, CP1250 (Central/Eastern Europe/Romania),
    CP1252 (Western), ISO-8859-1, and ISO-8859-2.
    """
    if not raw_bytes:
        return b""

    # Candidate encodings in order of priority
    candidates = [
        "utf-8-sig",
        "utf-8",
        "cp1250",
        "cp1252",
        "iso-8859-16",
        "iso-8859-2",
        "iso-8859-1",
    ]
    for encoding in candidates:
        try:
            decoded_text = raw_bytes.decode(encoding)
            return decoded_text.encode("utf-8")
        except UnicodeDecodeError:
            continue

    # Fallback to UTF-8 with replacement for any unmappable byte sequences
    return raw_bytes.decode("utf-8", errors="replace").encode("utf-8")


def parse_video_metadata(video_path: str | Path, compute_hash: bool = True) -> VideoMetadata:
    """Extract structured video metadata using guessit and calculate the video hash.

    Args:
        video_path: Path to the target video file.
        compute_hash: Whether to calculate the 64-bit OpenSubtitles hash.

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

    raw_title = guess.get("title", path.stem)
    if isinstance(raw_title, list):
        title = " ".join(str(t) for t in raw_title if t)
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

    release_group = guess.get("release_group")
    if isinstance(release_group, list):
        release_group = str(release_group[0])
    elif release_group:
        release_group = str(release_group)

    screen_size = str(guess.get("screen_size")) if guess.get("screen_size") else None
    source = str(guess.get("format") or guess.get("source") or "") or None
    video_codec = str(guess.get("video_codec")) if guess.get("video_codec") else None
    audio_codec = str(guess.get("audio_codec")) if guess.get("audio_codec") else None

    moviehash: str | None = None
    if compute_hash:
        try:
            moviehash = calculate_movie_hash(path)
        except (ValueError, OSError) as exc:
            LOG.debug(f"Could not calculate hash for {filename}: {exc}")

    return VideoMetadata(
        file_path=path,
        title=title,
        year=year,
        season=season,
        episode=episode,
        release_group=release_group,
        screen_size=screen_size,
        source=source,
        video_codec=video_codec,
        audio_codec=audio_codec,
        is_episode=is_episode,
        moviehash=moviehash,
        file_size=file_size,
    )


def score_subtitle_candidate(
    video_meta: VideoMetadata,
    release_name: str,
    matched_by_hash: bool = False,
    downloads: int | None = None,
) -> float:
    """Calculate candidate score using rapidfuzz string matching.

    Exact hash matches receive top priority (score >= 100).
    """
    if matched_by_hash:
        return 100.0 + (min(downloads or 0, 1000) / 100.0)

    score = 10.0
    rel_lower = release_name.lower()

    # RapidFuzz release group matching
    if video_meta.release_group:
        grp_ratio = fuzz.partial_ratio(video_meta.release_group.lower(), rel_lower)
        if grp_ratio > 80:
            score += 40.0 * (grp_ratio / 100.0)

    # Resolution match
    if video_meta.screen_size and video_meta.screen_size.lower() in rel_lower:
        score += 15.0

    # Source match (e.g., BluRay, WEBRip)
    if video_meta.source and video_meta.source.lower() in rel_lower:
        score += 15.0

    # Video Codec match
    if video_meta.video_codec and video_meta.video_codec.lower() in rel_lower:
        score += 10.0

    if downloads:
        score += min(downloads / 100.0, 10.0)

    return score


def find_video_files(path: str | Path) -> list[Path]:
    """Discover video files from a file path or directory recursively.

    Args:
        path: Path to a single video file or directory.

    Returns:
        Sorted list of matching Path objects.
    """
    from cinesub.core.constants import SUPPORTED_VIDEO_EXTS

    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Path does not exist: {p}")

    if p.is_file():
        return [p]

    video_files: list[Path] = []
    for item in p.rglob("*"):
        if item.is_file() and not item.name.startswith("."):
            if item.suffix.lower() in SUPPORTED_VIDEO_EXTS:
                video_files.append(item)

    video_files.sort(key=lambda x: str(x).lower())
    return video_files
