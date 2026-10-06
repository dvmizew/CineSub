# CineSub

Automated multi-provider subtitle downloader and media library management CLI for movies and TV series.

CineSub searches across top subtitle providers (**OpenSubtitles.com**, **SubDL**, **SubSource**, **Subs.ro**, **BetaSeries**, **Gestdown**, **BSPlayer**, **AnimeTosho**, and **Assrt.net**), matches files using exact 64-bit video hashes (`OSHash`) or release metadata enriched via **TMDb**, validates subtitle timing invariants against video duration, and downloads clean, UTF-8 normalized `.srt` companion subtitles.

Supports single video files or batch processing of entire media folders with multithreading and automatic token-bucket rate limiting.

---

## How It Works

```
Video File ──► [ 1. Compute 64-bit OSHash ] ──► Exact hash match across providers?
                     │                                   │
                    No                                  Yes
                     ▼                                   ▼
             [ 2. Parse Metadata & TMDb ]       Download Highest-Scoring Subtitle
             (Title, Year, Season/Episode,               │
              Resolution, Release Group, IMDb ID)        ▼
                     │                          [ 4. Validate & Normalize ]
                     ▼                          • Timing invariant check vs duration
          Query Multi-Provider APIs             • UTF-8 encoding normalization
          (OS, SubDL, SubSource, Subs.ro,       • Downgrade protection check
           BetaSeries, Gestdown, BSPlayer,               │
           AnimeTosho, Assrt.net)                        ▼
                     │                          Atomic POSIX file replace (.srt)
                     ▼                                   │
          Score candidates with RapidFuzz                ▼
          & Ecosystem Group Clusters            Final Verified Subtitle
                     │
                     └─────────────────────────►
```

1. **Exact Hash Match**: Computes the 64-bit checksum over the video header and footer. If found on OpenSubtitles, BSPlayer, or supported APIs, timing is typically already tailored for that specific encode.
2. **Metadata Fallback & TMDb Enrichment**: If hash matching yields no results, `guessit` extracts media metadata (title, season/episode, release group, source) and enriches it via TMDb to resolve IMDb IDs.
3. **Multi-Provider Search & Heuristic Scoring**: Subtitle candidates from all active providers are ranked by release group clusters, exact token boundaries, and short-title penalties using `rapidfuzz`.
4. **Timing Validation & Atomic Writes**: Validates maximum subtitle timestamps against video duration to eliminate cross-movie collisions, and saves `.srt` files using atomic POSIX replacement (`os.replace`).
5. **Thread-Safe Rate Limiting**: Ensures concurrent batch jobs strictly respect provider thresholds (4.0 req/s OpenSubtitles, 8.0 req/s SubDL, 1.0 req/s SubSource, 2.0 req/s Subs.ro, 2.0 req/s BetaSeries, 2.0 req/s Gestdown, 2.0 req/s BSPlayer, 2.0 req/s AnimeTosho, 0.33 req/s Assrt, 4.0 req/s TMDb) with synchronized backoff on HTTP 429.

---

## Installation

### 1. System Dependency

`ffmpeg` and `ffprobe` are required for container subtitle extraction (`cinesub extract`) and video duration inspection.

- **Ubuntu / Debian**: `sudo apt install ffmpeg`
- **Arch Linux**: `sudo pacman -S ffmpeg`
- **Fedora**: `sudo dnf install ffmpeg`
- **macOS**: `brew install ffmpeg`
- **Windows**: `winget install Gyan.FFmpeg` or `choco install ffmpeg`

### 2. Python Package

```bash
git clone https://github.com/dvmizew/CineSub.git
cd CineSub
pip install -e .
```

*Requires Python 3.10+.*

---

## Configuration & API Credentials

