# phoxif

**EXIF metadata toolkit for batch photo/video organization.**

A command-line toolkit for managing photo and video collections — rename by date, fix timestamps, deduplicate, batch-write GPS, organize by location, compress videos to HEVC, and manually sort with a web UI.

## Features

- **Rename by date** — `YYYYMMDD_HHMMSS.ext` from EXIF DateTimeOriginal
- **Fix FileModifyDate** — align filesystem dates with EXIF capture dates
- **Deduplicate** — MD5-based duplicate detection and removal
- **GPS batch write** — fill missing GPS metadata from a configurable location map
- **Location-based organization** — auto-sort files into folders by reverse-geocoded location
- **HEVC video compression** — hardware-accelerated conversion via VideoToolbox (macOS)
- **Web UI for manual sorting** — browser-based interface to classify unrecognized files

## Requirements

- **Python 3.12+**
- **[exiftool](https://exiftool.org/)** — EXIF metadata read/write
- **[ffmpeg](https://ffmpeg.org/)** with HEVC support (VideoToolbox on macOS)

Install on macOS:

```bash
brew install exiftool ffmpeg
```

## Quick Start

1. Clone the repo and set up:

```bash
git clone https://github.com/user/phoxif.git
cd phoxif
cp config.example.yaml config.yaml
```

2. Edit `config.yaml` with your actual paths and GPS locations.

3. Run individual tools:

```bash
# Organize photos by GPS location
python -m phoxif.organize --config config.yaml

# Write GPS to files missing coordinates
python -m phoxif.write_gps --config config.yaml

# Convert videos to HEVC
python -m phoxif.convert --config config.yaml --dry-run

# Launch web UI for manual sorting
python -m phoxif.sorter --config config.yaml
```

## Workflow

The full photo/video organization workflow has 9 steps (A through I).
See [docs/workflow.md](docs/workflow.md) for the complete reference.

### Common Combos

| Scenario | Steps |
|----------|-------|
| Event album (known location, no splitting) | A → B → C → D → E |
| Bulk unsorted photos (full pipeline) | A → B → C → D → E → F → G → H → I |
| Quick fix (filenames and dates only) | A → B → C |

## Configuration

All personal paths, GPS coordinates, and settings live in `config.yaml` (gitignored).
See [`config.example.yaml`](config.example.yaml) for the full structure.

## License

[Apache License 2.0](LICENSE)
