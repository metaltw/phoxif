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

## 目錄地圖

- 📊 `reports/` — 專案報告(統一入口 index.html)
- 📌 `docs/` — 設計文件/workflow 說明(要看)
- 🔧 `phoxif/` — 套件原始碼(AI 工作區)
- 🔧 `frontend/` — Web UI 原始碼(AI 工作區)
- 🔧 `assets/` — App 圖示等靜態資源(AI 工作區)

## Reports
報告一律進 `reports/`(入口 INDEX.md),規範見
`~/Documents/git/war_room/standards/repo-reports.md`;收工跑
`~/Documents/git/war_room/tools/hygiene_check.sh .`
