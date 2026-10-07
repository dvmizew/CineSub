from __future__ import annotations

import contextlib
import select
import sys
import threading
from collections.abc import Callable, Generator

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.theme import Theme

_THEME = Theme(
    {
        "info": "cyan",
        "warning": "yellow",
        "error": "red bold",
        "success": "green bold",
    }
)

CONSOLE = Console(theme=_THEME, force_terminal=True)


class CineSubLogger:
    def __init__(self) -> None:
        self.verbose: bool = False

    def info(self, message: str) -> None:
        CONSOLE.print(f"[cyan]i[/] {message}", highlight=False)

    def success(self, message: str) -> None:
        CONSOLE.print(f"[green]✓[/] {message}", highlight=False)

    def warning(self, message: str) -> None:
        CONSOLE.print(f"[yellow]⚠[/] {message}", highlight=False)

    def error(self, message: str) -> None:
        CONSOLE.print(f"[red]✗[/] {message}", highlight=False)

    def debug(self, message: str) -> None:
        if self.verbose:
            CONSOLE.print(f"[dim]{message}[/dim]", highlight=False)


LOG = CineSubLogger()


def create_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=CONSOLE,
    )


_PAUSE_EVENT = threading.Event()
_PAUSE_EVENT.set()


def wait_if_paused(poll_interval: float = 0.2) -> None:
    """Block calling thread safely until resumed."""
    while not _PAUSE_EVENT.wait(timeout=poll_interval):
        pass


def is_paused() -> bool:
    """Return True if execution is currently paused."""
    return not _PAUSE_EVENT.is_set()


@contextlib.contextmanager
def interactive_pause_listener(
    progress: Progress | None = None,
    task_id: TaskID | None = None,
    on_pause: Callable[[], None] | None = None,
    on_resume: Callable[[], None] | None = None,
) -> Generator[None, None, None]:
    """Listen for Space or 'p' / 'P' in background cbreak mode to pause/resume execution."""
    try:
        if not sys.stdin.isatty():
            yield
            return
    except (ValueError, OSError):
        yield
        return

    _PAUSE_EVENT.set()
    stop_event = threading.Event()
    pause_start_time: float | None = None
    original_description: str | None = None

    def _default_pause() -> None:
        nonlocal pause_start_time, original_description
        if progress is not None and task_id is not None:
            pause_start_time = progress.get_time()
            progress_task = next((t for t in progress.tasks if t.id == task_id), None)
            if progress_task:
                original_description = progress_task.description
            progress.stop_task(task_id)
            progress.update(
                task_id,
                description="[bold yellow]⏸️  PAUSED (Press [Space] or 'p' to resume)[/]",
            )
            progress.refresh()

    def _default_resume() -> None:
        nonlocal pause_start_time
        if progress is not None and task_id is not None:
            if pause_start_time is not None:
                pause_duration = progress.get_time() - pause_start_time
                progress_task = next((t for t in progress.tasks if t.id == task_id), None)
                if progress_task and progress_task.start_time is not None:
                    progress_task.start_time += pause_duration
                if progress_task:
                    progress_task.stop_time = None
                pause_start_time = None
            desc = original_description or "[cyan]Processing..."
            progress.update(task_id, description=desc)
            progress.refresh()

    def _listener_loop() -> None:
        orig_term = None
        try:
            import termios
            import tty

            orig_term = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
        except (ImportError, OSError, ValueError):
            return

        try:
            while not stop_event.is_set():
                rlist, _, _ = select.select([sys.stdin], [], [], 0.2)
                if rlist and not stop_event.is_set():
                    char = sys.stdin.read(1)
                    if not char:
                        break
                    if char in (" ", "p", "P"):
                        if _PAUSE_EVENT.is_set():
                            _PAUSE_EVENT.clear()
                            if on_pause:
                                try:
                                    on_pause()
                                except (RuntimeError, OSError, ValueError) as err:
                                    LOG.debug(f"Pause callback error: {err}")
                            else:
                                _default_pause()
                            LOG.warning(
                                "⏸️  [bold yellow]PAUSED[/] - In-flight operations "
                                "finishing cleanly. Press [bold cyan][Space][/] or "
                                "[bold cyan]'p'[/] to resume..."
                            )
                        else:
                            _PAUSE_EVENT.set()
                            if on_resume:
                                try:
                                    on_resume()
                                except (RuntimeError, OSError, ValueError) as err:
                                    LOG.debug(f"Resume callback error: {err}")
                            else:
                                _default_resume()
                            LOG.info("▶️  [bold green]RESUMED[/] - Continuing execution...")

                        while True:
                            drain_list, _, _ = select.select([sys.stdin], [], [], 0.05)
                            if drain_list:
                                sys.stdin.read(1)
                            else:
                                break
        finally:
            if orig_term is not None:
                with contextlib.suppress(OSError):
                    import termios

                    termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, orig_term)

    listener = threading.Thread(target=_listener_loop, name="CineSubPauseListener", daemon=True)
    listener.start()
    try:
        yield
    finally:
        stop_event.set()
        _PAUSE_EVENT.set()
        listener.join(timeout=0.5)
