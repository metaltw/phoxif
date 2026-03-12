"""Orientation detection using local ONNX model with optional Gemini fallback."""

import io
import json
import logging
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Supported extensions for orientation detection
_PHOTO_EXTS = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".tiff", ".tif", ".webp", ".bmp"}
_VIDEO_EXTS = {".mov", ".mp4", ".avi", ".mkv", ".m4v"}
_ALL_SUPPORTED_EXTS = _PHOTO_EXTS | _VIDEO_EXTS

# ONNX model config
_ONNX_REPO = "DuarteBarbosa/deep-image-orientation-detection"
_ONNX_FILE = "orientation_model_v2_0.9882.onnx"
_IMAGE_SIZE = 384
_CLASS_TO_ROTATION = {0: 0, 1: 90, 2: 180, 3: 270}

# Lazy-loaded ONNX session singleton
_onnx_session = None
_onnx_lock = threading.Lock()


def _get_onnx_session():  # type: ignore[no-untyped-def]
    """Get or create the ONNX inference session (lazy singleton).

    Downloads model from HuggingFace Hub on first use (~77MB).
    Uses double-checked locking to avoid race conditions.

    Returns:
        ONNX InferenceSession, or None if unavailable.
    """
    global _onnx_session  # noqa: PLW0603
    if _onnx_session is not None:
        return _onnx_session

    with _onnx_lock:
        # Double-check after acquiring lock
        if _onnx_session is not None:
            return _onnx_session

        try:
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download

            model_path = hf_hub_download(repo_id=_ONNX_REPO, filename=_ONNX_FILE)
            _onnx_session = ort.InferenceSession(
                model_path,
                providers=["CoreMLExecutionProvider", "CPUExecutionProvider"],
            )
            logger.info("ONNX orientation model loaded from %s", model_path)
            return _onnx_session
        except Exception as e:
            logger.warning("Failed to load ONNX orientation model: %s", e)
            return None


def _preprocess_for_onnx(file_path: Path) -> np.ndarray | None:
    """Load and preprocess an image for the ONNX orientation model.

    Args:
        file_path: Path to image file.

    Returns:
        Float32 tensor [1, 3, 384, 384], or None on failure.
    """
    from PIL import Image, ImageOps

    try:
        ext = file_path.suffix.lower()

        # Video: extract frame
        if ext in _VIDEO_EXTS:
            frame_bytes = _extract_video_frame(file_path)
            if frame_bytes is None:
                return None
            img = Image.open(io.BytesIO(frame_bytes))
        # HEIC: convert via sips
        elif ext in {".heic", ".heif"}:
            jpg_bytes = _convert_heic_to_jpeg(file_path)
            if jpg_bytes is None:
                return None
            img = Image.open(io.BytesIO(jpg_bytes))
        else:
            img = Image.open(file_path)

        # Apply EXIF orientation so the model sees what the viewer shows.
        # This way we detect images that LOOK wrong to the user.
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        # Resize + center crop to 384x384
        img = img.resize((416, 416), Image.BILINEAR)
        img = img.crop((16, 16, 400, 400))

        arr = np.array(img, dtype=np.float32) / 255.0
        arr = (arr - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
        return arr.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)
    except Exception as e:
        logger.warning("Preprocessing failed for %s: %s", file_path, e)
        return None


def detect_orientation_local(file_path: Path) -> dict[str, Any] | None:
    """Detect orientation using local ONNX model.

    Args:
        file_path: Path to image or video file.

    Returns:
        Dict with rotation (0/90/180/270) and confidence, or None.
    """
    session = _get_onnx_session()
    if session is None:
        return None

    tensor = _preprocess_for_onnx(file_path)
    if tensor is None:
        return None

    try:
        logits = session.run(None, {"input": tensor})[0][0]
        logits = logits - np.max(logits)  # numerical stability for softmax
        pred = int(np.argmax(logits))
        probs = np.exp(logits) / np.sum(np.exp(logits))
        return {
            "rotation": _CLASS_TO_ROTATION[pred],
            "confidence": float(probs[pred]),
        }
    except Exception as e:
        logger.warning("ONNX inference failed for %s: %s", file_path, e)
        return None


