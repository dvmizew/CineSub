from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from cinesub.core.models import SyncResult


def sync_subtitle_audio(
    video_path: Path | str,
    srt_path: Path | str,
    output_path: Path | str | None = None,
    keep_backup: bool = False,
    max_offset_seconds: int = 600,
) -> SyncResult:
    """Synchronize subtitle timestamps against video audio stream using ffsubsync."""
    video_p = Path(video_path).resolve()
    srt_p = Path(srt_path).resolve()
    target_out = Path(output_path).resolve() if output_path else srt_p

    if not video_p.is_file():
        raise FileNotFoundError(f"Video file not found: {video_p}")
    if not srt_p.is_file():
        raise FileNotFoundError(f"Subtitle file not found: {srt_p}")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found in PATH. ffmpeg is required by ffsubsync.")

    with tempfile.NamedTemporaryFile(
        dir=srt_p.parent, prefix=f".{srt_p.stem}_sync_", suffix=".srt", delete=False
    ) as tmp_file:
        temp_out = Path(tmp_file.name)

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