| Provider | API Endpoint | Documentation & API Key Registration | Rate Limit |
| :--- | :--- | :--- | :--- |
| **OpenSubtitles.com** | `https://api.opensubtitles.com/api/v1` | [OpenSubtitles API Portal](https://www.opensubtitles.com/consumers) | 4.0 req/s (Client Cap) / 5 req/s max |
| **SubDL** | `https://api.subdl.com/api/v1` | [SubDL API Portal](https://subdl.com/api) | 8.0 req/s (600 req/min Cap) |
| **SubSource** | `https://api.subsource.net/api/v1` | [SubSource Account Portal](https://subsource.net) | 1.0 req/s (60 req/min Cap) |
| **Subs.ro** | `https://api.subs.ro/v1.0` | [Subs.ro API Documentation](https://api.subs.ro) | 2.0 req/s (Safe Client Cap) |
| **BetaSeries** | `https://api.betaseries.com` | [BetaSeries API Portal](https://www.betaseries.com/api) | 2.0 req/s (Safe Client Cap) |
| **Gestdown** | `https://api.gestdown.info` | [Gestdown / Addic7ed Proxy](https://api.gestdown.info) | 2.0 req/s (Public, No Key Required) |
| **BSPlayer** | `http://s1.api.bsplayer-subtitles.com/v1.php` | BSPlayer Community 64-bit SOAP API | 2.0 req/s (Public, No Key Required) |
| **AnimeTosho** | `https://feed.animetosho.org/json` | [AnimeTosho Feed & Attachments](https://animetosho.org) | 2.0 req/s (Public, No Key Required) |
| **Assrt.net** | `https://api.assrt.net/v1` | [Assrt.net (Shooter.cn) API](https://assrt.net) | 0.33 req/s (20 req/min Cap) |
| **TMDb** | `https://api.themoviedb.org/3` | [TMDb Developer Settings](https://developer.themoviedb.org) | 4.0 req/s (Client Cap) |

Create a `.env` file in your working directory or home directory:

```bash
cp .env.example .env
```

```ini
# OpenSubtitles.com API Key (https://www.opensubtitles.com/consumers)
OPENSUBTITLES_API_KEY=your_opensubtitles_api_key

# SubDL API Key (https://subdl.com)
SUBDL_API_KEY=your_subdl_api_key

# SubSource API Key (https://subsource.net)
SUBSOURCE_API_KEY=your_subsource_api_key

# Subs.ro API Key (https://subs.ro)
SUBSRO_API_KEY=your_subsro_api_key

# BetaSeries API Key (https://www.betaseries.com/api)
BETASERIES_API_KEY=your_betaseries_api_key

# TMDb Read Access Token or API Key (https://www.themoviedb.org/settings/api)
TMDB_READ_ACCESS_TOKEN=your_tmdb_read_access_token
TMDB_ACCOUNT_ID=your_tmdb_account_id

# Default language (ISO 639-1 code, e.g. en, ro, es, fr, de)
CINESUB_DEFAULT_LANGUAGE=en

# Request timeout in seconds
CINESUB_TIMEOUT=15
```

Verify your environment configuration:
```bash
cinesub config
```

---

## Usage Guide

### 1. `cinesub download` (or `cinesub sync`) — Search & Download Best Subtitle

Downloads the single best subtitle candidate across configured providers and saves it matching the video filename (`movie.mp4` $\to$ `movie.srt`).

Supports single video files or entire directories for batch processing with multithreading.

```bash
# Download the best Romanian subtitle for a movie
cinesub download "Dune.Part.Two.2024.1080p.WEBRip.x264-FGT.mp4" -l ro

# Download for a full TV series season with 8 parallel worker threads
cinesub download "/path/to/House.of.the.Dragon.S02" -l ro -t 8

# Save with Plex/Emby language suffix (e.g., movie.ro.srt)
cinesub download "The.Last.of.Us.S01E01.720p.HDTV.mkv" -l ro -S

# Full simulation without downloading or modifying files (Dry Run)
cinesub download "/path/to/movies" -l ro --dry-run

# Save batch report as formatted indented JSON or streaming JSONL
cinesub download "/path/to/movies" -l ro -j report.jsonl
```

**Flags & Options:**
| Option | Default | Description |
| :--- | :--- | :--- |
| `PATH` | *required* | Path to a video file or a directory containing video files |
| `-l, --language` | `en` | Subtitle language (ISO 639-1 code, e.g. `ro`, `en`, `es`) |
| `-p, --provider` | `all` | Search provider: `all`, `opensubtitles`, `subdl`, `subsource`, `subsro`, or `betaseries` |
| `-S, --lang-suffix` | `False` | Save subtitle with language tag for Plex/Emby (e.g. `movie.ro.srt`) |
| `-d, --dry-run` | `False` | Simulate search and matching without downloading or modifying files |
| `-t, --threads` | `4` | Number of concurrent worker threads for batch processing |
| `-j, --json` | `None` | Save structured report to a JSON or JSONL file |
| `-f, --force` | `False` | Overwrite existing subtitle files (default: skips existing subtitles) |
| `--verbose` | `False` | Enable debug logging |

---

### 2. `cinesub bulk` — Batch Download for Manual Testing

Downloads 5–10 subtitle alternatives across providers into the video directory for manual comparison.

```bash
# Download 5 subtitle alternatives for a single episode
cinesub bulk "House.of.the.Dragon.S02E01.1080p.mkv" -l ro -n 5

# Simulate bulk download without writing to disk
cinesub bulk "/path/to/season1" -l en -n 3 --dry-run

# Download alternatives for an entire directory and export to JSON/JSONL
cinesub bulk "/path/to/season1" -l en -n 3 -t 4 --json bulk_report.jsonl
```

Files are named systematically in the video's folder:
```text
House.of.the.Dragon.S02E01.1080p_1_opensubtitles.srt
House.of.the.Dragon.S02E01.1080p_2_subdl.srt
House.of.the.Dragon.S02E01.1080p_3_opensubtitles.srt
```

**Flags & Options:**
| Option | Default | Description |
| :--- | :--- | :--- |
| `PATH` | *required* | Path to video file or directory |
| `-l, --language` | `en` | Subtitle language code |
| `-n, --limit` | `5` | Number of subtitle variations to download (1–20) |
| `-p, --provider` | `all` | Provider filter (`all`, `opensubtitles`, `subdl`, `subsource`, `subsro`, `betaseries`) |
| `-d, --dry-run` | `False` | Simulate search without downloading files |
| `-t, --threads` | `4` | Number of concurrent worker threads |
| `-j, --json` | `None` | Save structured report to a JSON or JSONL file |
| `-f, --force` | `False` | Overwrite existing files |
| `--verbose` | `False` | Enable debug logging |

---

### 3. `cinesub tmdb` — The Movie Database Sync & Bookmarks / Watchlist

Search TMDb for video files or directory names in a folder and optionally synchronize them directly to your TMDb Watchlist (Bookmarks) or Favorites:

```bash
# Query TMDb for media in a folder
cinesub tmdb "/path/to/Movies"

# Bookmark (add to Watchlist) all matched movies in TMDb
cinesub tmdb "/path/to/Movies" -b

# Scan directory / folder names directly (for libraries organized as movie folders)
cinesub tmdb "/path/to/Movies" --by-folder -b

# Add all matched movies to both TMDb Favorites and Watchlist
cinesub tmdb "/path/to/Movies" --favorite --bookmark

# Simulate without altering your TMDb account lists (Dry Run)
cinesub tmdb "/path/to/Movies" -b --dry-run
```

**Flags & Options:**
| Option | Default | Description |
| :--- | :--- | :--- |
| `PATH` | *required* | Path to video file or directory |
| `-b, --bookmark` | `False` | Add matched media to your TMDb Watchlist / Bookmarks (alias for `-w`) |
| `-w, --watchlist` | `False` | Add matched media to your TMDb Watchlist |
| `-f, --favorite` | `False` | Add matched media to your TMDb Favorites list |
| `-F, --by-folder` | `False` | Search TMDb using folder / directory names instead of video filenames |
| `-d, --dry-run` | `False` | Simulate search without submitting modifications to TMDb |
| `--verbose` | `False` | Enable debug logging |

---

### 4. `cinesub extract` — Embedded Subtitle Extraction

Inspects video containers (`.mkv`, `.mp4`, `.m2ts`, etc.) using `ffprobe` and extracts embedded text-based subtitle tracks (`subrip`, `srt`, `ass`, `ssa`, `mov_text`, `webvtt`) directly into UTF-8 normalized companion `.srt` files matching Plex / Emby conventions (`<video_stem>.<lang>.srt`).

**Video File Immutability Guarantee**: CineSub never modifies, re-encodes, or touches original video containers on disk. Subtitle streams are read-only mapped and written into external companion files.

```bash
# Extract all embedded subtitle streams from a movie
cinesub extract "Oppenheimer.2023.2160p.UHD.Remux.mkv"

# Extract only Romanian embedded subtitle tracks
cinesub extract "/path/to/movies" -l ro

# Simulate extraction without writing files to disk (Dry Run)
cinesub extract "/path/to/season1" -l en --dry-run

# Overwrite existing companion .srt files
cinesub extract "/path/to/movies" -l ro -f
```

**Flags & Options:**
| Option | Default | Description |
| :--- | :--- | :--- |
| `PATH` | *required* | Path to video file or directory |
| `-l, --language` | `all` | Target language code (e.g. `ro`, `en`) or `all` |
| `-f, --force` | `False` | Overwrite existing companion `.srt` files on disk |
| `-d, --dry-run` | `False` | Simulate extraction without writing files |
| `--verbose` | `False` | Enable debug logging |

---

### 5. `cinesub config` — Diagnostics

Inspects detected API keys, rate limits, default language, and checks whether `ffmpeg` and `ffprobe` are located on your system PATH.

```bash
cinesub config
```

---

## Technical Details & Rate Limiting

- **Subtitle Sanitization & Encoding**:
  - Automatically normalizes character encoding to clean UTF-8 (without BOM) using `charset-normalizer`, correctly decoding CP1250, CP1251, ISO-8859, and Asian charsets.
  - Validates and re-indexes subtitle sequence numbers and timestamp formatting with `srt`.
- **Rate Limits**:
  - **OpenSubtitles.com**: Official API limit is 5 req/s. CineSub enforces a safe client-side rate limit of **4.0 req/s**.
  - **SubDL.com**: Official API limit is 600 req/min (10 req/s). CineSub enforces **8.0 req/s**.
  - **SubSource.net**: Official API limit is 60 req/min (1 req/s). CineSub enforces **1.0 req/s**.
  - **Subs.ro**: Safe client-side rate limit of **2.0 req/s**.
  - **BetaSeries.com**: Safe client-side rate limit of **2.0 req/s**.
  - **TMDb**: Safe client-side rate limit of **4.0 req/s**.
  - **HTTP 429 Handling**: Token-bucket rate limiters automatically pause all concurrent threads and back off according to `Retry-After` response headers.
- **Embedded Subtitles**: Inspects container streams via `ffprobe` and extracts text tracks with `ffmpeg` without altering source media.
- **Hash Algorithm**: Uses OpenSubtitles' 64-bit checksum over the first and last 64 KB of the file added to the total file size with vectorized 1-shot unpacking.
- **HTTP Client**: Uses `httpx` with persistent connection pooling, HTTP/2 support, and retry handlers.
- **JSON Engine**: `orjson` is used for fast serialization and deserialization.
- **NAS & Plex Library Optimizations**:
  - Prunes non-media and thumbnail directory trees (`@eaDir`, `#recycle`, `.plex`, `Featurettes`, `Trailers`) during traversal for instant scans on large NFS/SMB shares.
  - Automatically skips media with existing valid subtitles (unless `-f, --force` is passed) for efficient incremental cron executions.
  - Supports Plex/Emby language suffix convention (`-S, --lang-suffix`, e.g. `movie.ro.srt`).
  - Employs atomic file replacement (`os.replace`) to prevent partial read race conditions with Plex Media Scanner.
- **Archive Extraction & Bomb Protection**: Archive packages (`.zip`) from SubDL, SubSource, Subs.ro, and BetaSeries are unpacked in-memory with strict uncompressed size limits (max 10MB) to protect against decompression bombs.