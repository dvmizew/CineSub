from __future__ import annotations

import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import orjson

from cinesub.core.constants import DEFAULT_LANGUAGE
from cinesub.core.logger import LOG, create_progress
from cinesub.core.models import SubtitleMatch, SyncResult, VideoMetadata
from cinesub.core.utils import find_video_files, normalize_language, parse_video_metadata
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


def download_and_sync(
    video_path: str | Path,
    language: str | None = None,
    provider: str = "all",
    do_sync: bool = False,
    keep_backup: bool = False,
    force: bool = False,
    sync_engine: str = "ffsubsync",
) -> tuple[VideoMetadata, SubtitleMatch, SyncResult | None, Path]:
    """Search, download best subtitle match, and optionally perform audio synchronization."""
    path = Path(video_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {path}")

    target_lang = normalize_language(language or DEFAULT_LANGUAGE)
    target_srt = path.parent / f"{path.stem}.srt"

    if target_srt.exists() and not force:
        LOG.warning(f"File {target_srt.name} already exists. Overwriting with matching subtitle.")

    LOG.info(f"Analyzing media file: [white]{path.name}[/white]")
    video_meta = parse_video_metadata(path, compute_hash=True)

    services = get_active_services(provider)
    if not services:
        raise RuntimeError(
            "No subtitle providers configured. "
            "Please add OPENSUBTITLES_API_KEY or SUBDL_API_KEY to your .env file."
        )

    all_matches: list[SubtitleMatch] = []
    hash_matches: list[SubtitleMatch] = []

    for name, svc in services.items():
        LOG.info(f"Querying {name.upper()} API for [{target_lang.upper()}] subtitles...")
        results = svc.search(video_meta, language=target_lang)
        for match in results:
            if match.matched_by_hash:
                hash_matches.append(match)
            all_matches.append(match)

    if not all_matches:
        raise ValueError(f"No [{target_lang.upper()}] subtitles found for '{video_meta.title}'.")

    if hash_matches:
        hash_matches.sort(key=lambda m: m.score, reverse=True)
        best_match = hash_matches[0]
        LOG.success(f"Matched by exact Video Hash via {best_match.provider.upper()}")
    else:
        all_matches.sort(key=lambda m: m.score, reverse=True)
        best_match = all_matches[0]
        LOG.info(
            f"Selected best candidate via {best_match.provider.upper()}: {best_match.release_name}"
        )

    LOG.info(f"Downloading into: [white]{target_srt.name}[/white]")
    service_inst = services[best_match.provider]
    service_inst.download(best_match, destination=target_srt)

    sync_result: SyncResult | None = None
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
    meta: VideoMetadata, match: SubtitleMatch, sync_res: SyncResult | None, srt_p: Path
) -> dict[str, Any]:
    return {
        "video_file": meta.file_path.name,
        "video_path": str(meta.file_path),
        "title": meta.title,
        "year": meta.year,
        "is_episode": meta.is_episode,
        "moviehash": meta.moviehash,
        "subtitle": {
            "id": match.id,
            "provider": match.provider,
            "language": match.language,
            "release_name": match.release_name,
            "matched_by_hash": match.matched_by_hash,
            "score": match.score,
            "saved_path": str(srt_p),
        },
        "sync": {
            "success": sync_res.success if sync_res else None,
            "offset_seconds": sync_res.offset_seconds if sync_res else None,
            "framerate_scale": sync_res.framerate_scale if sync_res else None,
            "message": sync_res.message if sync_res else "Skipped (Disabled)",
        },
        "status": "success",
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
) -> dict[str, Any]:
    """Batch search and download subtitles across multiple files with multithreading."""
    video_files = find_video_files(target_path)
    if not video_files:
        raise FileNotFoundError(f"No valid video files found in: {target_path}")

    report_items: list[dict[str, Any]] = []
    successful_count = 0
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
            )
            return _format_sync_item_report(meta, match, sync_res, srt_p)
        except Exception as exc:
            LOG.error(f"Failed processing {v_path.name}: {exc}")
            return {
                "video_file": v_path.name,
                "video_path": str(v_path),
                "status": "failed",
                "error": str(exc),
            }

    if len(video_files) == 1:
        # Single file execution
        item_report = _worker(video_files[0])
        report_items.append(item_report)
        if item_report.get("status") == "success":
            successful_count += 1
        else:
            failed_count += 1
    else:
        # Batch multithreaded execution
        LOG.info(
            f"Found {len(video_files)} video files. "
            f"Processing with [bold cyan]{threads}[/bold cyan] threads..."
        )

        progress = create_progress()
        with progress:
            task = progress.add_task("[cyan]Processing subtitles...", total=len(video_files))
            executor = ThreadPoolExecutor(max_workers=min(threads, len(video_files)))
            try:
                future_map = {executor.submit(_worker, vf): vf for vf in video_files}
                for future in as_completed(future_map):
                    item_report = future.result()
                    report_items.append(item_report)
                    if item_report.get("status") == "success":
                        successful_count += 1
                    else:
                        failed_count += 1
                    progress.advance(task)
            except KeyboardInterrupt:
                LOG.warning("\nBatch execution interrupted by user. Stopping worker threads...")
                executor.shutdown(wait=False, cancel_futures=True)
                raise
            finally:
                executor.shutdown(wait=True)

    report = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "command": "sync",
        "total_files": len(video_files),
        "successful": successful_count,
        "failed": failed_count,
        "results": report_items,
    }

    if json_path:
        out_p = Path(json_path).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_bytes(orjson.dumps(report, option=orjson.OPT_INDENT_2))
        LOG.success(f"JSON report saved to: [white]{out_p}[/white]")

    return report


