from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import srt
from babelfish import Error as BabelfishError
from babelfish import Language
from charset_normalizer import from_bytes
from guessit import guessit
from rapidfuzz import fuzz

from cinesub.core.constants import (
    IGNORED_DIRS,
    SCORE_BASE,
    SCORE_CODEC_MATCH,
    SCORE_HASH_MATCH_BASE,
    SCORE_MAX_DOWNLOAD_BONUS,
    SCORE_RELEASE_GROUP_THRESHOLD,
    SCORE_RELEASE_GROUP_WEIGHT,
    SCORE_RESOLUTION_MATCH,
    SCORE_SOURCE_MATCH,
    SUBTITLE_EXTENSIONS,
    SUPPORTED_VIDEO_EXTS,
)
from cinesub.core.hasher import calculate_movie_hash
from cinesub.core.logger import LOG
from cinesub.core.models import VideoMetadata


def normalize_language(lang: str) -> str:
    """Normalize language code or name to standard 2-letter ISO 639-1 code."""
    cleaned = lang.strip().lower()
    converters = (Language.fromalpha2, Language.fromalpha3b, Language.fromname)
    for converter in converters:
        try:
            return str(converter(cleaned).alpha2)
        except (ValueError, LookupError, AttributeError, BabelfishError):
            pass

    return cleaned[:2]


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


def parse_video_metadata(video_path: str | Path, compute_hash: bool = True) -> VideoMetadata:
    """Extract structured video metadata using guessit and calculate the video hash.

    Args:
        video_path: Path to target video file.
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
    """Calculate candidate relevance score using rapidfuzz string matching.

    Exact hash matches receive top priority (score >= 100).
    """
    download_bonus = min(float(downloads or 0) / 100.0, SCORE_MAX_DOWNLOAD_BONUS)

    if matched_by_hash:
        return SCORE_HASH_MATCH_BASE + download_bonus

    score = SCORE_BASE
    rel_lower = release_name.lower()

    if video_meta.release_group:
        v_grp = video_meta.release_group.lower().strip()
        aliases: frozenset[str] | None = _GROUP_ALIASES.get(v_grp)
        if aliases is None:
            for member, cluster in _GROUP_ALIASES.items():
                if member in v_grp:
                    aliases = cluster
                    break

        if aliases and any(alias in rel_lower for alias in aliases):
            score += SCORE_RELEASE_GROUP_WEIGHT
        else:
            grp_ratio = fuzz.partial_ratio(v_grp, rel_lower)
            if grp_ratio >= SCORE_RELEASE_GROUP_THRESHOLD:
                score += SCORE_RELEASE_GROUP_WEIGHT * (grp_ratio / 100.0)

    if video_meta.screen_size and video_meta.screen_size.lower() in rel_lower:
        score += SCORE_RESOLUTION_MATCH

    if video_meta.source and video_meta.source.lower() in rel_lower:
        score += SCORE_SOURCE_MATCH

    if video_meta.video_codec and video_meta.video_codec.lower() in rel_lower:
        score += SCORE_CODEC_MATCH

    score += download_bonus
    return score


def has_existing_subtitle(video_path: str | Path, language: str | None = None) -> Path | None:
    p = Path(video_path).resolve()
    parent = p.parent
    stem = p.stem

    candidates: list[Path] = [
        parent / f"{stem}.srt",
        parent / f"{stem}.default.srt",
        parent / f"{stem}.sdh.srt",
        parent / f"{stem}.forced.srt",
    ]
    if language:
        lang_clean = normalize_language(language)
        candidates.extend(
            [
                parent / f"{stem}.{lang_clean}.srt",
                parent / f"{stem}.{lang_clean}.sdh.srt",
                parent / f"{stem}.{lang_clean}.forced.srt",
                parent / f"{stem}.{lang_clean}.default.srt",
            ]
        )
        try:
            lang_3b = str(Language.fromalpha2(lang_clean).alpha3b)
            candidates.extend(
                [
                    parent / f"{stem}.{lang_3b}.srt",
                    parent / f"{stem}.{lang_3b}.sdh.srt",
                    parent / f"{stem}.{lang_3b}.forced.srt",
                ]
            )
        except (ValueError, LookupError, AttributeError, BabelfishError):
            pass

    for cand in candidates:
        if cand.is_file() and cand.stat().st_size > 0:
            return cand

    for sub_ext in SUBTITLE_EXTENSIONS:
        ext_p = parent / f"{stem}{sub_ext}"
        if ext_p.is_file() and ext_p.stat().st_size > 0:
            return ext_p

    return None


def find_video_files(path: str | Path, min_size: int = 0) -> list[Path]:
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Path does not exist: {p}")

    if p.is_file():
        return [p]

    video_files: list[Path] = []
    str_path = str(p)

    for root, dirs, files in os.walk(str_path, topdown=True):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in IGNORED_DIRS]

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

    video_files.sort(key=lambda x: str(x).lower())
    return video_files
