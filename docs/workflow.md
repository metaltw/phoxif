# Photo/Video Organization Workflow

Process each batch of new photos/videos by selecting the steps you need.

---

## Steps Overview

| # | Step | Description | Frequency |
|---|------|-------------|-----------|
| A | [Cleanup](#a-cleanup) | Remove junk files, normalize extensions | Always |
| B | [Rename by date](#b-rename-by-date) | Rename files to `YYYYMMDD_HHMMSS.ext` | Always |
| C | [Fix FileModifyDate](#c-fix-filemodifydate) | Align filesystem date with capture date | Always |
| D | [Dedup](#d-dedup) | MD5-based duplicate removal | Always |
| E | [Video compression](#e-video-compression) | Convert non-HEVC videos to HEVC .mp4 | As needed |
| F | [Location sort](#f-location-sort) | Auto-sort by GPS into location folders | As needed |
| G | [Manual sort](#g-manual-sort) | Web UI to classify Unknown files | As needed |
| H | [GPS write](#h-gps-write) | Batch-write GPS to files missing coordinates | As needed |
| I | [Upload](#i-upload) | rsync to NAS / photo library | Final step |

---

## A. Cleanup

- Delete `.DS_Store` files
- Delete 0-byte files (incomplete downloads or iCloud placeholders)
- Normalize extensions to lowercase (`.JPG` → `.jpg`, `.MOV` → `.mov`), `.jpeg` → `.jpg`

```bash
# Remove .DS_Store
find /path/to/photos -name ".DS_Store" -delete

# Remove 0-byte files
find /path/to/photos -type f -size 0 -delete

# Lowercase extensions (example for .JPG)
for f in /path/to/photos/*.JPG; do mv "$f" "${f%.JPG}.jpg"; done
```

## B. Rename by Date

- Format: `YYYYMMDD_HHMMSS.ext` (lowercase extension)
- Priority: `DateTimeOriginal` > `CreateDate` > `FileModifyDate`
- Same-second conflicts: append counter suffix, e.g., `20190601_091302-1.jpg`

```bash
exiftool -d '%Y%m%d_%H%M%S%%-c.%%le' '-FileName<DateTimeOriginal' -r /path/to/photos
```

If `DateTimeOriginal` is missing, fall back:

```bash
exiftool -d '%Y%m%d_%H%M%S%%-c.%%le' '-FileName<CreateDate' -r /path/to/photos
exiftool -d '%Y%m%d_%H%M%S%%-c.%%le' '-FileName<FileModifyDate' -r /path/to/photos
```

## C. Fix FileModifyDate

Align the filesystem modified date with the capture date so file managers show the correct date.

```bash
exiftool '-FileModifyDate<DateTimeOriginal' -overwrite_original -r /path/to/photos
```

## D. Dedup

- Compare files within the same folder using MD5
- Files with `_1` suffix are likely duplicates — check first
- Prefer keeping the file with more complete EXIF (GPS, dates)

```bash
# Compare two files
md5 -q file1.jpg file2.jpg
```

## E. Video Compression

Convert non-HEVC videos to HEVC .mp4 using hardware-accelerated encoding.

**Rules:**
- ProRes → always convert (10x+ compression ratio)
- H.264 → only convert if >100MB (small files may grow larger)
- HEVC .mov → optionally recompress to .mp4 (saves ~50% at `-q:v 55`)

```bash
# Convert with VideoToolbox hardware acceleration
ffmpeg -y -i input.mov \
  -c:v hevc_videotoolbox -q:v 55 -tag:v hvc1 \
  -c:a aac -b:a 128k \
  -map_metadata 0 \
  output.mp4

# Copy full metadata from original
exiftool -overwrite_original -TagsFromFile input.mov -All:All output.mp4

# Fix FileModifyDate on output
exiftool -overwrite_original '-FileModifyDate<CreateDate' output.mp4
```

After verification, delete the original file.

**Script:** `python -m phoxif.convert --config config.yaml [--dry-run] [--recompress]`

## F. Location Sort

Automatically organize files into folders by capture location using GPS metadata and reverse geocoding (Nominatim API).

- Files with GPS → reverse geocode to city/country → move to named folder
- Files without GPS → move to `Unknown/`
- Geocode results are cached in `.geocache.json`

**Script:** `python -m phoxif.organize --config config.yaml [--dry-run]`

## G. Manual Sort

Web UI for manually classifying files in the `Unknown/` folder.

- Each Set/Del action executes immediately (not batched)
- HEIC previews: converted to JPEG thumbnails via `sips -Z 600`
- ProRes MOV: needs pre-converted H.264 MP4 preview

**Script:** `python -m phoxif.sorter --config config.yaml`

Opens a browser-based interface on `http://localhost:8899` (configurable).

## H. GPS Write

Batch-write GPS coordinates to files that lack GPS metadata. Files that already have GPS are left untouched.

- GPS coordinates are mapped from folder names via `config.yaml`
- Condition filter: `exiftool -if 'not $GPSLatitude'`

```bash
exiftool -if 'not $GPSLatitude' -overwrite_original \
  -GPSLatitude=XX.XXXX -GPSLatitudeRef=N \
  -GPSLongitude=XXX.XXXX -GPSLongitudeRef=E \
  /path/to/folder
```

**Script:** `python -m phoxif.write_gps --config config.yaml [--dry-run]`

## I. Upload

Sync organized files to NAS or photo library.

```bash
rsync -avz --progress /path/to/photos/ user@nas:/volume/photos/
```

Once files are imported into a photo library (e.g., Immich), do not rename or move them — the library relies on stable paths, EXIF dates for timeline, and GPS for map view.

---

## Common Combos

| Scenario | Steps |
|----------|-------|
| Event album (known location, no splitting) | A → B → C → D → E |
| Bulk unsorted photos (full pipeline) | A → B → C → D → E → F → G → H → I |
| Quick fix (filenames and dates only) | A → B → C |