def detect_orientation_batch(
    files: list[dict[str, Any]],
    confidence_threshold: float = 0.7,
    progress_callback: Callable[[int, int, str], None] | None = None,
    api_key: str | None = None,
    model: str = "gemini-2.5-flash",
) -> list[dict[str, Any]]:
    """Detect orientation issues for a batch of files.

    Uses local ONNX model (fast, no API key). Falls back to Gemini API
    if ONNX is unavailable and api_key is provided.

    Args:
        files: List of normalized file info dicts (from scan_folder).
        confidence_threshold: Minimum confidence to report as issue.
        progress_callback: Optional callback(current, total, filename).
        api_key: Optional Google API key for Gemini fallback.
        model: Gemini model name (only used for fallback).

    Returns:
        List of dicts with file, rotation, confidence.
    """
    issues: list[dict[str, Any]] = []
    use_local = _get_onnx_session() is not None

    if not use_local and not api_key:
        logger.error("No ONNX model and no API key — cannot detect orientation")
        return []

    if use_local:
        logger.info("Using local ONNX model for orientation detection")
    else:
        logger.info("ONNX unavailable, falling back to Gemini API")

    # Filter to supported files
    candidates = [
        f for f in files if Path(f["path"]).suffix.lower() in _ALL_SUPPORTED_EXTS
    ]
    total = len(candidates)

    for idx, f in enumerate(candidates):
        filename = Path(f["path"]).name
        if progress_callback is not None:
            progress_callback(idx + 1, total, filename)

        file_path = Path(f["path"])

        if use_local:
            result = detect_orientation_local(file_path)
        else:
            result = _detect_gemini_with_fallback(file_path, api_key, model)  # type: ignore[arg-type]

        if result is None or "error" in result:
            continue

        if result["rotation"] != 0 and result["confidence"] >= confidence_threshold:
            issues.append(
                {
                    "file": f,
                    "rotation": result["rotation"],
                    "confidence": result["confidence"],
                }
            )

    return issues


# --- Video frame extraction ---

def _extract_video_frame(file_path: Path, max_size: int = 512) -> bytes | None:
    """Extract a single frame from a video file via ffmpeg.

    Args:
        file_path: Path to the video file.
        max_size: Maximum dimension in pixels for the output frame.

    Returns:
        JPEG bytes, or None if extraction failed.
    """
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        subprocess.run(
            [
                "ffmpeg", "-i", str(file_path),
                "-vframes", "1",
                "-vf", f"scale={max_size}:-1",
                "-q:v", "5",
                tmp_path, "-y",
            ],
            capture_output=True,
            timeout=15,
            check=True,
        )
        data = Path(tmp_path).read_bytes()
        Path(tmp_path).unlink(missing_ok=True)
        return data if data else None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError):
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
        return None


def _convert_heic_to_jpeg(file_path: Path, max_size: int = 512) -> bytes | None:
    """Convert HEIC to JPEG via sips (macOS).

    Args:
        file_path: Path to HEIC file.
        max_size: Maximum dimension.

    Returns:
        JPEG bytes, or None.
    """
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        subprocess.run(
            [
                "sips", "-s", "format", "jpeg",
                "-s", "formatOptions", "50",
                "-Z", str(max_size),
                str(file_path), "--out", tmp_path,
            ],
            capture_output=True,
            timeout=10,
            check=True,
        )
        data = Path(tmp_path).read_bytes()
        Path(tmp_path).unlink(missing_ok=True)
        return data
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
        return None


# --- Gemini API fallback ---

_MODEL_FALLBACK_CHAIN = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-3-flash-preview",
]

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
    """Create a JPEG thumbnail for Gemini API.

    Args:
        file_path: Path to image or video file.
        max_size: Maximum dimension in pixels.

    Returns:
        JPEG bytes, or None.
    """
    ext = file_path.suffix.lower()

    if ext in _VIDEO_EXTS:
        return _extract_video_frame(file_path, max_size)

    if ext in {".heic", ".heif"}:
        return _convert_heic_to_jpeg(file_path, max_size)

    try:
        from PIL import Image, ImageOps

        with Image.open(file_path) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((max_size, max_size))
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=50)
            return buf.getvalue()
    except Exception:
        return None


def _detect_gemini(
    image_path: Path,
    api_key: str,
    model: str = "gemini-2.5-flash",
) -> dict[str, Any] | None:
    """Detect orientation via Gemini Vision API.

    Args:
        image_path: Path to image or video file.
        api_key: Google Gemini API key.
        model: Gemini model name.

    Returns:
        Dict with rotation and confidence, or error sentinel, or None.
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
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        result = json.loads(text)
        rotation = int(result.get("rotation", 0))
        confidence = float(result.get("confidence", 0.0))

        if rotation not in (0, 90, 180, 270):
            rotation = min((0, 90, 180, 270), key=lambda x: abs(x - rotation))

        return {"rotation": rotation, "confidence": confidence}
    except Exception as e:
        logger.warning("Gemini detection failed for %s: %s", image_path, e)
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            return {"error": "rate_limit"}
        return None


def _detect_gemini_with_fallback(
    image_path: Path,
    api_key: str,
    preferred_model: str = "gemini-2.5-flash",
) -> dict[str, Any] | None:
    """Try Gemini detection with model fallback on rate limit.

    Args:
        image_path: Path to image or video file.
        api_key: Google API key.
        preferred_model: Preferred model name.

    Returns:
        Dict with rotation and confidence, or None.
    """
    chain = [preferred_model] + [m for m in _MODEL_FALLBACK_CHAIN if m != preferred_model]
    for m in chain:
        result = _detect_gemini(image_path, api_key, m)
        if result is None:
            return None
        if "error" not in result:
            return result
        logger.info("Rate limited on %s, trying next model", m)
    return None
