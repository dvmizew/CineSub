"""CineSub - Subtitle search, download, and media management CLI."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cinesub")
except PackageNotFoundError:
    __version__ = "0.2.0"

from cinesub.modules.downloader import download_batch, download_bulk, download_subtitle

__all__ = [
    "__version__",
    "download_batch",
    "download_bulk",
    "download_subtitle",
]
