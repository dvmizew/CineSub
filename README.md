# CineSub

Automated subtitle downloader and audio-waveform synchronizer for movies and TV series.

CineSub searches subtitle providers (**OpenSubtitles.com** and **SubDL**), matches files using exact 64-bit video hashes (`OSHash`) or release metadata, downloads `.srt` files, and aligns subtitle timings directly to dialogue audio via `ffsubsync`.

Supports single files or batch processing of entire folders with multithreading and automatic rate limiting.

---

## How It Works

```
Video File ──► [ 1. Compute 64-bit OSHash ] ──► Exact match on OpenSubtitles?
                     │                                   │
                    No                                  Yes
                     ▼                                   ▼
             [ 2. Parse Filename ]              Download Subtitle (.srt)
             (Title, Year, Episode,                      │
              Resolution, Release Group)                 │
                     │                                   │
                     ▼                                   ▼
          Query SubDL & OpenSubtitles         [ 4. Audio Synchronization ]
                     │                        Extract speech audio with ffmpeg
                     ▼                        Shift timestamps with ffsubsync
          Score candidates with RapidFuzz                │
                     │                                   ▼
                     └─────────────────────────► Final Synchronized Subtitle
```

1. **Exact Hash Match**: Computes the 64-bit checksum over the video header and footer. If found on OpenSubtitles, timing is typically already tailored for that specific encode.
2. **Metadata Fallback**: If hash matching yields no results, `guessit` extracts media metadata (title, season/episode, release group, source) to query SubDL and OpenSubtitles. Results are ranked by release similarity using `rapidfuzz`.
3. **Audio-Based Waveform Sync**: Runs `ffsubsync` against the video's audio track to detect Voice Activity (VAD) and recalculate timestamp offsets and framerate drift, overwriting the file with a synchronized `.srt`.
4. **Thread-Safe Rate Limiting**: Ensures concurrent batch jobs never exceed provider thresholds (4.0 req/s for OpenSubtitles, 8.0 req/s for SubDL) with automatic exponential backoff on HTTP 429.

---

## Installation

### 1. System Dependency

`ffmpeg` is required for extracting the audio stream during synchronization.

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

CineSub integrates with two major subtitle provider APIs. You can obtain free API keys from:

| Provider | API Endpoint | Documentation & API Key Registration | Rate Limit |
| :--- | :--- | :--- | :--- |
| **OpenSubtitles.com** | `https://api.opensubtitles.com/api/v1` | [OpenSubtitles API Portal](https://www.opensubtitles.com/consumers) | 4.0 req/s (Client Cap) / 5 req/s max |
| **SubDL** | `https://api.subdl.com/api/v1` | [SubDL API Portal](https://subdl.com/api) | 8.0 req/s (600 req/min Cap) |

Create a `.env` file in your working directory or home directory:

```bash
cp .env.example .env
```

```ini
# OpenSubtitles.com API Key (https://www.opensubtitles.com/consumers)
OPENSUBTITLES_API_KEY=your_opensubtitles_api_key

# SubDL API Key (https://subdl.com)
SUBDL_API_KEY=your_subdl_api_key

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

### 1. `cinesub sync` — Search & Download (Optional Audio Sync)

Downloads the single best candidate and saves it matching the video filename (`movie.mp4` $\to$ `movie.srt`).

By default, timestamps are preserved exactly as provided by the author. Pass `--sync` / `-s` to align subtitle timings against the dialogue audio stream using `ffsubsync`.

Supports single video files or entire directories for batch processing.

```bash
# Descarcă cea mai bună subtitrare în limba română (fără modificarea timestamp-urilor)
cinesub sync "Dune.Part.Two.2024.1080p.WEBRip.x264-FGT.mp4" -l ro

# Descarcă și aliniază sincronizarea audio cu ffsubsync
cinesub sync "Dune.Part.Two.2024.1080p.WEBRip.x264-FGT.mp4" -l ro --sync

# Descarcă pentru un întreg sezon de serial cu 8 fire de execuție paralele
cinesub sync "/path/to/House.of.the.Dragon.S02" -l ro -t 8

# Sincronizează audio și păstrează copia originală (.orig.srt)
cinesub sync "The.Last.of.Us.S01E01.720p.HDTV.mkv" -l en --sync --backup

# Salvează raportul detaliat în format JSON
cinesub sync "/path/to/movies" -l ro -t 4 --json sync_report.json
```

**Flags & Options:**
| Option | Default | Description |
| :--- | :--- | :--- |
| `PATH` | *required* | Path to a video file or a directory containing video files |
| `-l, --language` | `en` | Subtitle language (ISO 639-1 code, e.g. `ro`, `en`, `es`) |
| `-p, --provider` | `all` | Search provider: `all`, `opensubtitles`, or `subdl` |
| `-s, --sync` | `False` | Align subtitle timestamps against video audio |
| `-e, --engine` | `ffsubsync` | Synchronization engine: `ffsubsync` (Python/FFmpeg) or `alass` (Rust) |
| `-b, --backup` | `False` | Save unsynchronized original as `<name>.orig.srt` |
| `-t, --threads` | `4` | Number of concurrent worker threads for batch processing |
| `-j, --json` | `None` | Save structured report to a JSON file |
| `-f, --force` | `False` | Overwrite existing subtitle files |
| `--verbose` | `False` | Enable debug logging |

---

### 2. `cinesub bulk` — Batch Download for Manual Testing

Downloads 5–10 subtitle alternatives across providers into the video directory for manual comparison.

```bash
# Descarcă 5 alternative pentru un episod
cinesub bulk "House.of.the.Dragon.S02E01.1080p.mkv" -l ro -n 5

# Descarcă alternative pentru un întreg folder și exportă în JSON
cinesub bulk "/path/to/season1" -l en -n 3 -t 4 --json bulk_report.json
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
| `-p, --provider` | `all` | Provider filter (`all`, `opensubtitles`, `subdl`) |
| `-t, --threads` | `4` | Number of concurrent worker threads |
| `-j, --json` | `None` | Save structured report to a JSON file |
| `-f, --force` | `False` | Overwrite existing files |
| `--verbose` | `False` | Enable debug logging |

---

### 3. `cinesub config` — Diagnostics

Inspects detected API keys, rate limits, default language, and checks whether `ffmpeg` is located on your system PATH.

```bash
cinesub config
```

---

## Technical Details & Rate Limiting

- **Subtitle Sanitization & Encoding**:
  - Automatically normalizes character encoding to clean UTF-8 (without BOM) using `charset-normalizer`, correctly decoding CP1250, CP1251, ISO-8859, and Asian charsets.
  - Validates and re-indexes subtitle sequence numbers and timestamp formatting with `srt`.
- **Audio Alignment Engines**:
  - Supports **`ffsubsync`** (VAD cross-correlation) and **`alass`** (Dynamic Programming).
- **Rate Limits**:
  - **OpenSubtitles.com**: Official API limit is 5 req/s. CineSub enforces a safe client-side rate limit of **4.0 req/s**.
  - **SubDL.com**: Official API limit is 600 req/min (10 req/s). CineSub enforces **8.0 req/s**.
  - **HTTP 429 Handling**: Token-bucket rate limiters automatically pause all concurrent threads and back off according to `Retry-After` response headers.
- **Hash Algorithm**: Uses OpenSubtitles' 64-bit checksum over the first and last 64 KB of the file added to the total file size.
- **HTTP Client**: Uses `httpx` with persistent connection pooling, HTTP/2 support, and retry handlers.
- **JSON Engine**: `orjson` is used for fast serialization and deserialization.
- **Archive Extraction**: SubDL `.zip` packages are unpacked in-memory using `io.BytesIO` and `zipfile.ZipFile`.

---

## License

MIT License. See [LICENSE](LICENSE) for details.