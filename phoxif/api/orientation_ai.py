"""AI-based visual orientation detection using Gemini Vision API."""

import io
import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Supported image extensions for orientation detection
_PHOTO_EXTS = {".jpg", ".jpeg", ".heic", ".png", ".tiff", ".tif", ".webp", ".bmp"}

# Prompt for Gemini Vision
_ORIENTATION_PROMPT = """\
Analyze this photo's orientation. Is it displayed correctly, or does it need \
to be rotated?

Respond ONLY with a JSON object (no markdown, no explanation):
{"rotation": <degrees>, "confidence": <0.0-1.0>}

Where rotation is one of:
- 0: Image is correctly oriented, no rotation needed
- 90: Image needs to be rotated 90° clockwise to be correct
- 180: Image is upside down, needs 180° rotation
- 270: Image needs to be rotated 270° clockwise (or 90° counter-clockwise)

Look for visual cues: text direction, people standing upright, horizon line, \
gravity (objects hanging down), buildings, trees growing upward, sky position.
"""


def _make_thumbnail(file_path: Path, max_size: int = 512) -> bytes | None:
    """Create a JPEG thumbnail for sending to the API.

    Uses sips on macOS for HEIC support, falls back to PIL.

    Args:
        file_path: Path to the image file.
        max_size: Maximum dimension in pixels.

    Returns:
        JPEG bytes, or None if conversion failed.
    """
    ext = file_path.suffix.lower()

    # HEIC: use sips (macOS) to convert
    if ext in {".heic", ".heif"}:
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name
            subprocess.run(
                [
                    "sips",
                    "-s",
                    "format",
                    "jpeg",
                    "-s",
                    "formatOptions",
                    "50",
                    "-Z",
                    str(max_size),
                    str(file_path),
                    "--out",
                    tmp_path,
                ],
                capture_output=True,
                timeout=10,
                check=True,
            )
            data = Path(tmp_path).read_bytes()
            Path(tmp_path).unlink(missing_ok=True)
            return data
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            FileNotFoundError,
        ):
            return None

    # Other formats: use PIL
    try:
        from PIL import Image

        with Image.open(file_path) as img:
            # Don't apply EXIF rotation — we want to see the raw pixels
            img.thumbnail((max_size, max_size))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=50)
            return buf.getvalue()
    except Exception:
        return None


def detect_orientation(
    image_path: Path,
    api_key: str,
    model: str = "gemini-2.5-flash",
) -> dict[str, Any] | None:
    """Detect visual orientation of a single image using Gemini Vision.

    Args:
        image_path: Path to the image file.
        api_key: Google Gemini API key.
        model: Gemini model name.

    Returns:
        Dict with keys: rotation (0/90/180/270), confidence (0.0-1.0),
        or None if detection failed.
    """
    thumb_bytes = _make_thumbnail(image_path)
    if thumb_bytes is None:
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=[
                _ORIENTATION_PROMPT,
                types.Part.from_bytes(data=thumb_bytes, mime_type="image/jpeg"),
            ],
        )

        text = response.text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        result = json.loads(text)
        rotation = int(result.get("rotation", 0))
        confidence = float(result.get("confidence", 0.0))

        if rotation not in (0, 90, 180, 270):
            # Snap to nearest valid value
            rotation = min((0, 90, 180, 270), key=lambda x: abs(x - rotation))

        return {"rotation": rotation, "confidence": confidence}
    except Exception as e:
        logger.warning("Orientation detection failed for %s: %s", image_path, e)
        return None


def detect_orientation_batch(
    files: list[dict[str, Any]],
    api_key: str,
    model: str = "gemini-2.5-flash",
    confidence_threshold: float = 0.7,
) -> list[dict[str, Any]]:
    """Detect orientation issues for a batch of image files.

    Only checks images where EXIF Orientation is missing or 1 (Normal),
    since those are the ones that might be visually wrong without
    the EXIF tag to compensate.

    Args:
        files: List of normalized file info dicts (from scan_folder).
        api_key: Google Gemini API key.
        model: Gemini model name.
        confidence_threshold: Minimum confidence to report as issue.

    Returns:
        List of dicts, each with:
        - file: The original file info dict.
        - rotation: Suggested rotation in degrees (90/180/270).
        - confidence: Detection confidence (0.0-1.0).
    """
    issues: list[dict[str, Any]] = []

    for f in files:
        ext = Path(f["path"]).suffix.lower()
        if ext not in _PHOTO_EXTS:
            continue

        # Only check files where EXIF orientation is missing or normal,
        # since files with non-normal orientation are already handled
        # by the EXIF-based detection.
        orientation = f.get("orientation")
        if orientation is not None and orientation != 1:
            continue

        result = detect_orientation(Path(f["path"]), api_key, model)
        if result is None:
            continue

        # Only report if rotation is needed and confidence is high enough
        if result["rotation"] != 0 and result["confidence"] >= confidence_threshold:
            issues.append(
                {
                    "file": f,
                    "rotation": result["rotation"],
                    "confidence": result["confidence"],
                }
            )

    return issues
