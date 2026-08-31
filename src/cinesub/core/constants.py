from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

USER_AGENT = os.getenv("OPENSUBTITLES_USER_AGENT", "CineSub v0.2.0")
DEFAULT_LANGUAGE = os.getenv("CINESUB_DEFAULT_LANGUAGE", "en")
DEFAULT_TIMEOUT = int(os.getenv("CINESUB_TIMEOUT", "15"))

OPENSUBTITLES_API_URL = "https://api.opensubtitles.com/api/v1"
SUBDL_API_URL = "https://api.subdl.com/api/v1"
SUBDL_DL_URL = "https://dl.subdl.com"

SUPPORTED_VIDEO_EXTS = {
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".wmv",
    ".m4v",
    ".webm",
    ".flv",
    ".ts",
}

# 64KB chunk size for OpenSubtitles hash
HASH_CHUNK_SIZE = 65536
MIN_HASH_FILE_SIZE = HASH_CHUNK_SIZE * 2
