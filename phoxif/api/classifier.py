"""Classify files into non-photo categories (screenshots, recordings, messaging, documents)."""

import re
from pathlib import Path
from typing import Any

# --- Category constants ---

CATEGORY_SCREENSHOT = "screenshot"
CATEGORY_SCREEN_RECORDING = "screen_recording"
CATEGORY_MESSAGING = "messaging"
CATEGORY_DOCUMENT = "document"

ALL_CATEGORIES = [
    CATEGORY_SCREENSHOT,
    CATEGORY_SCREEN_RECORDING,
    CATEGORY_MESSAGING,
    CATEGORY_DOCUMENT,
]

CATEGORY_LABELS = {
    CATEGORY_SCREENSHOT: "Screenshots",
    CATEGORY_SCREEN_RECORDING: "Screen Recordings",
    CATEGORY_MESSAGING: "Messaging Images",
    CATEGORY_DOCUMENT: "Document Photos",
}

CATEGORY_DESCRIPTIONS = {
    CATEGORY_SCREENSHOT: "Desktop and mobile screenshots",
    CATEGORY_SCREEN_RECORDING: "Screen recordings from phone or computer",
    CATEGORY_MESSAGING: "Images received via messaging apps (LINE, WhatsApp, Telegram)",
    CATEGORY_DOCUMENT: "Photos of documents, receipts, IDs, business cards",
}

# --- Filename patterns ---

_SCREENSHOT_PATTERNS = [
    # macOS: "Screenshot 2024-01-15 at 10.30.45" or "Screenshot 2024-01-15 at 10.30.45 AM"
    re.compile(
        r"^Screenshot \d{4}-\d{2}-\d{2} at \d{1,2}\.\d{2}\.\d{2}", re.IGNORECASE
    ),
    # macOS older: "Screen Shot 2024-01-15 at ..."
    re.compile(r"^Screen Shot \d{4}-\d{2}-\d{2}", re.IGNORECASE),
    # Windows Snipping Tool: "Snip_20240115_103045"
    re.compile(r"^Snip_\d{8}_\d{6}", re.IGNORECASE),
    # Windows Print Screen: "Screenshot (123)"
    re.compile(r"^Screenshot\s*\(\d+\)", re.IGNORECASE),
    # Android: "Screenshot_20240115-103045" or "Screenshot_20240115_103045"
    re.compile(r"^Screenshot[-_]\d{8}[-_]\d{6}", re.IGNORECASE),
    # Samsung: "screenshot_20240115-103045_AppName"
    re.compile(r"^screenshot[-_]\d{8}", re.IGNORECASE),
    # Chinese/Japanese/Korean
    re.compile(r"^(截圖|截屏|スクリーンショット|スクショ|화면캡처)", re.IGNORECASE),
    # Generic "screenshot" as a standalone word in filename
    re.compile(r"\bscreenshot\b", re.IGNORECASE),
    # macOS CleanShot: "CleanShot 2024-01-15 at ..."
    re.compile(r"^CleanShot\s", re.IGNORECASE),
    # Flameshot, Shutter, etc.
    re.compile(r"^(flameshot|shutter|greenshot|snagit)", re.IGNORECASE),
]

_SCREEN_RECORDING_PATTERNS = [
    # macOS: "Screen Recording 2024-01-15 at 10.30.45"
    re.compile(r"^Screen Recording \d{4}-\d{2}-\d{2}", re.IGNORECASE),
    # iOS ReplayKit: "RPReplay_Final1705312245"
    re.compile(r"^RPReplay", re.IGNORECASE),
    # Generic
    re.compile(r"screen[-_ ]?recording", re.IGNORECASE),
    # Android screen recorder
    re.compile(r"^screenrecord[-_]", re.IGNORECASE),
    # Samsung screen recorder
    re.compile(r"^Screen_Recording_\d{8}", re.IGNORECASE),
]

_MESSAGING_PATTERNS = [
    # WhatsApp: "IMG-20240115-WA0001"
    re.compile(r"^IMG-\d{8}-WA\d+", re.IGNORECASE),
    # WhatsApp video: "VID-20240115-WA0001"
    re.compile(r"^VID-\d{8}-WA\d+", re.IGNORECASE),
    # LINE: "LINE_ALBUM_xxx" or just "LINE_"
    re.compile(r"^LINE[-_]", re.IGNORECASE),
    # Telegram: "photo_2024-01-15_10-30-45"
    re.compile(r"^photo_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}", re.IGNORECASE),
    # Telegram video: "video_2024-01-15_10-30-45"
    re.compile(r"^video_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}", re.IGNORECASE),
    # WeChat: "mmexport1705312245678"
    re.compile(r"^mmexport\d{13}", re.IGNORECASE),
    # Signal: "signal-2024-01-15-103045"
    re.compile(r"^signal-\d{4}-\d{2}-\d{2}", re.IGNORECASE),
    # Facebook Messenger: "received_1234567890.jpeg"
    re.compile(r"^received_\d{5,}", re.IGNORECASE),
    # KakaoTalk: "KakaoTalk_Photo_"
    re.compile(r"^KakaoTalk[-_]", re.IGNORECASE),
]

_DOCUMENT_PATTERNS = [
    # Scanned documents
    re.compile(r"^scan[-_ ]?\d", re.IGNORECASE),
    re.compile(r"^scanned[-_ ]", re.IGNORECASE),
    # CamScanner: "CamScanner 01-15-2024 10.30"
    re.compile(r"^CamScanner", re.IGNORECASE),
    # Adobe Scan: "Adobe Scan ..."
    re.compile(r"^Adobe Scan", re.IGNORECASE),
    # Microsoft Lens / Office Lens
    re.compile(r"^(Microsoft|Office)[-_ ]?Lens", re.IGNORECASE),
    # Generic document keywords in filename
    re.compile(
        r"(receipt|invoice|contract|certificate|license|passport|id[-_ ]?card)",
        re.IGNORECASE,
    ),
]

