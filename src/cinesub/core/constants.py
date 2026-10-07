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
SUBSOURCE_API_URL = "https://api.subsource.net/api/v1"
SUBSRO_API_URL = "https://api.subs.ro/v1.0"
BETASERIES_API_URL = "https://api.betaseries.com"
TMDB_API_URL = "https://api.themoviedb.org/3"
GESTDOWN_API_URL = "https://api.gestdown.info"
BSPLAYER_API_URL = "http://s1.api.bsplayer-subtitles.com/v1.php"
ANIMETOSHO_FEED_URL = "https://feed.animetosho.org/json"
ANIMETOSHO_STORAGE_URL = "https://storage.animetosho.org/attach"
ASSRT_API_URL = "https://api.assrt.net/v1"
ASSRT_FALLBACK_URL = "https://api.makedie.me/v1"

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
        ".mts",
        ".vob",
        ".ogv",
        ".mpg",
        ".mpeg",
        ".m2v",
        ".rmvb",
        ".rm",
        ".divx",
        ".3gp",
        ".3g2",
        ".asf",
        ".f4v",
        ".wtv",
        ".dvr-ms",
    }
)

IGNORED_DIRS = frozenset(
    {
        "@eadir",
        "#recycle",
        ".recycle",
        ".trash",
        ".trash-1000",
        ".plex",
        ".git",
        ".venv",
        ".idea",
        ".vscode",
        "lost+found",
        "node_modules",
        "extras",
        "featurettes",
        "trailers",
        "behind the scenes",
        "deleted scenes",
        "shorts",
        "interviews",
        "scenes",
        "samples",
        "subs",
        "subtitles",
    }
)

SUBTITLE_EXTENSIONS = (".srt", ".vtt", ".sub", ".ass", ".ssa", ".idx", ".smi")
MIN_VIDEO_SCAN_SIZE = 10 * 1024 * 1024

GZIP_MAGIC_BYTES = b"\x1f\x8b"
ZIP_MAGIC_BYTES = b"PK\x03\x04"
XZ_MAGIC_BYTES = b"\xfd7zXZ\x00"

HASH_CHUNK_SIZE = 65536
MIN_HASH_FILE_SIZE = HASH_CHUNK_SIZE * 2

SCORE_HASH_MATCH_BASE = 100.0
SCORE_BASE = 10.0
SCORE_RELEASE_GROUP_WEIGHT = 40.0
SCORE_RELEASE_GROUP_THRESHOLD = 80.0
SCORE_RESOLUTION_MATCH = 15.0
SCORE_SOURCE_MATCH = 15.0
SCORE_CODEC_MATCH = 10.0
SCORE_MAX_DOWNLOAD_BONUS = 10.0

SHORT_TITLE_MAX_LENGTH = 5
SCORE_SHORT_TITLE_PENALTY = 40.0
UPGRADE_HYSTERESIS_DELTA = 5.0
MAX_SUBTITLE_DURATION_TOLERANCE_SECONDS = 15.0
MIN_SUBTITLE_DURATION_RATIO = 0.70
