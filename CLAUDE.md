# phoxif — EXIF Metadata Toolkit

## Overview
Public repo. Photo/video EXIF metadata batch processing tools.
Born from personal photo organization needs, generalized for public use.

## CRITICAL: This is a PUBLIC repo
- NO personal paths, GPS coordinates, location names, or usernames
- All personal config goes in `config.yaml` (gitignored)
- Only `config.example.yaml` is committed
- Code must use configurable paths, never hardcode

## Tech Stack
- Python 3.12 (uv)
- exiftool for EXIF read/write
- ffmpeg with VideoToolbox for HEVC encoding
- Nominatim API for reverse geocoding

## Code Style
- Type hints, Google style docstring
- ruff for linting/formatting
