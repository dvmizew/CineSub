from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from rich.progress import Progress, TaskID

from cinesub.core.logger import (
    _PAUSE_EVENT,
    interactive_pause_listener,
    is_paused,
    wait_if_paused,
)
from cinesub.core.utils import InterruptedOperationError, is_interruption
from cinesub.modules.downloader import download_batch, download_bulk_batch
from cinesub.modules.extractor import extract_embedded_subtitles_batch


def test_wait_if_paused_immediate_when_unpaused() -> None:
    _PAUSE_EVENT.set()
    start_time = time.perf_counter()
    wait_if_paused(poll_interval=0.05)
    elapsed = time.perf_counter() - start_time
    assert elapsed < 0.1
    assert not is_paused()


def test_wait_if_paused_blocks_until_resumed() -> None:
    _PAUSE_EVENT.clear()
    assert is_paused()

    def _resume_after_delay() -> None:
        time.sleep(0.1)
        _PAUSE_EVENT.set()

    worker = threading.Thread(target=_resume_after_delay)
    worker.start()

    start_time = time.perf_counter()
    wait_if_paused(poll_interval=0.02)
    elapsed = time.perf_counter() - start_time

    worker.join()
    assert elapsed >= 0.08
    assert not is_paused()


def test_is_interruption_detection() -> None:
    assert is_interruption(KeyboardInterrupt())
    assert is_interruption(InterruptedOperationError("partial"))
    assert is_interruption(RuntimeError("release unlocked lock"))
    assert is_interruption(RuntimeError("cannot release unlocked lock in condition"))

    chained_exc = RuntimeError("Wrapper failed")
    chained_exc.__context__ = KeyboardInterrupt()
    assert is_interruption(chained_exc)

    assert not is_interruption(ValueError("Invalid argument"))
    assert not is_interruption(RuntimeError("General computation failure"))
    assert not is_interruption(OSError("Disk full"))


def test_interactive_pause_listener_non_tty_skips() -> None:
    with patch("sys.stdin.isatty", return_value=False):
        with interactive_pause_listener():
            assert not is_paused()


def test_interactive_pause_listener_progress_callbacks() -> None:
    progress = Progress()
    task_id: TaskID = progress.add_task("Processing items", total=10)

    with patch("sys.stdin.isatty", return_value=False):
        with interactive_pause_listener(progress, task_id):
            assert not is_paused()

    progress.stop()


def test_download_batch_interruption_graceful(tmp_path: Path) -> None:
    video_one = tmp_path / "Movie.A.2024.1080p.mp4"
    video_two = tmp_path / "Movie.B.2024.1080p.mp4"
    video_one.write_bytes(b"dummy_data_one")
    video_two.write_bytes(b"dummy_data_two")

    with (
        patch("cinesub.modules.downloader.get_active_services", return_value={"os": MagicMock()}),
        patch(
            "cinesub.modules.downloader._batch_download_worker",
            side_effect=[
                {"video_file": "Movie.A.2024.1080p.mp4", "status": "success"},
                KeyboardInterrupt(),
            ],
        ),
    ):
        report = download_batch(target_path=tmp_path, threads=1)
        assert report["interrupted"] is True
        assert report["command"] == "download"
        assert len(report["results"]) >= 1


def test_download_bulk_batch_interruption_graceful(tmp_path: Path) -> None:
    video_one = tmp_path / "Movie.A.2024.1080p.mp4"
    video_two = tmp_path / "Movie.B.2024.1080p.mp4"
    video_one.write_bytes(b"dummy_data_one")
    video_two.write_bytes(b"dummy_data_two")

    with (
        patch("cinesub.modules.downloader.get_active_services", return_value={"os": MagicMock()}),
        patch(
            "cinesub.modules.downloader._batch_bulk_worker",
            side_effect=[
                {"video_file": "Movie.A.2024.1080p.mp4", "status": "success", "subtitles": []},
                KeyboardInterrupt(),
            ],
        ),
    ):
        report = download_bulk_batch(target_path=tmp_path, threads=1)
        assert report["interrupted"] is True
        assert report["command"] == "bulk"


def test_extract_embedded_subtitles_batch_interruption_graceful(tmp_path: Path) -> None:
    video_one = tmp_path / "Movie.A.2024.1080p.mp4"
    video_two = tmp_path / "Movie.B.2024.1080p.mp4"
    video_one.write_bytes(b"dummy_data_one")
    video_two.write_bytes(b"dummy_data_two")

    with patch(
        "cinesub.modules.extractor.extract_video_embedded_subtitles",
        side_effect=[
            [{"video_file": "Movie.A.2024.1080p.mp4", "status": "success"}],
            KeyboardInterrupt(),
        ],
    ):
        report = extract_embedded_subtitles_batch(path=tmp_path)
        assert report["interrupted"] is True
        assert len(report["results"]) == 1
