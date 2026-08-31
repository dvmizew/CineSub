from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from cinesub.core.models import SyncResult


def is_ffmpeg_available() -> bool:
    """Check whether ffmpeg is installed and accessible in system PATH."""
    return shutil.which("ffmpeg") is not None


def sync_subtitle_audio(
    video_path: Path | str,
    srt_path: Path | str,
    output_path: Path | str | None = None,
    keep_backup: bool = False,
    max_offset_seconds: int = 600,
) -> SyncResult:
    """Synchronize subtitle timestamps against video audio stream using ffsubsync.

    Args:
        video_path: Target video file path.
        srt_path: Input subtitle file path.
        output_path: Output synchronized srt path (defaults to overwriting srt_path).
        keep_backup: If True and overwriting, saves `*.orig.srt`.
        max_offset_seconds: Maximum search window for alignment.

    Returns:
        SyncResult instance.

    Raises:
        FileNotFoundError: If input video or subtitle file does not exist.
        RuntimeError: If ffmpeg is missing or synchronization fails.
    """
    video_p = Path(video_path).resolve()
    srt_p = Path(srt_path).resolve()
    target_out = Path(output_path).resolve() if output_path else srt_p

    if not video_p.is_file():
        raise FileNotFoundError(f"Video file not found: {video_p}")
    if not srt_p.is_file():
        raise FileNotFoundError(f"Subtitle file not found: {srt_p}")
    if not is_ffmpeg_available():
        raise RuntimeError("ffmpeg not found in PATH. ffmpeg is required by ffsubsync.")

    temp_out = srt_p.parent / f"{srt_p.stem}.synced.tmp.srt"

    cmd = [
        sys.executable,
        "-m",
        "ffsubsync",
        str(video_p),
        "-i",
        str(srt_p),
        "-o",
        str(temp_out),
        "--max-offset-seconds",
        str(max_offset_seconds),
        "--overwrite-input",
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            err_msg = res.stderr.strip() or res.stdout.strip() or "Unknown error"
            raise RuntimeError(f"ffsubsync failed: {err_msg}")

        if not temp_out.is_file() or temp_out.stat().st_size == 0:
            raise RuntimeError("ffsubsync failed to generate a synchronized subtitle file.")

        # Extract offset and framerate scale from ffsubsync logs
        output_text = f"{res.stdout}\n{res.stderr}"
        offset: float | None = None
        scale: float | None = None

        offset_match = re.search(
            r"offset seconds:\s*([+-]?\d+(?:\.\d+)?)", output_text, re.IGNORECASE
        )
        if offset_match:
            try:
                offset = float(offset_match.group(1))
            except ValueError:
                pass

        scale_match = re.search(
            r"framerate scale factor:\s*([+-]?\d+(?:\.\d+)?)", output_text, re.IGNORECASE
        )
        if scale_match:
            try:
                scale = float(scale_match.group(1))
            except ValueError:
                pass

        if keep_backup and target_out == srt_p:
            backup_p = srt_p.parent / f"{srt_p.stem}.orig.srt"
            if not backup_p.exists():
                shutil.copy2(srt_p, backup_p)

        shutil.move(str(temp_out), str(target_out))

        offset_desc = f"{offset:+.3f}s" if offset is not None else "Aligned"
        msg = f"Audio sync complete (offset: {offset_desc})"

        return SyncResult(
            success=True,
            video_path=video_p,
            srt_path=target_out,
            offset_seconds=offset,
            framerate_scale=scale,
            message=msg,
        )
    finally:
        if temp_out.exists():
            temp_out.unlink(missing_ok=True)
