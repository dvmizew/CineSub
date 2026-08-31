from __future__ import annotations

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
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
