from __future__ import annotations

import datetime
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import orjson

from cinesub.core.constants import DEFAULT_LANGUAGE
from cinesub.core.logger import LOG, create_progress
from cinesub.core.models import SubtitleMatch, SyncResult, VideoMetadata
from cinesub.core.utils import (
    find_video_files,
    has_existing_subtitle,
    normalize_language,
    parse_video_metadata,
)
from cinesub.modules.syncer import sync_subtitle_audio
from cinesub.services.opensubtitles import OpenSubtitlesService
from cinesub.services.subdl import SubdlService


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


def download_and_sync(
    video_path: str | Path,
    language: str | None = None,
    provider: str = "all",
    do_sync: bool = False,
    keep_backup: bool = False,
    force: bool = False,
    sync_engine: str = "ffsubsync",
    use_lang_suffix: bool = False,
    dry_run: bool = False,
) -> tuple[VideoMetadata, SubtitleMatch | None, SyncResult | None, Path]:
    """Search, download best subtitle match, and optionally perform audio synchronization."""
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
        return video_meta, None, None, existing_sub

    if use_lang_suffix:
        target_srt = path.parent / f"{path.stem}.{target_lang}.srt"
    else:
        target_srt = path.parent / f"{path.stem}.srt"

    LOG.info(f"Analyzing media file: [white]{path.name}[/white]")
    video_meta = parse_video_metadata(path, compute_hash=True)

    services = get_active_services(provider)
    if not services:
        raise RuntimeError(
            "No subtitle providers configured. "
            "Please add OPENSUBTITLES_API_KEY or SUBDL_API_KEY to your .env file."
        )

    all_matches: list[SubtitleMatch] = []
    for name, svc in services.items():
        LOG.info(f"Querying {name.upper()} API for [{target_lang.upper()}] subtitles...")
        all_matches.extend(svc.search(video_meta, language=target_lang))

    if not all_matches:
        raise ValueError(f"No [{target_lang.upper()}] subtitles found for '{video_meta.title}'.")

    all_matches.sort(key=lambda m: m.score, reverse=True)
    best_match = all_matches[0]

    if best_match.matched_by_hash:
        LOG.success(f"Matched by exact Video Hash via {best_match.provider.upper()}")
    else:
        LOG.info(
            f"Selected best candidate via {best_match.provider.upper()}: {best_match.release_name}"
        )

    sync_result: SyncResult | None = None

    if dry_run:
        LOG.info(
            f"[yellow][DRY-RUN][/yellow] Would download into: [white]{target_srt.name}[/white]"
        )
        if do_sync:
            sync_result = SyncResult(
                success=True,
                video_path=path,
                srt_path=target_srt,
                offset_seconds=0.0,
                message="Simulated (Dry Run)",
            )
    else:
        LOG.info(f"Downloading into: [white]{target_srt.name}[/white]")
        service_inst = services[best_match.provider]
        service_inst.download(best_match, destination=target_srt)

        if do_sync:
            LOG.info(f"Synchronizing subtitle against video audio track with {sync_engine}...")
            sync_result = sync_subtitle_audio(
                video_path=path,
                srt_path=target_srt,
                keep_backup=keep_backup,
                engine=sync_engine,
            )
            LOG.success(sync_result.message)

    return video_meta, best_match, sync_result, target_srt


def _format_sync_item_report(
    meta: VideoMetadata,
    match: SubtitleMatch | None,
    sync_res: SyncResult | None,
    srt_p: Path,
    status: str = "success",
) -> dict[str, Any]:
    return {
        "video_file": meta.file_path.name,
        "video_path": str(meta.file_path),
        "title": meta.title,
        "year": meta.year,
        "is_episode": meta.is_episode,
        "moviehash": meta.moviehash,
        "subtitle": {
            "id": match.id if match else "existing",
            "provider": match.provider if match else "local",
            "language": match.language if match else "local",
            "release_name": match.release_name if match else srt_p.name,
            "matched_by_hash": match.matched_by_hash if match else False,
            "score": match.score if match else 100.0,
            "saved_path": str(srt_p),
        },
        "sync": {
            "success": sync_res.success if sync_res else None,
            "offset_seconds": sync_res.offset_seconds if sync_res else None,
            "framerate_scale": sync_res.framerate_scale if sync_res else None,
            "message": (
                sync_res.message
                if sync_res
                else (
                    "Skipped (Existing Subtitle)"
                    if status == "skipped"
                    else ("Simulated (Dry Run)" if status == "dry_run" else "Skipped (Disabled)")
                )
            ),
        },
        "status": status,
    }


