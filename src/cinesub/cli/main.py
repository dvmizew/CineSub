from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter
from dotenv import load_dotenv
from rich.panel import Panel
from rich.table import Table

from cinesub import __version__
from cinesub.core.constants import DEFAULT_LANGUAGE, DEFAULT_TIMEOUT, USER_AGENT
from cinesub.core.logger import CONSOLE, LOG
from cinesub.modules.downloader import download_and_sync_batch, download_bulk_batch

load_dotenv()

app = App(
    name="cinesub",
    version=__version__,
    version_flags=["--version", "-v"],
    help_flags=["--help", "-h"],
    help="CineSub - Subtitle searching, downloading, and audio-based synchronization.",
    result_action="return_value",
)


def _render_stats_card(
    title: str,
    total: int,
    success: int,
    skipped: int,
    dry_run: int,
    failed: int,
    duration: float,
    throughput: float,
    is_dry_run: bool = False,
) -> None:
    """Render a Sonora-inspired statistics dashboard card with key performance metrics."""
    stats_grid = Table.grid(expand=True, padding=(0, 2))
    stats_grid.add_column(justify="center")
    stats_grid.add_column(justify="center")
    stats_grid.add_column(justify="center")
    stats_grid.add_column(justify="center")

    match_pct = (
        (success / max(1, total)) * 100.0 if not is_dry_run else (dry_run / max(1, total)) * 100.0
    )
    action_label = "Dry-Run Matches" if is_dry_run else "Downloaded"
    action_count = dry_run if is_dry_run else success
    action_val = f"{action_count} ({match_pct:.1f}%)"
    action_color = "yellow" if is_dry_run else "green"

    stats_grid.add_row(
        f"[dim]Total Scanned[/dim]\n[bold white]{total} media files[/bold white]",
        f"[dim]{action_label}[/dim]\n[bold {action_color}]{action_val}[/bold {action_color}]",
        f"[dim]Skipped / Failed[/dim]\n[bold white]{skipped} skip / {failed} fail[/bold white]",
        f"[dim]Throughput / Speed[/dim]\n[bold cyan]{throughput} files/s[/bold cyan]",
    )

    border_color = "yellow" if is_dry_run else "green"
    card = Panel(
        stats_grid,
        title=f"[bold {border_color}]{title}[/bold {border_color}]",
        border_style=border_color,
        padding=(1, 2),
    )
    CONSOLE.print()
    CONSOLE.print(card)
    CONSOLE.print()


