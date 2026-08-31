from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class VideoMetadata:
    file_path: Path
    title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    release_group: str | None = None
    screen_size: str | None = None
    source: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    is_episode: bool = False
    moviehash: str | None = None
    file_size: int = 0


@dataclass
class SubtitleMatch:
    id: str
    provider: str
    language: str
    release_name: str
    matched_by_hash: bool = False
    download_url: str | None = None
    file_id: str | int | None = None
    rating: float | None = None
    download_count: int | None = None
    hearing_impaired: bool = False
    fps: float | str | None = None
    score: float = 0.0


@dataclass
class SyncResult:
    success: bool
    video_path: Path
    srt_path: Path
    offset_seconds: float | None = None
    framerate_scale: float | None = None
    message: str = ""
