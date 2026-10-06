from __future__ import annotations

import datetime
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import orjson

from cinesub.core.constants import DEFAULT_LANGUAGE, UPGRADE_HYSTERESIS_DELTA
from cinesub.core.logger import LOG, create_progress
from cinesub.core.models import SubtitleMatch, VideoMetadata
from cinesub.core.utils import (
    evaluate_local_subtitle_score,
    find_video_files,
    get_subtitle_max_timestamp,
    has_existing_subtitle,
    normalize_language,
    parse_video_metadata,
    validate_subtitle_timing,
)
from cinesub.services.betaseries import BetaSeriesService
from cinesub.services.opensubtitles import OpenSubtitlesService
from cinesub.services.subdl import SubdlService
from cinesub.services.subsource import SubsourceService
from cinesub.services.subsro import SubsRoService
from cinesub.services.tmdb import TmdbService


def get_active_services(provider_filter: str = "all") -> dict[str, Any]:
    """Retrieve initialized service instances based on provider filter and configured API keys."""
    services: dict[str, Any] = {}
    chosen = provider_filter.lower().strip()

    if chosen in ("all", "opensubtitles", "os"):
        os_svc = OpenSubtitlesService()
        if os_svc.is_configured:
            services["opensubtitles"] = os_svc

    if chosen in ("all", "subdl"):
        subdl_svc = SubdlService()
        if subdl_svc.is_configured:
            services["subdl"] = subdl_svc

    if chosen in ("all", "subsource", "ss"):
        ss_svc = SubsourceService()
        if ss_svc.is_configured:
            services["subsource"] = ss_svc

    if chosen in ("all", "subsro", "sr", "subs.ro"):
        subsro_svc = SubsRoService()
        if subsro_svc.is_configured:
            services["subsro"] = subsro_svc

    if chosen in ("all", "betaseries", "bs"):
        bs_svc = BetaSeriesService()
        if bs_svc.is_configured:
            services["betaseries"] = bs_svc

    return services


def save_report_output(report: dict[str, Any], json_path: Path | str) -> Path:
    """Save report as formatted indented JSON or line-delimited JSONL with atomic replace."""
    out_p = Path(json_path).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)
    temp_p = out_p.with_name(f".{out_p.name}.{os.getpid()}.tmp")

    try:
        if out_p.suffix.lower() == ".jsonl":
            lines: list[bytes] = [orjson.dumps(r) for r in report.get("results", [])]
            summary_line = orjson.dumps(
                {
                    "summary": {
                        "timestamp": report.get("timestamp"),
                        "command": report.get("command"),
                        "dry_run": report.get("dry_run", False),
                        "total_files": report.get("total_files"),
                        "successful": report.get("successful"),
                        "skipped": report.get("skipped"),
                        "dry_run_count": report.get("dry_run_count", 0),
                        "failed": report.get("failed"),
                        "duration_seconds": report.get("duration_seconds"),
                        "throughput_files_per_sec": report.get("throughput_files_per_sec"),
                    }
                }
            )
            lines.append(summary_line)
            temp_p.write_bytes(b"\n".join(lines) + b"\n")
        else:
            temp_p.write_bytes(orjson.dumps(report, option=orjson.OPT_INDENT_2))

        temp_p.replace(out_p)
    except Exception:
        if temp_p.exists():
            temp_p.unlink(missing_ok=True)
        raise

    LOG.success(f"Report saved to: [white]{out_p}[/white]")
    return out_p


