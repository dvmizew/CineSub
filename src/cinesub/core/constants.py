from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

APP_NAME = "CineSub"
APP_VERSION = "0.2.0"

USER_AGENT = os.getenv("OPENSUBTITLES_USER_AGENT", f"{APP_NAME} v{APP_VERSION}")
DEFAULT_LANGUAGE = os.getenv("CINESUB_DEFAULT_LANGUAGE", "en")
DEFAULT_TIMEOUT = int(os.getenv("CINESUB_TIMEOUT", "15"))

OPENSUBTITLES_API_URL = "https://api.opensubtitles.com/api/v1"
SUBDL_API_URL = "https://api.subdl.com/api/v1"
SUBDL_DL_URL = "https://dl.subdl.com"

SUPPORTED_VIDEO_EXTS = frozenset(
    {
        ".mp4",
        ".mkv",
        ".avi",
        ".mov",
        ".wmv",
        ".m4v",
        ".webm",
        ".flv",
        ".ts",
        ".m2ts",
        ".vob",
        ".ogv",
    }
)

SUBTITLE_EXTENSIONS = (".srt", ".vtt", ".sub", ".ass")

SUBTITLE_ENCODINGS: tuple[str, ...] = (
    "utf-8-sig",
    "utf-8",
    "cp1250",
    "cp1252",
    "iso-8859-16",
    "iso-8859-2",
    "iso-8859-1",
)

# Magic bytes for archive detection
GZIP_MAGIC_BYTES = b"\x1f\x8b"
ZIP_MAGIC_BYTES = b"PK\x03\x04"

# 64KB chunk size for OpenSubtitles 64-bit checksum calculation
HASH_CHUNK_SIZE = 65536
MIN_HASH_FILE_SIZE = HASH_CHUNK_SIZE * 2

# Heuristic scoring weights
SCORE_HASH_MATCH_BASE = 100.0
SCORE_BASE = 10.0
SCORE_RELEASE_GROUP_WEIGHT = 40.0
SCORE_RELEASE_GROUP_THRESHOLD = 80.0
SCORE_RESOLUTION_MATCH = 15.0
SCORE_SOURCE_MATCH = 15.0
SCORE_CODEC_MATCH = 10.0
SCORE_MAX_DOWNLOAD_BONUS = 10.0
