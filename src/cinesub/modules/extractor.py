from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

from cinesub.core.logger import LOG
from cinesub.core.utils import (
    decode_and_normalize_subtitle_content,
    find_video_files,
    normalize_language,
)

TEXT_SUBTITLE_CODECS: frozenset[str] = frozenset(
    {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}
)


@dataclass
class EmbeddedSubtitleTrack:
    stream_index: int
    codec_name: str
    language: str
    title: str | None = None
    is_forced: bool = False
    is_default: bool = False
    is_sdh: bool = False

    @property
    def is_text_based(self) -> bool:
        return self.codec_name.lower() in TEXT_SUBTITLE_CODECS


def inspect_embedded_subtitles(video_path: str | Path) -> list[EmbeddedSubtitleTrack]:
    """Inspect video container for embedded subtitle streams using ffprobe."""
    path = Path(video_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {path}")

    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin:
        raise RuntimeError("ffprobe is not installed or not found in system PATH.")

    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-select_streams",
        "s",
        "-show_entries",
        "stream=index,codec_name,disposition:stream_tags=language,title",
        "-of",
        "json",
        str(path),
    ]

    try:
        probe_process = subprocess.run(cmd, capture_output=True, text=True, check=True)
        probe_payload = orjson.loads(probe_process.stdout)
    except (subprocess.CalledProcessError, orjson.JSONDecodeError) as exc:
        LOG.debug(f"ffprobe stream inspection failed for {path.name}: {exc}")
        return []

    streams = probe_payload.get("streams", [])
    if not isinstance(streams, list):
        return []

    tracks: list[EmbeddedSubtitleTrack] = []
    for s in streams:
        raw_idx = s.get("index")
        if raw_idx is None:
            continue

        codec = str(s.get("codec_name") or "unknown").lower()
        tags = s.get("tags") or {}
        raw_lang = tags.get("language") or "und"
        norm_lang = normalize_language(str(raw_lang)) if raw_lang != "und" else "und"
        stream_title = tags.get("title")

        disposition = s.get("disposition") or {}
        is_forced = bool(disposition.get("forced", 0))
        is_default = bool(disposition.get("default", 0))
        is_sdh = bool(disposition.get("hearing_impaired", 0)) or (
            "sdh" in (stream_title or "").lower()
        )

        tracks.append(
            EmbeddedSubtitleTrack(
                stream_index=int(raw_idx),
                codec_name=codec,
                language=norm_lang,
                title=stream_title,
                is_forced=is_forced,
                is_default=is_default,
                is_sdh=is_sdh,
            )
        )

    return tracks


def extract_track_to_srt(
    video_path: Path,
    track: EmbeddedSubtitleTrack,
    target_path: Path,
) -> Path:
    """Extract a single text-based embedded subtitle stream and save as UTF-8 SRT."""
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("ffmpeg is not installed or not found in system PATH.")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target_path.parent / f".{target_path.name}.{os.getpid()}.tmp"

    cmd = [
        ffmpeg_bin,
        "-y",
        "-nostdin",
        "-i",
        str(video_path),
        "-map",
        f"0:{track.stream_index}",
        "-c:s",
        "srt",
        str(temp_target),
    ]

    try:
        subprocess.run(cmd, capture_output=True, check=True)
        raw_bytes = temp_target.read_bytes()
        normalized_bytes = decode_and_normalize_subtitle_content(raw_bytes)

        # Atomic replacement into final destination path
        temp_target.write_bytes(normalized_bytes)
        temp_target.replace(target_path)
        return target_path
    except Exception:
        if temp_target.exists():
            temp_target.unlink(missing_ok=True)
        raise


def extract_video_embedded_subtitles(
    video_path: str | Path,
    language: str = "all",
    force: bool = False,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Inspect and extract matching embedded subtitle tracks for a single video file."""
    path = Path(video_path).resolve()
    tracks = inspect_embedded_subtitles(path)
    if not tracks:
        return []

    target_lang = normalize_language(language) if language != "all" else "all"
    extracted_results: list[dict[str, Any]] = []

    for track in tracks:
        if target_lang != "all" and track.language != target_lang:
            continue

        lang_suffix = track.language if track.language != "und" else "sub"
        forced_suffix = ".forced" if track.is_forced else ""
        target_name = f"{path.stem}.{lang_suffix}{forced_suffix}.srt"
        target_srt_path = path.parent / target_name

        if not track.is_text_based:
            extracted_results.append(
                {
                    "video_file": path.name,
                    "stream_index": track.stream_index,
                    "codec": track.codec_name,
                    "language": track.language,
                    "target_path": str(target_srt_path),
                    "status": "unsupported_codec",
                    "message": f"Bitmap codec ({track.codec_name}) requires OCR",
                }
            )
            continue

        if target_srt_path.exists() and not force:
            extracted_results.append(
                {
                    "video_file": path.name,
                    "stream_index": track.stream_index,
                    "codec": track.codec_name,
                    "language": track.language,
                    "target_path": str(target_srt_path),
                    "status": "skipped",
                    "message": "Companion SRT already exists on disk",
                }
            )
            continue

        if dry_run:
            extracted_results.append(
                {
                    "video_file": path.name,
                    "stream_index": track.stream_index,
                    "codec": track.codec_name,
                    "language": track.language,
                    "target_path": str(target_srt_path),
                    "status": "dry_run",
                    "message": "Simulated extraction",
                }
            )
            continue

        try:
            saved_file = extract_track_to_srt(path, track, target_srt_path)
            extracted_results.append(
                {
                    "video_file": path.name,
                    "stream_index": track.stream_index,
                    "codec": track.codec_name,
                    "language": track.language,
                    "target_path": str(saved_file),
                    "status": "success",
                    "message": "Extracted & UTF-8 Normalized",
                }
            )
        except Exception as exc:
            extracted_results.append(
                {
                    "video_file": path.name,
                    "stream_index": track.stream_index,
                    "codec": track.codec_name,
                    "language": track.language,
                    "target_path": str(target_srt_path),
                    "status": "failed",
                    "message": str(exc),
                }
            )

    return extracted_results


def extract_embedded_subtitles_batch(
    path: str | Path,
    language: str = "all",
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Extract embedded subtitles across a single file or an entire media library."""
    target_path = Path(path).resolve()
    video_files = find_video_files(target_path)
    if not video_files:
        return {
            "status": "empty",
            "total_files": 0,
            "extracted_count": 0,
            "results": [],
        }

    start_time = time.time()
    all_results: list[dict[str, Any]] = []

    for vf in video_files:
        file_extracted = extract_video_embedded_subtitles(
            video_path=vf,
            language=language,
            force=force,
            dry_run=dry_run,
        )
        all_results.extend(file_extracted)

    duration = time.time() - start_time
    successful = sum(1 for r in all_results if r.get("status") == "success")
    dry_run_count = sum(1 for r in all_results if r.get("status") == "dry_run")
    skipped = sum(1 for r in all_results if r.get("status") == "skipped")
    failed = sum(1 for r in all_results if r.get("status") in ("failed", "unsupported_codec"))

    return {
        "status": "success",
        "total_files": len(video_files),
        "total_tracks": len(all_results),
        "successful": successful,
        "dry_run_count": dry_run_count,
        "skipped": skipped,
        "failed": failed,
        "duration_seconds": duration,
        "results": all_results,
    }