def download_bulk(
    video_path: str | Path,
    language: str | None = None,
    limit: int = 5,
    provider: str = "all",
    force: bool = False,
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

        LOG.info(f"Downloading [{idx}/{len(selected)}]: [white]{filename}[/white]")
        svc = services.get(match.provider)
        if svc:
            svc.download(match, destination=dest_path)
            downloaded.append((match, dest_path))

    return video_meta, downloaded


def _format_bulk_item_report(
    meta: VideoMetadata, items: list[tuple[SubtitleMatch, Path]]
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
        "status": "success",
    }


def download_bulk_batch(
    target_path: str | Path,
    language: str | None = None,
    limit: int = 5,
    provider: str = "all",
    force: bool = False,
    threads: int = 4,
    json_path: Path | None = None,
) -> dict[str, Any]:
    """Batch download multiple subtitle alternatives for all files in a directory."""
    video_files = find_video_files(target_path)
    if not video_files:
        raise FileNotFoundError(f"No valid video files found in: {target_path}")

    report_items: list[dict[str, Any]] = []
    successful_count = 0
    failed_count = 0

    def _worker(v_path: Path) -> dict[str, Any]:
        try:
            meta, items = download_bulk(
                video_path=v_path,
                language=language,
                limit=limit,
                provider=provider,
                force=force,
            )
            return _format_bulk_item_report(meta, items)
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
        if item_report.get("status") == "success":
            successful_count += 1
        else:
            failed_count += 1
    else:
        LOG.info(
            f"Found {len(video_files)} video files for bulk download. "
            f"Processing with [bold cyan]{threads}[/bold cyan] threads..."
        )

        progress = create_progress()
        with progress:
            task = progress.add_task("[cyan]Bulk downloading...", total=len(video_files))
            executor = ThreadPoolExecutor(max_workers=min(threads, len(video_files)))
            try:
                future_map = {executor.submit(_worker, vf): vf for vf in video_files}
                for future in as_completed(future_map):
                    item_report = future.result()
                    report_items.append(item_report)
                    if item_report.get("status") == "success":
                        successful_count += 1
                    else:
                        failed_count += 1
                    progress.advance(task)
            except KeyboardInterrupt:
                LOG.warning("\nBatch execution interrupted by user. Stopping worker threads...")
                executor.shutdown(wait=False, cancel_futures=True)
                raise
            finally:
                executor.shutdown(wait=True)

    report = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "command": "bulk",
        "total_files": len(video_files),
        "successful": successful_count,
        "failed": failed_count,
        "results": report_items,
    }

    if json_path:
        out_p = Path(json_path).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_bytes(orjson.dumps(report, option=orjson.OPT_INDENT_2))
        LOG.success(f"JSON report saved to: [white]{out_p}[/white]")

    return report