def download_subtitle(
    video_path: str | Path,
    language: str | None = None,
    provider: str = "all",
    force: bool = False,
    use_lang_suffix: bool = False,
    dry_run: bool = False,
) -> tuple[VideoMetadata, SubtitleMatch | None, Path]:
    """Search and download the best subtitle match for a video file."""
    path = Path(video_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {path}")

    target_lang = normalize_language(language or DEFAULT_LANGUAGE)
    existing_sub = has_existing_subtitle(path, target_lang)

    if existing_sub and not force:
        LOG.info(
            f"Skipping {path.name} (subtitle already exists: [white]{existing_sub.name}[/white])"
        )
        video_meta = parse_video_metadata(path, compute_hash=False)
        return video_meta, None, existing_sub

    if use_lang_suffix:
        target_srt = path.parent / f"{path.stem}.{target_lang}.srt"
    else:
        target_srt = path.parent / f"{path.stem}.srt"

    LOG.info(f"Analyzing media file: [white]{path.name}[/white]")
    video_meta = parse_video_metadata(path, compute_hash=True, extract_duration=True)

    tmdb_svc = TmdbService()
    if tmdb_svc.is_configured:
        try:
            video_meta = tmdb_svc.enrich_video_metadata(video_meta)
            if video_meta.imdb_id:
                LOG.debug(f"Resolved IMDb ID via TMDb: {video_meta.imdb_id}")
        except Exception as exc:
            LOG.debug(f"TMDb enrichment warning: {exc}")

    services = get_active_services(provider)
    if not services:
        raise RuntimeError(
            "No subtitle providers configured. Please add OPENSUBTITLES_API_KEY, "
            "SUBDL_API_KEY, SUBSOURCE_API_KEY, SUBSRO_API_KEY, or "
            "BETASERIES_API_KEY to your .env file."
        )

    all_matches: list[SubtitleMatch] = []
    for name, svc in services.items():
        LOG.info(f"Querying {name.upper()} API for [{target_lang.upper()}] subtitles...")
        all_matches.extend(svc.search(video_meta, language=target_lang))

    if not all_matches:
        raise ValueError(f"No [{target_lang.upper()}] subtitles found for '{video_meta.title}'.")

    all_matches.sort(key=lambda m: m.score, reverse=True)
    best_match = all_matches[0]

    # Quality downgrade protection: never overwrite higher-fidelity existing subtitle
    if existing_sub and force:
        existing_sub_score = evaluate_local_subtitle_score(existing_sub, video_meta)
        if best_match.score < (existing_sub_score + UPGRADE_HYSTERESIS_DELTA):
            LOG.warning(
                f"Downgrade protection: existing subtitle quality ({existing_sub_score:.1f}) "
                f"exceeds candidate match ({best_match.score:.1f}). Preserving local file."
            )
            return video_meta, None, existing_sub

    if best_match.matched_by_hash:
        LOG.success(f"Matched by exact Video Hash via {best_match.provider.upper()}")
    else:
        LOG.info(
            f"Selected best candidate via {best_match.provider.upper()}: {best_match.release_name}"
        )

    if dry_run:
        LOG.info(
            f"[yellow][DRY-RUN][/yellow] Would download into: [white]{target_srt.name}[/white]"
        )
    else:
        LOG.info(f"Downloading into: [white]{target_srt.name}[/white]")
        service_inst = services[best_match.provider]
        service_inst.download(best_match, destination=target_srt)

        # Video duration timing invariant validation
        if video_meta.duration and video_meta.duration > 0.0 and target_srt.is_file():
            sub_max_time = get_subtitle_max_timestamp(target_srt.read_bytes())
            if not validate_subtitle_timing(sub_max_time, video_meta.duration):
                LOG.warning(
                    f"Timing mismatch: subtitle timestamp ({sub_max_time:.1f}s) conflicts with "
                    f"video duration ({video_meta.duration:.1f}s)."
                )

    return video_meta, best_match, target_srt


def _format_download_report(
    video_meta: VideoMetadata,
    subtitle_match: SubtitleMatch | None,
    subtitle_path: Path,
    status: str = "success",
) -> dict[str, Any]:
    return {
        "video_file": video_meta.file_path.name,
        "video_path": str(video_meta.file_path),
        "title": video_meta.title,
        "year": video_meta.year,
        "is_episode": video_meta.is_episode,
        "moviehash": video_meta.moviehash,
        "subtitle": {
            "id": subtitle_match.id if subtitle_match else "existing",
            "provider": subtitle_match.provider if subtitle_match else "local",
            "language": subtitle_match.language if subtitle_match else "local",
            "release_name": (subtitle_match.release_name if subtitle_match else subtitle_path.name),
            "matched_by_hash": subtitle_match.matched_by_hash if subtitle_match else False,
            "score": subtitle_match.score if subtitle_match else 100.0,
            "saved_path": str(subtitle_path),
        },
        "status": status,
    }


def _tally_batch_results(
    report_items: list[dict[str, Any]],
) -> tuple[int, int, int, int]:
    """Tally batch processing outcome counts deterministically."""
    successful = sum(1 for result_entry in report_items if result_entry.get("status") == "success")
    dry_run = sum(1 for result_entry in report_items if result_entry.get("status") == "dry_run")
    skipped = sum(1 for result_entry in report_items if result_entry.get("status") == "skipped")
    failed = sum(1 for result_entry in report_items if result_entry.get("status") == "failed")
    return successful, dry_run, skipped, failed


def _batch_download_worker(
    video_file_path: Path,
    language: str | None,
    provider: str,
    force: bool,
    use_lang_suffix: bool,
    dry_run: bool,
) -> dict[str, Any]:
    """Worker task executing subtitle download workflow for a single video file."""
    try:
        (
            video_meta,
            subtitle_match,
            subtitle_path,
        ) = download_subtitle(
            video_path=video_file_path,
            language=language,
            provider=provider,
            force=force,
            use_lang_suffix=use_lang_suffix,
            dry_run=dry_run,
        )
        if subtitle_match is None:
            item_status = "skipped"
        elif dry_run:
            item_status = "dry_run"
        else:
            item_status = "success"
        return _format_download_report(
            video_meta=video_meta,
            subtitle_match=subtitle_match,
            subtitle_path=subtitle_path,
            status=item_status,
        )
    except Exception as exc:
        LOG.error(f"Failed processing {video_file_path.name}: {exc}")
        return {
            "video_file": video_file_path.name,
            "video_path": str(video_file_path),
            "status": "failed",
            "error": str(exc),
        }


def download_batch(
    target_path: str | Path,
    language: str | None = None,
    provider: str = "all",
    force: bool = False,
    threads: int = 4,
    json_path: Path | None = None,
    use_lang_suffix: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Batch search and download subtitles across multiple files with multithreading."""
    services = get_active_services(provider)
    if not services:
        raise RuntimeError(
            "No subtitle providers configured. Please add OPENSUBTITLES_API_KEY, "
            "SUBDL_API_KEY, SUBSOURCE_API_KEY, SUBSRO_API_KEY, or "
            "BETASERIES_API_KEY to your .env file."
        )

    start_time = time.perf_counter()
    video_files = find_video_files(target_path)
    if not video_files:
        raise FileNotFoundError(f"No valid video files found in: {target_path}")

    report_items: list[dict[str, Any]] = []

    if len(video_files) == 1:
        report_items.append(
            _batch_download_worker(
                video_files[0],
                language,
                provider,
                force,
                use_lang_suffix,
                dry_run,
            )
        )
    else:
        LOG.info(
            f"Found {len(video_files)} video files. "
            f"Processing with [bold cyan]{threads}[/bold cyan] threads..."
        )

        progress = create_progress()
        with progress, ThreadPoolExecutor(max_workers=min(threads, len(video_files))) as executor:
            task = progress.add_task("[cyan]Processing subtitles...", total=len(video_files))
            try:
                future_map = {
                    executor.submit(
                        _batch_download_worker,
                        vf,
                        language,
                        provider,
                        force,
                        use_lang_suffix,
                        dry_run,
                    ): vf
                    for vf in video_files
                }
                for future in as_completed(future_map):
                    report_items.append(future.result())
                    progress.advance(task)
                    future_map.pop(future, None)
            except KeyboardInterrupt:
                LOG.warning("\nBatch execution interrupted by user. Stopping worker threads...")
                executor.shutdown(wait=True, cancel_futures=True)
                raise

    successful_count, dry_run_count, skipped_count, failed_count = _tally_batch_results(
        report_items
    )

    elapsed = max(0.001, time.perf_counter() - start_time)
    duration_sec = round(elapsed, 2)
    throughput = round(len(video_files) / elapsed, 1)

    report = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "command": "download",
        "dry_run": dry_run,
        "total_files": len(video_files),
        "successful": successful_count,
        "skipped": skipped_count,
        "dry_run_count": dry_run_count,
        "failed": failed_count,
        "duration_seconds": duration_sec,
        "throughput_files_per_sec": throughput,
        "results": report_items,
    }

    if json_path:
        save_report_output(report, json_path)

    return report


# Backward compatibility aliases
download_and_sync = download_subtitle
download_and_sync_batch = download_batch
_format_sync_item_report = _format_download_report
_batch_sync_worker = _batch_download_worker


def download_bulk(
    video_path: str | Path,
    language: str | None = None,
    limit: int = 5,
    provider: str = "all",
    force: bool = False,
    dry_run: bool = False,
) -> tuple[VideoMetadata, list[tuple[SubtitleMatch, Path]]]:
    """Download multiple subtitle alternatives for manual inspection."""
    path = Path(video_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {path}")

    target_lang = normalize_language(language or DEFAULT_LANGUAGE)
    video_meta = parse_video_metadata(path, compute_hash=True)

    tmdb_svc = TmdbService()
    if tmdb_svc.is_configured:
        try:
            video_meta = tmdb_svc.enrich_video_metadata(video_meta)
        except Exception as exc:
            LOG.debug(f"TMDb enrichment warning: {exc}")

    services = get_active_services(provider)
    if not services:
        raise RuntimeError("No subtitle providers configured. Check your .env file.")

    candidates: list[SubtitleMatch] = []
    for name, svc in services.items():
        LOG.info(f"Querying {name.upper()} API...")
        candidates.extend(svc.search(video_meta, language=target_lang))

    if not candidates:
        raise ValueError(f"No [{target_lang.upper()}] subtitles found for '{video_meta.title}'.")

    candidates.sort(key=lambda m: m.score, reverse=True)
    selected: list[SubtitleMatch] = []
    seen_releases: set[str] = set()
    selected_ids: set[str] = set()

    for c in candidates:
        key = c.release_name.strip().lower()
        if key not in seen_releases:
            seen_releases.add(key)
            selected_ids.add(c.id)
            selected.append(c)
        if len(selected) >= limit:
            break

    if len(selected) < limit:
        for c in candidates:
            if c.id not in selected_ids:
                selected_ids.add(c.id)
                selected.append(c)
            if len(selected) >= limit:
                break

    downloaded: list[tuple[SubtitleMatch, Path]] = []
    video_stem = path.stem

    for idx, match in enumerate(selected, start=1):
        filename = f"{video_stem}_{idx}_{match.provider}.srt"
        dest_path = path.parent / filename

        if dest_path.exists() and not force:
            LOG.debug(f"Skipping existing file: {filename}")
            downloaded.append((match, dest_path))
            continue

        if dry_run:
            LOG.info(
                f"[yellow][DRY-RUN][/yellow] Would download [{idx}/{len(selected)}]: "
                f"[white]{filename}[/white]"
            )
            downloaded.append((match, dest_path))
        else:
            LOG.info(f"Downloading [{idx}/{len(selected)}]: [white]{filename}[/white]")
            svc = services.get(match.provider)
            if svc:
                svc.download(match, destination=dest_path)
                downloaded.append((match, dest_path))

    return video_meta, downloaded


def _format_bulk_item_report(
    video_meta: VideoMetadata,
    downloaded_subtitles: list[tuple[SubtitleMatch, Path]],
    status: str = "success",
) -> dict[str, Any]:
    return {
        "video_file": video_meta.file_path.name,
        "video_path": str(video_meta.file_path),
        "subtitles": [
            {
                "id": match_item.id,
                "provider": match_item.provider,
                "release_name": match_item.release_name,
                "score": match_item.score,
                "saved_path": str(saved_path),
            }
            for match_item, saved_path in downloaded_subtitles
        ],
        "status": status,
    }


def _batch_bulk_worker(
    video_file_path: Path,
    language: str | None,
    limit: int,
    provider: str,
    force: bool,
    dry_run: bool,
) -> dict[str, Any]:
    """Worker task executing bulk subtitle retrieval for a single video file."""
    try:
        video_meta, downloaded_subtitles = download_bulk(
            video_path=video_file_path,
            language=language,
            limit=limit,
            provider=provider,
            force=force,
            dry_run=dry_run,
        )
        item_status = "dry_run" if dry_run else "success"
        return _format_bulk_item_report(video_meta, downloaded_subtitles, status=item_status)
    except Exception as exc:
        LOG.error(f"Failed bulk retrieval for {video_file_path.name}: {exc}")
        return {
            "video_file": video_file_path.name,
            "video_path": str(video_file_path),
            "status": "failed",
            "error": str(exc),
        }


def download_bulk_batch(
    target_path: str | Path,
    language: str | None = None,
    limit: int = 5,
    provider: str = "all",
    force: bool = False,
    threads: int = 4,
    json_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Batch download multiple subtitle alternatives for all files in a directory."""
    services = get_active_services(provider)
    if not services:
        raise RuntimeError(
            "No subtitle providers configured. Please add OPENSUBTITLES_API_KEY, "
            "SUBDL_API_KEY, SUBSOURCE_API_KEY, SUBSRO_API_KEY, or "
            "BETASERIES_API_KEY to your .env file."
        )

    start_time = time.perf_counter()
    video_files = find_video_files(target_path)
    if not video_files:
        raise FileNotFoundError(f"No valid video files found in: {target_path}")

    report_items: list[dict[str, Any]] = []

    if len(video_files) == 1:
        report_items.append(
            _batch_bulk_worker(
                video_files[0],
                language,
                limit,
                provider,
                force,
                dry_run,
            )
        )
    else:
        LOG.info(
            f"Found {len(video_files)} video files for bulk download. "
            f"Processing with [bold cyan]{threads}[/bold cyan] threads..."
        )

        progress = create_progress()
        with progress, ThreadPoolExecutor(max_workers=min(threads, len(video_files))) as executor:
            task = progress.add_task("[cyan]Bulk downloading...", total=len(video_files))
            try:
                future_map = {
                    executor.submit(
                        _batch_bulk_worker,
                        vf,
                        language,
                        limit,
                        provider,
                        force,
                        dry_run,
                    ): vf
                    for vf in video_files
                }
                for future in as_completed(future_map):
                    report_items.append(future.result())
                    progress.advance(task)
                    future_map.pop(future, None)
            except KeyboardInterrupt:
                LOG.warning("\nBatch execution interrupted by user. Stopping worker threads...")
                executor.shutdown(wait=True, cancel_futures=True)
                raise

    successful_count, dry_run_count, _, failed_count = _tally_batch_results(report_items)

    elapsed = max(0.001, time.perf_counter() - start_time)
    duration_sec = round(elapsed, 2)
    throughput = round(len(video_files) / elapsed, 1)

    report = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "command": "bulk",
        "dry_run": dry_run,
        "total_files": len(video_files),
        "successful": successful_count,
        "dry_run_count": dry_run_count,
        "failed": failed_count,
        "duration_seconds": duration_sec,
        "throughput_files_per_sec": throughput,
        "results": report_items,
    }

    if json_path:
        save_report_output(report, json_path)

    return report