# Known screen resolutions (width x height) for screenshot detection
_SCREEN_RESOLUTIONS = {
    # iPhone
    (1170, 2532),
    (2532, 1170),  # iPhone 12/13/14
    (1179, 2556),
    (2556, 1179),  # iPhone 14/15 Pro
    (1290, 2796),
    (2796, 1290),  # iPhone 14/15 Pro Max
    (1125, 2436),
    (2436, 1125),  # iPhone X/XS/11 Pro
    (1242, 2688),
    (2688, 1242),  # iPhone XS Max/11 Pro Max
    (828, 1792),
    (1792, 828),  # iPhone XR/11
    (750, 1334),
    (1334, 750),  # iPhone 6/7/8
    (1080, 1920),
    (1920, 1080),  # iPhone 6+/7+/8+
    (640, 1136),
    (1136, 640),  # iPhone 5/5S/SE1
    # iPad
    (2048, 2732),
    (2732, 2048),  # iPad Pro 12.9"
    (1668, 2388),
    (2388, 1668),  # iPad Pro 11"
    (2048, 1536),
    (1536, 2048),  # iPad Air/Mini Retina
    # Mac common
    (2880, 1800),
    (1800, 2880),  # MacBook Pro 15"
    (3024, 1964),
    (1964, 3024),  # MacBook Pro 14" (M1/M2/M3/M4)
    (3456, 2234),
    (2234, 3456),  # MacBook Pro 16"
    (2560, 1600),
    (1600, 2560),  # MacBook Air 13"
    (2560, 1440),
    (1440, 2560),  # QHD
    (3840, 2160),
    (2160, 3840),  # 4K
    (5120, 2880),
    (2880, 5120),  # 5K iMac
    # Common Android
    (1080, 2400),
    (2400, 1080),
    (1080, 2340),
    (2340, 1080),
    (1440, 3200),
    (3200, 1440),
    (1440, 3120),
    (3120, 1440),
    (1440, 2960),
    (2960, 1440),  # Samsung S8/S9
    (1440, 3088),
    (3088, 1440),  # Samsung S10+
}

_PHOTO_EXTS = {
    ".jpg",
    ".jpeg",
    ".heic",
    ".heif",
    ".png",
    ".tiff",
    ".tif",
    ".webp",
    ".bmp",
}
_VIDEO_EXTS = {".mov", ".mp4", ".avi", ".mkv", ".m4v"}


def _match_patterns(filename: str, patterns: list[re.Pattern[str]]) -> bool:
    """Check if filename matches any of the given patterns."""
    for pattern in patterns:
        if pattern.search(filename):
            return True
    return False


def _is_screenshot_by_metadata(f: dict[str, Any]) -> bool:
    """Check metadata heuristics for screenshot detection.

    Screenshots typically have:
    - PNG format (iOS/macOS/Android)
    - Exact screen resolution dimensions
    - No GPS
    - No camera model (exiftool would have Model/Make fields)
    """
    ext = Path(f.get("path", "")).suffix.lower()
    w = f.get("width")
    h = f.get("height")

    # PNG with exact screen resolution and no GPS = likely screenshot
    if ext == ".png" and w and h:
        if (w, h) in _SCREEN_RESOLUTIONS and f.get("gps_lat") is None:
            return True

    return False


def classify_non_photos(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Classify files into non-photo categories.

    Uses filename pattern matching and metadata heuristics. Each file
    can only be assigned to one category (first match wins, priority order:
    screenshot > screen_recording > messaging > document).

    Args:
        files: List of normalized file info dicts (from scan_folder).

    Returns:
        List of dicts, each with:
        - file: The original file info dict.
        - category: One of the CATEGORY_* constants.
        - reason: Human-readable reason for classification.
    """
    results: list[dict[str, Any]] = []

    for f in files:
        filename = f.get("filename", "")
        ext = Path(f.get("path", "")).suffix.lower()

        if ext not in _PHOTO_EXTS and ext not in _VIDEO_EXTS:
            continue

        stem = Path(filename).stem

        # Priority 1: Screen recordings (check before screenshots for video files)
        if ext in _VIDEO_EXTS and _match_patterns(stem, _SCREEN_RECORDING_PATTERNS):
            results.append(
                {
                    "file": f,
                    "category": CATEGORY_SCREEN_RECORDING,
                    "reason": "Filename matches screen recording pattern",
                }
            )
            continue

        # Priority 2: Screenshots
        if _match_patterns(stem, _SCREENSHOT_PATTERNS):
            results.append(
                {
                    "file": f,
                    "category": CATEGORY_SCREENSHOT,
                    "reason": "Filename matches screenshot pattern",
                }
            )
            continue

        # Metadata-based screenshot detection (PNG + screen resolution + no GPS)
        if _is_screenshot_by_metadata(f):
            results.append(
                {
                    "file": f,
                    "category": CATEGORY_SCREENSHOT,
                    "reason": "PNG with screen resolution, no GPS data",
                }
            )
            continue

        # Priority 3: Messaging app images
        if _match_patterns(stem, _MESSAGING_PATTERNS):
            results.append(
                {
                    "file": f,
                    "category": CATEGORY_MESSAGING,
                    "reason": "Filename matches messaging app pattern",
                }
            )
            continue

        # Priority 4: Document photos
        if _match_patterns(stem, _DOCUMENT_PATTERNS):
            results.append(
                {
                    "file": f,
                    "category": CATEGORY_DOCUMENT,
                    "reason": "Filename matches document/scan pattern",
                }
            )
            continue

    return results