def download_and_sync_batch(
    target_path: str | Path,
    language: str | None = None,
    provider: str = "all",
    do_sync: bool = False,
    keep_backup: bool = False,
    force: bool = False,
    threads: int = 4,
    json_path: Path | None = None,
    sync_engine: str = "ffsubsync",
    use_lang_suffix: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Batch search and download subtitles across multiple files with multithreading."""
    services = get_active_services(provider)
    if not services:
        raise RuntimeError(
            "No subtitle providers configured. "
            "Please add OPENSUBTITLES_API_KEY or SUBDL_API_KEY to your .env file."
        )

    start_time = time.perf_counter()
    video_files = find_video_files(target_path)
    if not video_files:
        raise FileNotFoundError(f"No valid video files found in: {target_path}")

    report_items: list[dict[str, Any]] = []
    successful_count = 0
    skipped_count = 0
    dry_run_count = 0
    failed_count = 0

    def _worker(v_path: Path) -> dict[str, Any]:
        try:
            meta, match, sync_res, srt_p = download_and_sync(
                video_path=v_path,
                language=language,
                provider=provider,
                do_sync=do_sync,
                keep_backup=keep_backup,
                force=force,
                sync_engine=sync_engine,
                use_lang_suffix=use_lang_suffix,
                dry_run=dry_run,
            )
            if match is None:
                item_status = "skipped"
            elif dry_run:
                item_status = "dry_run"
            else:
                item_status = "success"
            return _format_sync_item_report(meta, match, sync_res, srt_p, status=item_status)
        except Exception as exc:
            LOG.error(f"Failed processing {v_path.name}: {exc}")
            return {
                "video_file": v_path.name,
                "video_path": str(v_path),
                "status": "failed",
                "error": str(exc),
            }

    if len(video_files) == 1:
        item_report = _worker(video_files[0])
        report_items.append(item_report)
        st = item_report.get("status")
        if st == "success":
            successful_count += 1
        elif st == "dry_run":
            dry_run_count += 1
        elif st == "skipped":
            skipped_count += 1
        else:
            failed_count += 1
    else:
        LOG.info(
            f"Found {len(video_files)} video files. "
            f"Processing with [bold cyan]{threads}[/bold cyan] threads..."
        )

        progress = create_progress()
        with progress, ThreadPoolExecutor(max_workers=min(threads, len(video_files))) as executor:
            task = progress.add_task("[cyan]Processing subtitles...", total=len(video_files))
            try:
                future_map = {executor.submit(_worker, vf): vf for vf in video_files}
                for future in as_completed(future_map):
                    item_report = future.result()
                    report_items.append(item_report)
                    st = item_report.get("status")
                    if st == "success":
                        successful_count += 1
                    elif st == "dry_run":
                        dry_run_count += 1
                    elif st == "skipped":
                        skipped_count += 1
                    else:
                        failed_count += 1
                    progress.advance(task)
                    future_map.pop(future, None)
            except KeyboardInterrupt:
                LOG.warning("\nBatch execution interrupted by user. Stopping worker threads...")
                executor.shutdown(wait=False, cancel_futures=True)
                raise

    elapsed = max(0.001, time.perf_counter() - start_time)
    duration_sec = round(elapsed, 2)
    throughput = round(len(video_files) / elapsed, 1)

    report = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "command": "sync",
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
    meta: VideoMetadata, items: list[tuple[SubtitleMatch, Path]], status: str = "success"
) -> dict[str, Any]:
    return {
        "video_file": meta.file_path.name,
        "video_path": str(meta.file_path),
        "subtitles": [
            {
                "id": m.id,
                "provider": m.provider,
                "release_name": m.release_name,
                "score": m.score,
                "saved_path": str(p),
            }
            for m, p in items
        ],
        "status": status,
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
            "No subtitle providers configured. "
            "Please add OPENSUBTITLES_API_KEY or SUBDL_API_KEY to your .env file."
        )

    start_time = time.perf_counter()
    video_files = find_video_files(target_path)
    if not video_files:
        raise FileNotFoundError(f"No valid video files found in: {target_path}")

    report_items: list[dict[str, Any]] = []
    successful_count = 0
    dry_run_count = 0
    failed_count = 0

    def _worker(v_path: Path) -> dict[str, Any]:
        try:
            meta, items = download_bulk(
                video_path=v_path,
                language=language,
                limit=limit,
                provider=provider,
                force=force,
                dry_run=dry_run,
            )
            status = "dry_run" if dry_run else "success"
            return _format_bulk_item_report(meta, items, status=status)
        except Exception as exc:
            LOG.error(f"Failed bulk retrieval for {v_path.name}: {exc}")
            return {
                "video_file": v_path.name,
                "video_path": str(v_path),
                "status": "failed",
                "error": str(exc),
            }

    if len(video_files) == 1:
        item_report = _worker(video_files[0])
        report_items.append(item_report)
        st = item_report.get("status")
        if st == "success":
            successful_count += 1
        elif st == "dry_run":
            dry_run_count += 1
        else:
            failed_count += 1
    else:
        LOG.info(
            f"Found {len(video_files)} video files for bulk download. "
            f"Processing with [bold cyan]{threads}[/bold cyan] threads..."
        )

        progress = create_progress()
        with progress, ThreadPoolExecutor(max_workers=min(threads, len(video_files))) as executor:
            task = progress.add_task("[cyan]Bulk downloading...", total=len(video_files))
            try:
                future_map = {executor.submit(_worker, vf): vf for vf in video_files}
                for future in as_completed(future_map):
                    item_report = future.result()
                    report_items.append(item_report)
                    st = item_report.get("status")
                    if st == "success":
                        successful_count += 1
                    elif st == "dry_run":
                        dry_run_count += 1
                    else:
                        failed_count += 1
                    progress.advance(task)
                    future_map.pop(future, None)
            except KeyboardInterrupt:
                LOG.warning("\nBatch execution interrupted by user. Stopping worker threads...")
                executor.shutdown(wait=False, cancel_futures=True)
                raise

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
