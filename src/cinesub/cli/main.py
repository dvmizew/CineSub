from __future__ import annotations

import contextlib
import os
import shutil
import signal
from pathlib import Path
from typing import Annotated, Any

from cyclopts import App, Parameter
from dotenv import load_dotenv
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from cinesub import __version__
from cinesub.core.constants import DEFAULT_LANGUAGE, DEFAULT_TIMEOUT, IGNORED_DIRS, USER_AGENT
from cinesub.core.logger import (
    CONSOLE,
    LOG,
    create_progress,
    interactive_pause_listener,
    wait_if_paused,
)
from cinesub.core.models import VideoMetadata
from cinesub.core.utils import (
    find_video_files,
    is_interruption,
    parse_directory_metadata,
    parse_video_metadata,
)
from cinesub.modules.downloader import download_batch, download_bulk_batch
from cinesub.modules.extractor import extract_embedded_subtitles_batch
from cinesub.services.tmdb import TmdbService

load_dotenv()

if hasattr(signal, "SIGCONT"):
    with contextlib.suppress(ValueError, OSError):
        signal.signal(
            signal.SIGCONT,
            lambda *_: LOG.info("▶️ [bold green]Resumed execution.[/]"),
        )

app = App(
    name="cinesub",
    version=__version__,
    version_flags=["--version", "-v"],
    help_flags=["--help", "-h"],
    help="CineSub - Automated subtitle searching, downloading, and media management CLI.",
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
        f"[dim]Throughput / Speed[/dim]\n[bold cyan]{throughput:.1f} files/s[/bold cyan]",
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


@app.command(name="download", alias="sync")
def download(
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
            help=(
                "Provider to search: 'all', 'opensubtitles', 'subdl', 'subsource', "
                "'subsro', 'betaseries', 'gestdown', 'bsplayer', 'animetosho', or 'assrt'."
            ),
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
    """Download the highest-scoring matching subtitles for video files."""
    LOG.verbose = verbose

    try:
        report = download_batch(
            target_path=path,
            language=language,
            provider=provider,
            force=force,
            threads=threads,
            json_path=json_report,
            use_lang_suffix=lang_suffix,
            dry_run=dry_run,
        )

        results = report.get("results", [])
        total_files = report.get("total_files", len(results))
        duration = float(report.get("duration_seconds", 0.0))
        throughput = float(report.get("throughput_files_per_sec", 0.0))

        if len(results) == 1 and results[0].get("status") in ("success", "skipped", "dry_run"):
            single_result = results[0]
            matched_subtitle = single_result.get("subtitle", {})
            file_status = single_result.get("status")

            if file_status == "dry_run":
                title = "⚡ Dry-Run Simulation (No Files Modified)"
                color = "yellow"
            elif file_status == "skipped":
                title = "✓ Subtitle Already Present"
                color = "yellow"
            else:
                title = "✓ Subtitle Download Complete"
                color = "green"

            table = Table(title=f"[bold {color}]{title}[/bold {color}]")
            table.add_column("Property", style="cyan", no_wrap=True)
            table.add_column("Value", style="white")

            table.add_row("Video File", escape(str(single_result.get("video_file") or "")))
            table.add_row(
                "Detected Title",
                escape(f"{single_result.get('title')} ({single_result.get('year') or 'N/A'})"),
            )
            table.add_row("OpenSubtitles Hash", single_result.get("moviehash") or "N/A")
            table.add_row("Selected Provider", matched_subtitle.get("provider", "").upper())
            table.add_row(
                "Match Strategy",
                "Existing Local File"
                if file_status == "skipped"
                else (
                    "Exact HASH Match"
                    if matched_subtitle.get("matched_by_hash")
                    else "Relevance Score"
                ),
            )
            table.add_row("Subtitle Release", escape(str(matched_subtitle.get("release_name", ""))))
            table.add_row("Language", matched_subtitle.get("language", "").upper())
            score_num = matched_subtitle.get("score")
            table.add_row(
                "Match Score",
                f"{score_num:.1f}" if score_num is not None else "100.0",
            )
            table.add_row("Target SRT Path", escape(str(matched_subtitle.get("saved_path", ""))))

            CONSOLE.print(table)
            if file_status != "dry_run":
                target_saved = escape(str(matched_subtitle.get("saved_path") or ""))
                CONSOLE.print(f"\n[bold green]✓ Ready for playback:[/] {target_saved}\n")
            else:
                simulated_path = escape(str(matched_subtitle.get("saved_path") or ""))
                CONSOLE.print(f"\n[bold yellow]⚡ Simulated target path:[/] {simulated_path}\n")
        else:
            if report.get("interrupted"):
                dashboard_title = "⏹ CineSub Download Interrupted (Ctrl+C)"
            elif dry_run:
                dashboard_title = "⚡ CineSub Dry-Run Simulation"
            else:
                dashboard_title = "✓ CineSub Download Summary"

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
            table.add_column("Score / Details", style="green")

            for result_entry in results:
                file_status = result_entry.get("status")
                video_name = escape(str(result_entry.get("video_file") or ""))
                if file_status in ("success", "downloaded"):
                    matched_subtitle = result_entry.get("subtitle", {})
                    score_num = matched_subtitle.get("score", 0.0)
                    table.add_row(
                        "[bold green]SUCCESS[/bold green]",
                        video_name,
                        matched_subtitle.get("provider", "").upper(),
                        f"Score: {score_num:.1f}",
                    )
                elif file_status == "dry_run":
                    matched_subtitle = result_entry.get("subtitle", {})
                    score_num = matched_subtitle.get("score", 0.0)
                    table.add_row(
                        "[bold yellow]DRY-RUN[/bold yellow]",
                        video_name,
                        matched_subtitle.get("provider", "").upper(),
                        f"Score: {score_num:.1f}",
                    )
                elif file_status == "skipped":
                    matched_subtitle = result_entry.get("subtitle", {})
                    rel_name = escape(str(matched_subtitle.get("release_name", "SRT")))
                    table.add_row(
                        "[bold dim]SKIPPED[/bold dim]",
                        video_name,
                        "LOCAL",
                        f"Exists: {rel_name}",
                    )
                else:
                    err_msg = escape(str(result_entry.get("error", "Error"))[:35])
                    table.add_row(
                        "[bold red]FAILED[/bold red]",
                        video_name,
                        "-",
                        err_msg,
                    )

            CONSOLE.print(table)
            CONSOLE.print()

        return report

    except (KeyboardInterrupt, RuntimeError) as exc:
        if not is_interruption(exc):
            raise
        LOG.warning("\n⏹️  [bold yellow]INTERRUPTED[/] - Download stopped by user (Ctrl+C).")
        return {"status": "interrupted"}
    except Exception as exc:
        LOG.error(str(exc))
        return {"status": "error", "error": str(exc)}


# Backward compatibility alias
sync = download


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
            help=(
                "Provider to search: 'all', 'opensubtitles', 'subdl', 'subsource', "
                "'subsro', 'betaseries', 'gestdown', 'bsplayer', 'animetosho', or 'assrt'."
            ),
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
            alternative_subtitles = results[0].get("subtitles", [])
            title_prefix = "⚡ Dry-Run Simulated" if dry_run else "✓ Downloaded"
            table_heading = (
                f"[bold green]{title_prefix} {len(alternative_subtitles)} "
                "Subtitle Alternatives[/bold green]"
            )
            table = Table(title=table_heading)
            table.add_column("#", style="dim", justify="right")
            table.add_column("Provider", style="cyan")
            table.add_column("Release Match", style="white")
            table.add_column("Score", justify="right", style="yellow")
            table.add_column("Saved File", style="green")

            for rank_idx, sub_candidate in enumerate(alternative_subtitles, start=1):
                saved_name = Path(sub_candidate.get("saved_path", "")).name
                rel_name = sub_candidate.get("release_name", "")
                table.add_row(
                    str(rank_idx),
                    sub_candidate.get("provider", "").upper(),
                    escape(rel_name[:45] + ("..." if len(rel_name) > 45 else "")),
                    f"{sub_candidate.get('score', 0.0):.1f}",
                    escape(saved_name),
                )

            CONSOLE.print(table)
            saved_loc = path.parent if path.is_file() else path
            if not dry_run:
                CONSOLE.print(f"\n[bold green]✓ Files saved in:[/] {escape(str(saved_loc))}\n")
            else:
                CONSOLE.print(
                    f"\n[bold yellow]⚡ Simulated directory:[/] {escape(str(saved_loc))}\n"
                )
        else:
            if report.get("interrupted"):
                dashboard_title = "⏹ CineSub Bulk Interrupted (Ctrl+C)"
            elif dry_run:
                dashboard_title = "⚡ CineSub Bulk Dry-Run"
            else:
                dashboard_title = "✓ CineSub Bulk Summary"

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

            for batch_entry in results:
                video_name = escape(str(batch_entry.get("video_file") or ""))
                if batch_entry.get("status") in ("success", "dry_run"):
                    count_str = str(len(batch_entry.get("subtitles", [])))
                    status_str = (
                        "[bold yellow]DRY-RUN[/bold yellow]"
                        if dry_run
                        else "[bold green]SUCCESS[/bold green]"
                    )
                    table.add_row(status_str, video_name, count_str)
                else:
                    table.add_row("[bold red]FAILED[/bold red]", video_name, "0")

            CONSOLE.print(table)
            CONSOLE.print()

        return report

    except (KeyboardInterrupt, RuntimeError) as exc:
        if not is_interruption(exc):
            raise
        LOG.warning("\n⏹️  [bold yellow]INTERRUPTED[/] - Bulk download stopped by user (Ctrl+C).")
        return {"status": "interrupted"}
    except Exception as exc:
        LOG.error(str(exc))
        return {"status": "error", "error": str(exc)}


def _mask_secret(value: str) -> str:
    secret_token = value.strip()
    if not secret_token:
        return ""
    if len(secret_token) <= 8:
        return "Configured (***)"
    return f"Configured ({secret_token[:4]}...{secret_token[-4:]})"


@app.command
def config() -> None:
    os_key = os.getenv("OPENSUBTITLES_API_KEY", "").strip()
    subdl_key = os.getenv("SUBDL_API_KEY", "").strip()
    subsource_key = os.getenv("SUBSOURCE_API_KEY", "").strip()
    subsro_key = os.getenv("SUBSRO_API_KEY", "").strip() or os.getenv("SUBS_RO_API_KEY", "").strip()
    betaseries_key = (
        os.getenv("BETASERIES_API_KEY", "").strip() or os.getenv("BETA_SERIES_API_KEY", "").strip()
    )
    assrt_key = os.getenv("ASSRT_API_TOKEN", "").strip() or os.getenv("ASSRT_TOKEN", "").strip()
    tmdb_token = (
        os.getenv("TMDB_READ_ACCESS_TOKEN", "").strip() or os.getenv("TMDB_API_KEY", "").strip()
    )
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    ffprobe_ok = shutil.which("ffprobe") is not None

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

    subsource_status = (
        f"[green]{_mask_secret(subsource_key)}[/green]"
        if subsource_key
        else "[yellow]Missing (SUBSOURCE_API_KEY)[/yellow]"
    )
    table.add_row("SubSource API", subsource_status)
    table.add_row("SubSource Rate Limit", "1.0 req/s (60 req/min Cap)")

    subsro_status = (
        f"[green]{_mask_secret(subsro_key)}[/green]"
        if subsro_key
        else "[yellow]Missing (SUBSRO_API_KEY)[/yellow]"
    )
    table.add_row("Subs.ro API", subsro_status)
    table.add_row("Subs.ro Rate Limit", "2.0 req/s (Safe Client Cap)")

    betaseries_status = (
        f"[green]{_mask_secret(betaseries_key)}[/green]"
        if betaseries_key
        else "[yellow]Missing (BETASERIES_API_KEY)[/yellow]"
    )
    table.add_row("BetaSeries API", betaseries_status)
    table.add_row("BetaSeries Rate Limit", "2.0 req/s (Safe Client Cap)")

    table.add_row("Gestdown API", "[green]Active (Free TV REST API)[/green]")
    table.add_row("Gestdown Rate Limit", "2.0 req/s (Safe Client Cap)")

    table.add_row("BSPlayer API", "[green]Active (Free 64-bit Hash SOAP)[/green]")
    table.add_row("BSPlayer Rate Limit", "2.0 req/s (Safe Client Cap)")

    table.add_row("AnimeTosho API", "[green]Active (Free Feed & Attachments)[/green]")
    table.add_row("AnimeTosho Rate Limit", "2.0 req/s (Safe Client Cap)")

    assrt_status = (
        f"[green]{_mask_secret(assrt_key)}[/green]"
        if assrt_key
        else "[yellow]Missing (ASSRT_API_TOKEN)[/yellow]"
    )
    table.add_row("Assrt.net API", assrt_status)
    table.add_row("Assrt.net Rate Limit", "0.33 req/s (20 req/min Cap)")

    tmdb_status = (
        f"[green]{_mask_secret(tmdb_token)}[/green]"
        if tmdb_token
        else "[yellow]Missing (TMDB_READ_ACCESS_TOKEN)[/yellow]"
    )
    table.add_row("TMDb API", tmdb_status)
    table.add_row("TMDb Rate Limit", "4.0 req/s (Safe Client Cap)")
    table.add_row("Default Language", DEFAULT_LANGUAGE)
    table.add_row("HTTP Timeout", f"{DEFAULT_TIMEOUT}s")

    ffmpeg_status = (
        "[green]Installed & Found in PATH[/green]"
        if ffmpeg_ok
        else "[yellow]Not Found (Required for cinesub extract)[/yellow]"
    )
    table.add_row("FFmpeg (Subtitle Extraction)", ffmpeg_status)

    ffprobe_status = (
        "[green]Installed & Found in PATH[/green]"
        if ffprobe_ok
        else "[yellow]Not Found (Required for cinesub extract)[/yellow]"
    )
    table.add_row("FFprobe (Metadata Inspection)", ffprobe_status)

    CONSOLE.print(table)
    CONSOLE.print()


@app.command(name="extract")
def extract(
    path: Annotated[
        Path,
        Parameter(
            show_default=False,
            help="Path to a video file or directory to extract embedded subtitles from.",
        ),
    ],
    *,
    language: Annotated[
        str,
        Parameter(
            name=["--language", "-l"],
            help="Target language ISO code (e.g. 'ro', 'en') or 'all'.",
        ),
    ] = "all",
    force: Annotated[
        bool,
        Parameter(
            name=["--force", "-f"],
            help="Overwrite existing companion .srt files on disk.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        Parameter(
            name=["--dry-run", "-d"],
            help="Simulate embedded subtitle extraction without writing files.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        Parameter(
            name=["--verbose"],
            help="Enable verbose debug logging.",
        ),
    ] = False,
) -> dict[str, Any]:
    """Inspect and extract embedded subtitle streams from video files into companion .srt files."""
    LOG.verbose = verbose
    resolved_path = path.resolve()
    if not resolved_path.exists():
        LOG.error(f"Path does not exist: {resolved_path}")
        return {"status": "error", "message": f"Path does not exist: {resolved_path}"}

    try:
        report = extract_embedded_subtitles_batch(
            path=resolved_path,
            language=language,
            force=force,
            dry_run=dry_run,
        )

        results = report.get("results", [])
        total_files = report.get("total_files", 0)
        duration = float(report.get("duration_seconds", 0.0))

        if report.get("interrupted"):
            dashboard_title = "⏹ CineSub Extract Interrupted (Ctrl+C)"
        elif dry_run:
            dashboard_title = "⚡ CineSub Extract Dry-Run"
        else:
            dashboard_title = "✓ CineSub Extract Summary"

        _render_stats_card(
            title=dashboard_title,
            total=total_files,
            success=report.get("successful", 0),
            skipped=report.get("skipped", 0),
            dry_run=report.get("dry_run_count", 0),
            failed=report.get("failed", 0),
            duration=duration,
            throughput=float(total_files / max(0.001, duration)),
            is_dry_run=dry_run,
        )

        table = Table(title="[bold cyan]Embedded Subtitle Extraction Results[/bold cyan]")
        table.add_column("Video File", style="white")
        table.add_column("Track", justify="right", style="magenta")
        table.add_column("Codec", style="cyan")
        table.add_column("Lang", style="yellow")
        table.add_column("Status", justify="center")
        table.add_column("Output File / Details", style="green")

        for entry in results:
            entry_status = entry.get("status")
            if entry_status == "success":
                status_display = "[bold green]EXTRACTED[/bold green]"
            elif entry_status == "dry_run":
                status_display = "[bold yellow]DRY-RUN[/bold yellow]"
            elif entry_status == "skipped":
                status_display = "[bold dim]SKIPPED[/bold dim]"
            else:
                status_display = "[bold red]FAILED[/bold red]"

            output_detail = Path(entry.get("target_path", "")).name
            if entry_status not in ("success", "dry_run"):
                output_detail = f"{output_detail} ({entry.get('message', '')})"

            table.add_row(
                escape(str(entry.get("video_file") or "")),
                str(entry.get("stream_index")),
                entry.get("codec"),
                entry.get("language", "").upper(),
                status_display,
                escape(output_detail),
            )

        CONSOLE.print(table)
        CONSOLE.print()
        return report

    except (KeyboardInterrupt, RuntimeError) as exc:
        if not is_interruption(exc):
            raise
        LOG.warning(
            "\n⏹️  [bold yellow]INTERRUPTED[/] - Subtitle extraction stopped by user (Ctrl+C)."
        )
        return {"status": "interrupted"}
    except Exception as exc:
        LOG.error(str(exc))
        return {"status": "error", "error": str(exc)}


@app.command(name="tmdb")
def tmdb(
    path: Annotated[
        Path,
        Parameter(
            show_default=False,
            help="Path to a video file or directory to query against TMDb.",
        ),
    ],
    *,
    favorite: Annotated[
        bool,
        Parameter(
            name=["--favorite", "-f"],
            help="Add matched media to your TMDb Favorites list.",
        ),
    ] = False,
    watchlist: Annotated[
        bool,
        Parameter(
            name=["--watchlist", "-w"],
            help="Add matched media to your TMDb Watchlist.",
        ),
    ] = False,
    bookmark: Annotated[
        bool,
        Parameter(
            name=["--bookmark", "-b"],
            help="Add matched media to your TMDb Watchlist / Bookmarks (alias for --watchlist).",
        ),
    ] = False,
    by_folder: Annotated[
        bool,
        Parameter(
            name=["--by-folder", "-F"],
            help="Search TMDb using directory / folder names instead of video filenames.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        Parameter(
            name=["--dry-run", "-d"],
            help="Simulate TMDb search without submitting modifications to your account.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        Parameter(
            name=["--verbose"],
            help="Enable verbose debug logging.",
        ),
    ] = False,
) -> dict[str, Any]:
    """Lookup media on TMDb and optionally sync to your account Favorites or Watchlist."""
    LOG.verbose = verbose
    tmdb_svc = TmdbService()
    if not tmdb_svc.is_configured:
        LOG.error(
            "TMDb API is not configured. Please set TMDB_READ_ACCESS_TOKEN in your .env file."
        )
        return {"status": "error", "message": "TMDb API not configured"}

    resolved_path = path.resolve()
    if not resolved_path.exists():
        LOG.error(f"Path does not exist: {resolved_path}")
        return {"status": "error", "message": f"Path does not exist: {resolved_path}"}

    media_targets: list[VideoMetadata] = []
    if resolved_path.is_file():
        if by_folder:
            media_targets = [parse_directory_metadata(resolved_path.parent)]
        else:
            media_targets = [parse_video_metadata(resolved_path, compute_hash=False)]
    elif resolved_path.is_dir():
        if by_folder:
            child_subdirs = [
                child_dir
                for child_dir in resolved_path.iterdir()
                if child_dir.is_dir()
                and not child_dir.name.startswith(".")
                and child_dir.name.lower() not in IGNORED_DIRS
            ]
            if child_subdirs:
                child_subdirs.sort(key=lambda directory: directory.name.lower())
                media_targets = [parse_directory_metadata(directory) for directory in child_subdirs]
            else:
                media_targets = [parse_directory_metadata(resolved_path)]
        else:
            found_videos = find_video_files(resolved_path)
            if found_videos:
                media_targets = [
                    parse_video_metadata(video_path, compute_hash=False)
                    for video_path in found_videos
                ]
            else:
                child_subdirs = [
                    child_dir
                    for child_dir in resolved_path.iterdir()
                    if child_dir.is_dir()
                    and not child_dir.name.startswith(".")
                    and child_dir.name.lower() not in IGNORED_DIRS
                ]
                if child_subdirs:
                    LOG.info(
                        f"No video files found in {escape(str(resolved_path))}; scanning "
                        f"{len(child_subdirs)} subdirectories by folder name..."
                    )
                    child_subdirs.sort(key=lambda directory: directory.name.lower())
                    media_targets = [
                        parse_directory_metadata(directory) for directory in child_subdirs
                    ]
                else:
                    folder_label = escape(resolved_path.name)
                    LOG.info(
                        f"No video files found; querying folder name '{folder_label}' "
                        "against TMDb..."
                    )
                    media_targets = [parse_directory_metadata(resolved_path)]

    if not media_targets:
        LOG.warning(f"No media targets found in {escape(str(resolved_path))}")
        return {"status": "empty", "total_files": 0}

    LOG.info(f"Processing {len(media_targets)} media items against TMDb...")
    table = Table(title="[bold cyan]TMDb Media Synchronization[/bold cyan]")
    table.add_column("Media Target", style="cyan")
    table.add_column("TMDb Match", style="white")
    table.add_column("Year", style="dim")
    table.add_column("TMDb ID", style="magenta")
    table.add_column("IMDb ID", style="yellow")
    table.add_column("Action / Status", style="green", no_wrap=True)

    processed: list[dict[str, Any]] = []
    seen_tmdb_ids: set[int] = set()
    sync_watchlist = watchlist or bookmark
    bookmark_label = "Bookmarked" if bookmark else "Watchlisted"
    interrupted = False

    progress = create_progress()
    with progress:
        task = progress.add_task("[cyan]Processing media with TMDb...", total=len(media_targets))
        with interactive_pause_listener(progress, task):
            try:
                for media_meta in media_targets:
                    wait_if_paused()
                    display_label = media_meta.file_path.name
                    display_escaped = escape(display_label)
                    match_candidate = (
                        tmdb_svc.search_tv(media_meta.title, media_meta.year)
                        if media_meta.is_episode
                        else tmdb_svc.search_movie(media_meta.title, media_meta.year)
                    )
                    if not match_candidate:
                        table.add_row(
                            display_escaped,
                            "[red]No match found[/red]",
                            "-",
                            "-",
                            "-",
                            "[red]FAILED[/red]",
                        )
                        processed.append({"file": display_label, "status": "not_found"})
                        progress.advance(task)
                        continue

                    raw_id = match_candidate.get("id")
                    if raw_id is None:
                        table.add_row(
                            display_escaped,
                            "[red]No match found[/red]",
                            "-",
                            "-",
                            "-",
                            "[red]FAILED[/red]",
                        )
                        processed.append({"file": display_label, "status": "not_found"})
                        progress.advance(task)
                        continue

                    tmdb_id = int(raw_id)
                    matched_title = (
                        match_candidate.get("title")
                        or match_candidate.get("name")
                        or media_meta.title
                    )
                    release_date = (
                        match_candidate.get("release_date")
                        or match_candidate.get("first_air_date")
                        or ""
                    )
                    year_str = release_date[:4] if release_date else "N/A"

                    external_ids = tmdb_svc.get_external_ids(tmdb_id, is_tv=media_meta.is_episode)
                    imdb_id = external_ids.get("imdb_id") or "N/A"

                    actions: list[str] = []
                    is_duplicate = tmdb_id in seen_tmdb_ids
                    seen_tmdb_ids.add(tmdb_id)

                    if favorite:
                        if is_duplicate:
                            actions.append("Favorited (already)")
                        elif not dry_run:
                            fav_ok = tmdb_svc.add_to_favorite(
                                tmdb_id, is_tv=media_meta.is_episode, favorite=True
                            )
                            actions.append("Favorited" if fav_ok else "Fav Failed")
                        else:
                            actions.append("[DRY-RUN] Favorite")

                    if sync_watchlist:
                        if is_duplicate:
                            actions.append(f"{bookmark_label} (already)")
                        elif not dry_run:
                            watch_ok = tmdb_svc.add_to_watchlist(
                                tmdb_id, is_tv=media_meta.is_episode, watchlist=True
                            )
                            actions.append(
                                bookmark_label if watch_ok else f"{bookmark_label} Failed"
                            )
                        else:
                            actions.append(f"[DRY-RUN] {bookmark_label}")

                    status_msg = " | ".join(actions) if actions else "Matched"
                    table.add_row(
                        display_escaped,
                        escape(str(matched_title)),
                        year_str,
                        str(tmdb_id),
                        str(imdb_id),
                        status_msg,
                    )
                    processed.append(
                        {
                            "file": display_label,
                            "title": matched_title,
                            "year": year_str,
                            "tmdb_id": tmdb_id,
                            "imdb_id": imdb_id,
                            "status": "success",
                        }
                    )
                    progress.advance(task)
            except (KeyboardInterrupt, RuntimeError) as exc:
                if not is_interruption(exc):
                    raise
                interrupted = True
                LOG.warning(
                    "\n⏹️  [bold yellow]INTERRUPTED[/] - TMDb querying stopped by user (Ctrl+C)."
                )

    CONSOLE.print(table)
    if not favorite and not sync_watchlist:
        CONSOLE.print(
            "[dim]Tip: Pass [bold]--bookmark[/bold] ([bold]-b[/bold]) "
            "to save matches to your TMDb Watchlist / Bookmarks.[/dim]\n"
        )
    else:
        CONSOLE.print()

    return {
        "status": "interrupted" if interrupted else "success",
        "total_files": len(media_targets),
        "processed_count": len(processed),
        "results": processed,
    }


def main() -> None:
    """Main CLI entrypoint."""
    app()


if __name__ == "__main__":
    main()
