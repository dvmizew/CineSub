"""CineSub - Subtitle search, download, and audio synchronization."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cinesub")
except PackageNotFoundError:
    __version__ = "0.2.0"

from cinesub.modules.downloader import download_and_sync, download_bulk
from cinesub.modules.syncer import sync_subtitle_audio

__all__ = [
    "__version__",
    "download_and_sync",
    "download_bulk",
    "sync_subtitle_audio",
]
