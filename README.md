# CineSub

[![Latest Release](https://img.shields.io/github/v/release/dvmizew/CineSub?color=brightgreen&label=Latest%20Release&logo=github)](https://github.com/dvmizew/CineSub/releases/latest)

Automated subtitle downloader and media library management CLI for movies and TV series.

CineSub searches across 9 subtitle providers, matches releases using 64-bit video hashes (`OSHash`) or TMDb-enriched metadata, validates timing against video duration, and downloads clean, UTF-8 normalized `.srt` companion subtitles for Plex, Emby, and Jellyfin.

- **Hash-First Precision**: Prioritizes exact 64-bit video checksums (`OSHash`) before falling back to filename metadata matching.
- **9 Providers**: OpenSubtitles, SubDL, SubSource, Subs.ro, BetaSeries, Gestdown, BSPlayer, AnimeTosho, and Assrt.
- **Plex / Emby Ready**: Language tags (`movie.ro.srt`), atomic POSIX file writes, and NAS directory pruning.
- **Embedded Stream Extraction**: Extracts embedded tracks from `.mkv` and `.mp4` without re-encoding.
- **Source Video Immutability**: CineSub **never** alters, moves, or touches original video files.

---

## Quickstart

### 1. Download Binary

Download the self-contained executable for your platform from **[GitHub Releases](https://github.com/dvmizew/CineSub/releases/latest)**:

**Linux (x86_64)** — [Direct Download](https://github.com/dvmizew/CineSub/releases/latest/download/cinesub-linux-x86_64):
```bash
curl -L -o cinesub https://github.com/dvmizew/CineSub/releases/latest/download/cinesub-linux-x86_64
chmod +x cinesub
sudo mv cinesub /usr/local/bin/
```

**Windows (x86_64)** — [Direct Download](https://github.com/dvmizew/CineSub/releases/latest/download/cinesub-windows-x86_64.exe):
```powershell
Invoke-WebRequest -Uri "https://github.com/dvmizew/CineSub/releases/latest/download/cinesub-windows-x86_64.exe" -OutFile "cinesub.exe"
```
*(Place `cinesub.exe` in your `PATH` or media folder)*

> *Optional:* `ffmpeg` and `ffprobe` are only required for `cinesub extract` and video duration inspection.

### 2. Run Immediately

Providers like **BSPlayer**, **Gestdown**, and **AnimeTosho** work **out of the box with zero API keys**:

```bash
# Download best subtitle for a movie
cinesub download "Dune.Part.Two.2024.1080p.WEBRip.mp4" -l ro

# Process an entire TV season in parallel with Plex language suffix (e.g. episode.ro.srt)
cinesub download "/media/TV/House.of.the.Dragon.S02" -l ro -S -t 8
```

---

## Configuration & `.env.example`

To unlock commercial and archive providers (**OpenSubtitles**, **SubDL**, **SubSource**, **Subs.ro**, **BetaSeries**, **Assrt**, and **TMDb**), create a `.env` file from `.env.example`:

```bash
# Copy the template to .env in your current working directory or home directory
cp .env.example .env
```

Open `.env` in any text editor and fill in your free API credentials:

```ini
# OpenSubtitles.com REST API (Register at https://www.opensubtitles.com/consumers)
OPENSUBTITLES_API_KEY=your_key_here
OPENSUBTITLES_USER_AGENT=CineSub v0.2.0

# SubDL API (Register at https://subdl.com)
SUBDL_API_KEY=your_key_here

# SubSource API (Register at https://subsource.net)
SUBSOURCE_API_KEY=your_key_here

# Subs.ro API (Register at https://subs.ro)
SUBSRO_API_KEY=your_key_here

# BetaSeries API (Register at https://www.betaseries.com/api)
BETASERIES_API_KEY=your_key_here

# Assrt.net / Shooter.cn API (Register at https://assrt.net)
ASSRT_API_TOKEN=your_token_here

# TMDb API (Generate token at https://www.themoviedb.org/settings/api)
TMDB_READ_ACCESS_TOKEN=your_token_here
TMDB_ACCOUNT_ID=your_account_id_here

# General Settings
CINESUB_DEFAULT_LANGUAGE=en
CINESUB_TIMEOUT=15
```

### Environment Variables Reference

| Variable | Description & Registration | Required? |
| :--- | :--- | :---: |
| `OPENSUBTITLES_API_KEY` | [OpenSubtitles.com](https://www.opensubtitles.com/consumers) API key | Optional |
| `OPENSUBTITLES_USERNAME` | OpenSubtitles username (increases daily download quotas) | Optional |
| `OPENSUBTITLES_PASSWORD` | OpenSubtitles password | Optional |
| `SUBDL_API_KEY` | [SubDL](https://subdl.com) API key for multi-language release search | Optional |
| `SUBSOURCE_API_KEY` | [SubSource](https://subsource.net) API key | Optional |
| `SUBSRO_API_KEY` | [Subs.ro](https://subs.ro) API key (Romanian retail subtitle archive) | Optional |
| `BETASERIES_API_KEY` | [BetaSeries](https://www.betaseries.com/api) API key (TV series & movies) | Optional |
| `ASSRT_API_TOKEN` | [Assrt.net](https://assrt.net) API token (Shooter.cn subtitle archive) | Optional |
| `TMDB_READ_ACCESS_TOKEN` | [TMDb](https://www.themoviedb.org/settings/api) Read Access Token (metadata enrichment & watchlist) | Optional |
| `TMDB_ACCOUNT_ID` | TMDb Account ID (required for Watchlist / Favorites sync) | Optional |
| `CINESUB_DEFAULT_LANGUAGE` | Default ISO 639-1 language code (e.g. `en`, `ro`, `es`, `fr`, `de`). Default: `en` | Optional |
| `CINESUB_TIMEOUT` | HTTP network request timeout in seconds. Default: `15` | Optional |

> **Note:** CineSub searches for `.env` in the current working directory first, then in your user home directory (`~/.env`). Verify your setup at any time with `cinesub config`.

---

## How It Works

```
Video File ──► [ 1. 64-bit OSHash ] ──► Exact Hash Match? ────► [ Download Best Subtitle ]
                     │                                                   │
                    No                                                   ▼
                     ▼                                        [ 3. Validate & Save ]
              [ 2. Metadata & TMDb ]                          • Timing check vs duration
              (Title, Year, Episode,                          • UTF-8 normalization
               Release Group, IMDb ID)                        • Downgrade protection
                     │                                        • Atomic write (.srt)
                     ▼                                                   │
         Query 9 Providers in Parallel ──────────────────────────────────┘
```

1. **Exact 64-Bit Hash Matching**: Reads the first and last 64 KB of the video file (`OSHash`). If a provider has a matching hash, subtitle synchronization is guaranteed without relying on messy filenames.
2. **Metadata Fallback & TMDb Enrichment**: If no hash matches, CineSub extracts media details (`guessit`), enriches IMDb/TMDb IDs, and scores candidate releases using release group clusters and token boundaries (`rapidfuzz`).
3. **Timing Invariants & Atomic Writes**: Validates the subtitle's maximum timestamp against video duration to eliminate cross-movie collisions. Subtitles are written atomically (`.<name>.tmp` → `os.replace`) to prevent media server scan glitches.
4. **Token-Bucket Rate Limiting**: Synchronized per-provider rate limiters prevent HTTP 429 bans during large multi-threaded batch jobs.

---

## Commands & Examples

### 1. `cinesub download` (alias: `sync`)

Finds and saves matching subtitles for your video files (`movie.mp4` → `movie.srt`). By default, downloads the single highest-scoring subtitle. With `-a` / `--alternatives`, downloads multiple ranked alternatives for comparison (`movie_1_provider.srt`, `movie_2_provider.srt`).

```bash
# Download best subtitle using default language (en, or CINESUB_DEFAULT_LANGUAGE from .env)
cinesub download "Movie.2024.1080p.mp4"

# Download in a specific language (e.g. Romanian)
cinesub download "Movie.2024.1080p.mp4" -l ro

# Save with Plex/Emby language suffix (Movie.2024.1080p.ro.srt)
cinesub download "Movie.2024.1080p.mp4" -l ro -S

# Download top 3 ranked subtitle alternatives for manual comparison
cinesub download "Movie.2024.1080p.mp4" -l ro -a 3

# Batch process a TV show folder with 8 parallel worker threads
cinesub download "/media/TV/Show.S01" -l ro -S -t 8

# Query only a specific provider (e.g. bsplayer, opensubtitles, subdl, subsro)
cinesub download "Movie.2024.mp4" -p bsplayer

# Dry-run: simulate matching without downloading files or touching disk
cinesub download "/media/Movies" -l ro --dry-run

# Force overwrite existing subtitles (default skips media with existing .srt)
cinesub download "/media/Movies" -l ro -f

# Export execution summary to JSON or streaming JSONL
cinesub download "/media/Movies" -l ro -j report.jsonl
```

**Key Flags:**
- `PATH`: Path to a video file or directory.
- `-l, --language`: ISO 639-1 language code (e.g. `ro`, `en`, `es`, `fr`). Default: `en`.
- `-a, -n, --alternatives`: Number of subtitle alternatives to download per video. Default: `1`.
- `-S, --lang-suffix`: Saves subtitle with Plex/Emby language tag (`<stem>.<lang>.srt`).
- `-p, --provider`: Provider filter (`all`, `opensubtitles`, `subdl`, `subsource`, `subsro`, `betaseries`, `gestdown`, `bsplayer`, `animetosho`, `assrt`). Default: `all`.
- `-t, --threads`: Worker threads for concurrent batch processing. Default: `4`.
- `-d, --dry-run`: Simulate search and matching without downloading.
- `-f, --force`: Overwrite existing subtitle files on disk.
- `-j, --json`: Save structured execution report to a `.json` or `.jsonl` file.
- `--verbose`: Enable debug logging.

---

### 2. `cinesub extract`

Inspects video containers (`.mkv`, `.mp4`, `.m2ts`) using `ffprobe` and extracts embedded text subtitle streams (`subrip`, `srt`, `ass`, `mov_text`, `webvtt`) into companion `.srt` files.

> **Video Immutability:** CineSub reads stream tracks without altering, re-encoding, or touching original video files.

```bash
# Extract all embedded subtitle streams from a movie
cinesub extract "Movie.2024.mkv"

# Extract only Romanian embedded subtitle tracks
cinesub extract "Movie.2024.mkv" -l ro

# Batch extract embedded subtitles across an entire movies directory
cinesub extract "/media/Movies" -l ro

# Simulate extraction without writing files to disk (dry-run)
cinesub extract "/media/Movies" -l ro --dry-run

# Overwrite existing companion .srt files
cinesub extract "/media/Movies" -l ro -f
```

**Key Flags:**
- `PATH`: Path to a video file or directory containing video files.
- `-l, --language`: Target language code (e.g. `ro`, `en`) or `all`. Default: `all`.
- `-f, --force`: Overwrite existing companion `.srt` files on disk.
- `-d, --dry-run`: Inspect streams without writing files.
- `--verbose`: Enable debug logging.

---

### 3. `cinesub tmdb`

Queries TMDb for video files or directory names, resolves metadata (IMDb ID, canonical title, release year), and optionally synchronizes matched items directly to your TMDb account Watchlist (Bookmarks) or Favorites.

```bash
# Identify media items in a folder against TMDb
cinesub tmdb "/media/Movies"

# Bookmark (add to Watchlist) all matched media items in your TMDb account
cinesub tmdb "/media/Movies" -b

# Scan folder names instead of filenames (for movie-folder library structures)
cinesub tmdb "/media/Movies" --by-folder -b

# Add all matched items to both TMDb Favorites and Watchlist
cinesub tmdb "/media/Movies" --favorite --bookmark

# Simulate matching without submitting modifications to your TMDb account
cinesub tmdb "/media/Movies" -b --dry-run
```

**Key Flags:**
- `PATH`: Path to a video file or directory containing video files.
- `-b, --bookmark` / `-w, --watchlist`: Add matched media to your TMDb Watchlist.
- `-f, --favorite`: Add matched media to your TMDb Favorites list.
- `-F, --by-folder`: Search TMDb using folder names instead of filenames.
- `-d, --dry-run`: Simulate search without modifying TMDb account lists.
- `--verbose`: Enable debug logging.

---

### 4. `cinesub config`

Runs a diagnostic health check on your environment. Displays active API credentials, configured rate limits, default language, and checks whether `ffmpeg` and `ffprobe` are installed on your system PATH.

```bash
cinesub config
```

---

### Interactive Runtime Controls

During long-running batch operations (`download`, `extract`, `tmdb`), CineSub supports interactive keyboard controls:

- **`[Space]` or `p`**: Pause/Resume execution. In-flight requests complete cleanly, worker threads pause, and ETA timers halt. Press again to resume.
- **`Ctrl+C` (SIGINT)**: Gracefully cancel pending operations, shut down thread pools, and export partial reports without leaving orphan temporary files (`.<name>.tmp`).

---

## Providers & Rate Limits

| Provider | Type | API Key Required? | Rate Limit |
| :--- | :--- | :---: | :--- |
| **BSPlayer** | 64-bit Video Hash (SOAP) | **No** (Public) | 2.0 req/s |
| **Gestdown** | TV Shows / Addic7ed REST | **No** (Public) | 2.0 req/s |
| **AnimeTosho** | Anime Feed & Attachments | **No** (Public) | 2.0 req/s |
| **OpenSubtitles.com** | Hash & Text Search REST | Yes (Free) | 4.0 req/s |
| **SubDL** | Text Search REST | Yes (Free) | 8.0 req/s |
| **SubSource** | Text Search REST | Yes (Free) | 1.0 req/s |
| **Subs.ro** | Romanian Archive REST | Yes (Free) | 2.0 req/s |
| **BetaSeries** | TV & Movies REST | Yes (Free) | 2.0 req/s |
| **Assrt.net** | Shooter.cn Archive REST | Yes (Free) | 0.33 req/s |
| **TMDb** | Metadata Enrichment & Watchlist | Yes (Free) | 4.0 req/s |