@app.command
def sync(
    path: Annotated[
        Path,
        Parameter(
            show_default=False,
            help="Path to a video file or a directory containing video files.",
        ),
    ],
    *,
    language: Annotated[
        str,
        Parameter(
            name=["--language", "-l"],
            help="Target subtitle language code (ISO 639-1, e.g., 'ro', 'en', 'es').",
        ),
    ] = DEFAULT_LANGUAGE,
    provider: Annotated[
        str,
        Parameter(
            name=["--provider", "-p"],
            help="Provider to search: 'all', 'opensubtitles', or 'subdl'.",
        ),
    ] = "all",
    threads: Annotated[
        int,
        Parameter(
            name=["--threads", "-t"],
            help="Number of concurrent worker threads for batch processing.",
        ),
    ] = 4,
    json_report: Annotated[
        Path | None,
        Parameter(
            name=["--json", "-j"],
            help="Save structured report to a JSON or JSONL file.",
        ),
    ] = None,
    sync_audio: Annotated[
        bool,
        Parameter(
            name=["--sync", "-s"],
            help="Perform audio waveform synchronization with ffsubsync.",
        ),
    ] = False,
    backup: Annotated[
        bool,
        Parameter(
            name=["--backup", "-b"],
            help="Keep original unsynchronized subtitle copy (*.orig.srt).",
        ),
    ] = False,
    engine: Annotated[
        str,
        Parameter(
            name=["--engine", "-e"],
            help="Audio synchronization engine: 'ffsubsync' or 'alass'.",
        ),
    ] = "ffsubsync",
    force: Annotated[
        bool,
        Parameter(
            name=["--force", "-f"],
            help="Overwrite existing subtitle files without warning.",
        ),
    ] = False,
    lang_suffix: Annotated[
        bool,
        Parameter(
            name=["--lang-suffix", "-S"],
            help="Save subtitle with language suffix for Plex/Emby (e.g. movie.ro.srt).",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        Parameter(
            name=["--dry-run", "-d"],
            help="Simulate search and matching without downloading or altering files.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        Parameter(
            name=["--verbose"],
            help="Enable verbose debug logging.",
        ),
    ] = False,
) -> dict:
    """Download the single best subtitle and optionally synchronize it to audio dialogue."""
    LOG.verbose = verbose

    try:
        report = download_and_sync_batch(
            target_path=path,
            language=language,
            provider=provider,
            do_sync=sync_audio,
            keep_backup=backup,
            force=force,
            threads=threads,
            json_path=json_report,
            sync_engine=engine,
            use_lang_suffix=lang_suffix,
            dry_run=dry_run,
        )

        results = report.get("results", [])
        total_files = report.get("total_files", len(results))
        duration = float(report.get("duration_seconds", 0.0))
        throughput = float(report.get("throughput_files_per_sec", 0.0))

        if len(results) == 1 and results[0].get("status") in ("success", "skipped", "dry_run"):
            res = results[0]
            sub = res.get("subtitle", {})
            sync_data = res.get("sync", {})
            st = res.get("status")

            if st == "dry_run":
                title = "⚡ Dry-Run Simulation (No Files Modified)"
                color = "yellow"
            elif st == "skipped":
                title = "✓ Subtitle Already Present"
                color = "yellow"
            else:
                title = "✓ Subtitle Download Complete"
                color = "green"

            table = Table(title=f"[bold {color}]{title}[/bold {color}]")
            table.add_column("Property", style="cyan", no_wrap=True)
            table.add_column("Value", style="white")

            table.add_row("Video File", res.get("video_file"))
            table.add_row("Detected Title", f"{res.get('title')} ({res.get('year') or 'N/A'})")
            table.add_row("OpenSubtitles Hash", res.get("moviehash") or "N/A")
            table.add_row("Selected Provider", sub.get("provider", "").upper())
            table.add_row(
                "Match Strategy",
                "Existing Local File"
                if st == "skipped"
                else ("Exact HASH Match" if sub.get("matched_by_hash") else "Relevance Score"),
            )
            table.add_row("Subtitle Release", sub.get("release_name", ""))
            table.add_row("Language", sub.get("language", "").upper())
            table.add_row("Target SRT Path", sub.get("saved_path", ""))

            if sync_data.get("success"):
                offset_val = sync_data.get("offset_seconds")
                offset_str = f"{offset_val:+.3f}s" if offset_val is not None else "Aligned"
                table.add_row("Audio Sync", f"[bold green]Active ({offset_str})[/bold green]")
            else:
                msg = sync_data.get("message", "Disabled")
                table.add_row("Audio Sync", f"[dim]{msg}[/dim]")

            CONSOLE.print(table)
            if st != "dry_run":
                CONSOLE.print(f"\n[bold green]✓ Ready for playback:[/] {sub.get('saved_path')}\n")
            else:
                CONSOLE.print(
                    f"\n[bold yellow]⚡ Simulated target path:[/] {sub.get('saved_path')}\n"
                )
        else:
            dashboard_title = (
                "⚡ CineSub Dry-Run Simulation" if dry_run else "✓ CineSub Synchronization Summary"
            )
            _render_stats_card(
                title=dashboard_title,
                total=total_files,
                success=report.get("successful", 0),
                skipped=report.get("skipped", 0),
                dry_run=report.get("dry_run_count", 0),
                failed=report.get("failed", 0),
                duration=duration,
                throughput=throughput,
                is_dry_run=dry_run,
            )

            table = Table(title="[bold]Detailed Results Breakdown[/bold]")
            table.add_column("Status", justify="center")
            table.add_column("Video File", style="white")
            table.add_column("Provider", style="cyan")
            table.add_column("Details", style="green")

            for r in results:
                st = r.get("status")
                if st == "success":
                    sub = r.get("subtitle", {})
                    sync_data = r.get("sync", {})
                    offset_val = sync_data.get("offset_seconds")
                    offset_str = f"{offset_val:+.2f}s" if offset_val is not None else "OK"
                    table.add_row(
                        "[bold green]SUCCESS[/bold green]",
                        r.get("video_file"),
                        sub.get("provider", "").upper(),
                        offset_str,
                    )
                elif st == "dry_run":
                    sub = r.get("subtitle", {})
                    score_val = f"Score: {sub.get('score', 0):.1f}"
                    table.add_row(
                        "[bold yellow]DRY-RUN[/bold yellow]",
                        r.get("video_file"),
                        sub.get("provider", "").upper(),
                        score_val,
                    )
                elif st == "skipped":
                    sub = r.get("subtitle", {})
                    table.add_row(
                        "[bold dim]SKIPPED[/bold dim]",
                        r.get("video_file"),
                        "LOCAL",
                        f"Exists: {sub.get('release_name', 'SRT')}",
                    )
                else:
                    table.add_row(
                        "[bold red]FAILED[/bold red]",
                        r.get("video_file"),
                        "-",
                        r.get("error", "Error")[:35],
                    )

            CONSOLE.print(table)
            CONSOLE.print()

        return report

    except Exception as exc:
        LOG.error(str(exc))
        return {"status": "error", "error": str(exc)}


@app.command
def bulk(
    path: Annotated[
        Path,
        Parameter(
            show_default=False,
            help="Path to a video file or a directory containing video files.",
        ),
    ],
    *,
    language: Annotated[
        str,
        Parameter(
            name=["--language", "-l"],
            help="Target subtitle language code (e.g., 'ro', 'en').",
        ),
    ] = DEFAULT_LANGUAGE,
    limit: Annotated[
        int,
        Parameter(
            name=["--limit", "-n"],
            help="Number of subtitle alternatives to download (5-10 recommended).",
        ),
    ] = 5,
    provider: Annotated[
        str,
        Parameter(
            name=["--provider", "-p"],
            help="Provider to search: 'all', 'opensubtitles', or 'subdl'.",
        ),
    ] = "all",
    threads: Annotated[
        int,
        Parameter(
            name=["--threads", "-t"],
            help="Number of concurrent worker threads for batch processing.",
        ),
    ] = 4,
    json_report: Annotated[
        Path | None,
        Parameter(
            name=["--json", "-j"],
            help="Save structured report to a JSON or JSONL file.",
        ),
    ] = None,
    force: Annotated[
        bool,
        Parameter(
            name=["--force", "-f"],
            help="Overwrite existing files.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        Parameter(
            name=["--dry-run", "-d"],
            help="Simulate search and matching without downloading files.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        Parameter(
            name=["--verbose"],
            help="Enable verbose debug logging.",
        ),
    ] = False,
) -> dict:
    """Download multiple subtitle alternatives for manual inspection and comparison."""
    LOG.verbose = verbose

    try:
        report = download_bulk_batch(
            target_path=path,
            language=language,
            limit=limit,
            provider=provider,
            force=force,
            threads=threads,
            json_path=json_report,
            dry_run=dry_run,
        )

        results = report.get("results", [])
        total_files = report.get("total_files", len(results))
        duration = float(report.get("duration_seconds", 0.0))
        throughput = float(report.get("throughput_files_per_sec", 0.0))

        if len(results) == 1 and results[0].get("status") in ("success", "dry_run"):
            subs = results[0].get("subtitles", [])
            title_prefix = "⚡ Dry-Run Simulated" if dry_run else "✓ Downloaded"
            table = Table(
                title=f"[bold green]{title_prefix} {len(subs)} Subtitle Alternatives[/bold green]"
            )
            table.add_column("#", style="dim", justify="right")
            table.add_column("Provider", style="cyan")
            table.add_column("Release Match", style="white")
            table.add_column("Score", justify="right", style="yellow")
            table.add_column("Saved File", style="green")

            for idx, s in enumerate(subs, start=1):
                saved_name = Path(s.get("saved_path", "")).name
                rel_name = s.get("release_name", "")
                table.add_row(
                    str(idx),
                    s.get("provider", "").upper(),
                    rel_name[:45] + ("..." if len(rel_name) > 45 else ""),
                    f"{s.get('score', 0.0):.1f}",
                    saved_name,
                )

            CONSOLE.print(table)
            saved_loc = path.parent if path.is_file() else path
            if not dry_run:
                CONSOLE.print(f"\n[bold green]✓ Files saved in:[/] {saved_loc}\n")
            else:
                CONSOLE.print(f"\n[bold yellow]⚡ Simulated directory:[/] {saved_loc}\n")
        else:
            dashboard_title = "⚡ CineSub Bulk Dry-Run" if dry_run else "✓ CineSub Bulk Summary"
            _render_stats_card(
                title=dashboard_title,
                total=total_files,
                success=report.get("successful", 0),
                skipped=0,
                dry_run=report.get("dry_run_count", 0),
                failed=report.get("failed", 0),
                duration=duration,
                throughput=throughput,
                is_dry_run=dry_run,
            )

            table = Table(title="[bold]Detailed Results Breakdown[/bold]")
            table.add_column("Status", justify="center")
            table.add_column("Video File", style="white")
            table.add_column("Subtitles Found", justify="right", style="cyan")

            for r in results:
                if r.get("status") in ("success", "dry_run"):
                    count_str = str(len(r.get("subtitles", [])))
                    status_str = (
                        "[bold yellow]DRY-RUN[/bold yellow]"
                        if dry_run
                        else "[bold green]SUCCESS[/bold green]"
                    )
                    table.add_row(status_str, r.get("video_file"), count_str)
                else:
                    table.add_row("[bold red]FAILED[/bold red]", r.get("video_file"), "0")

            CONSOLE.print(table)
            CONSOLE.print()

        return report

    except Exception as exc:
        LOG.error(str(exc))
        return {"status": "error", "error": str(exc)}


def _mask_secret(value: str) -> str:
    val = value.strip()
    if not val:
        return ""
    if len(val) <= 8:
        return "Configured (***)"
    return f"Configured ({val[:4]}...{val[-4:]})"


@app.command
def config() -> None:
    os_key = os.getenv("OPENSUBTITLES_API_KEY", "").strip()
    subdl_key = os.getenv("SUBDL_API_KEY", "").strip()
    ffmpeg_ok = shutil.which("ffmpeg") is not None

    table = Table(title="[bold cyan]CineSub Configuration Status[/bold cyan]")
    table.add_column("Component", style="cyan")
    table.add_column("Status / Value", style="white")

    os_status = (
        f"[green]{_mask_secret(os_key)}[/green]"
        if os_key
        else "[yellow]Missing (OPENSUBTITLES_API_KEY)[/yellow]"
    )
    table.add_row("OpenSubtitles API", os_status)
    table.add_row("OpenSubtitles User-Agent", USER_AGENT)
    table.add_row("OpenSubtitles Rate Limit", "4.0 req/s (Safe Client Cap)")

    subdl_status = (
        f"[green]{_mask_secret(subdl_key)}[/green]"
        if subdl_key
        else "[yellow]Missing (SUBDL_API_KEY)[/yellow]"
    )
    table.add_row("SubDL API", subdl_status)
    table.add_row("SubDL Rate Limit", "8.0 req/s (600 req/min Cap)")
    table.add_row("Default Language", DEFAULT_LANGUAGE)
    table.add_row("HTTP Timeout", f"{DEFAULT_TIMEOUT}s")

    ffmpeg_status = (
        "[green]Installed & Found in PATH[/green]"
        if ffmpeg_ok
        else "[yellow]Not Found (Required for ffsubsync)[/yellow]"
    )
    table.add_row("FFmpeg (ffsubsync)", ffmpeg_status)

    alass_bin = shutil.which("alass") or shutil.which("alass-cli")
    alass_status = (
        "[green]Installed & Found in PATH[/green]"
        if alass_bin
        else "[dim]Optional (Rust binary not in PATH)[/dim]"
    )
    table.add_row("Alass Engine", alass_status)

    CONSOLE.print(table)
    CONSOLE.print()


def main() -> None:
    """Main CLI entrypoint."""
    app()


if __name__ == "__main__":
    main()
