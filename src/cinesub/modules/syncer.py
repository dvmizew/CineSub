from __future__ import annotations

import os
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
    engine: str = "ffsubsync",
) -> SyncResult:
    """Synchronize subtitle timestamps against video audio stream using ffsubsync or alass."""
    video_file_path = Path(video_path).resolve()
    subtitle_path = Path(srt_path).resolve()
    target_subtitle_path = Path(output_path).resolve() if output_path else subtitle_path
    chosen_engine = engine.lower().strip()

    if not video_file_path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_file_path}")
    if not subtitle_path.is_file():
        raise FileNotFoundError(f"Subtitle file not found: {subtitle_path}")

    with tempfile.NamedTemporaryFile(
        dir=subtitle_path.parent, prefix=f".{subtitle_path.stem}_sync_", suffix=".srt", delete=False
    ) as tmp_file:
        temp_subtitle_path = Path(tmp_file.name)

    if chosen_engine == "alass":
        alass_bin = shutil.which("alass") or shutil.which("alass-cli")
        if not alass_bin:
            raise RuntimeError(
                "alass binary not found in PATH. Install alass or use engine='ffsubsync'."
            )
        cmd = [alass_bin, str(video_file_path), str(subtitle_path), str(temp_subtitle_path)]
    else:
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg not found in PATH. ffmpeg is required by ffsubsync.")
        cmd = [
            sys.executable,
            "-m",
            "ffsubsync",
            str(video_file_path),
            "-i",
            str(subtitle_path),
            "-o",
            str(temp_subtitle_path),
            "--max-offset-seconds",
            str(max_offset_seconds),
            "--overwrite-input",
        ]

    try:
        process_result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if process_result.returncode != 0:
            err_msg = (
                process_result.stderr.strip() or process_result.stdout.strip() or "Unknown error"
            )
            raise RuntimeError(f"Sync engine '{chosen_engine}' failed: {err_msg}")

        if not temp_subtitle_path.is_file() or temp_subtitle_path.stat().st_size == 0:
            raise RuntimeError(f"{chosen_engine} failed to generate a synchronized subtitle file.")

        output_text = f"{process_result.stdout}\n{process_result.stderr}"
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

        if keep_backup and target_subtitle_path == subtitle_path:
            backup_path = subtitle_path.parent / f"{subtitle_path.stem}.orig.srt"
            if not backup_path.exists():
                shutil.copy2(subtitle_path, backup_path)

        os.replace(temp_subtitle_path, target_subtitle_path)

        offset_desc = f"{offset:+.3f}s" if offset is not None else "Aligned"
        msg = f"Audio sync complete via {chosen_engine} (offset: {offset_desc})"

        return SyncResult(
            success=True,
            video_path=video_file_path,
            srt_path=target_subtitle_path,
            offset_seconds=offset,
            framerate_scale=scale,
            message=msg,
        )
    finally:
        if temp_subtitle_path.exists():
            temp_subtitle_path.unlink(missing_ok=True)
