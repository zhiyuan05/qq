"""Video understanding helpers: sample frames with ffmpeg and build a montage."""
from __future__ import annotations

import asyncio
import json
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image

from utils.logger import setup_logger

logger = setup_logger("media.video")

_VIDEO_EXTENSIONS = {
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm",
    "video/x-matroska": "mkv",
    "video/x-msvideo": "avi",
    "video/x-flv": "flv",
}


@dataclass(frozen=True)
class VideoAnalysis:
    duration: float
    frame_count: int
    montage: bytes


def available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def video_extension(content_type: str = "", data: bytes = b"") -> str | None:
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime in _VIDEO_EXTENSIONS:
        return _VIDEO_EXTENSIONS[mime]
    if len(data) >= 12:
        if data[4:8] == b"ftyp":
            return "mp4"
        if data[:4] == b"\x1a\x45\xdf\xa3":
            return "webm"
        if data[:4] == b"RIFF" and data[8:12] == b"AVI ":
            return "avi"
        if data[:3] == b"FLV":
            return "flv"
    return None


def _probe_duration(path: Path, timeout: float) -> float | None:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "{}")
        duration = float((payload.get("format") or {}).get("duration") or 0)
    except (ValueError, TypeError):
        return None
    return duration if duration > 0 else None


def _extract_frames(path: Path, duration: float, frames: int, workdir: Path) -> list[bytes]:
    collected: list[bytes] = []
    for index in range(frames):
        timestamp = duration * (index + 0.5) / frames
        target = workdir / f"frame-{index:02d}.jpg"
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-nostdin",
                    "-threads",
                    "1",
                    "-ss",
                    f"{timestamp:.2f}",
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale='min(480,iw)':-2",
                    "-an",
                    "-sn",
                    "-q:v",
                    "4",
                    "-y",
                    str(target),
                ],
                capture_output=True,
                timeout=20,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        if result.returncode == 0 and target.is_file():
            collected.append(target.read_bytes())
    return collected


def _montage(frames: list[bytes], tile_width: int = 480) -> bytes | None:
    images: list[Image.Image] = []
    for data in frames:
        try:
            image = Image.open(BytesIO(data)).convert("RGB")
        except (OSError, ValueError):
            continue
        ratio = min(1.0, tile_width / max(1, image.width))
        size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
        images.append(image.resize(size, Image.LANCZOS))
    if not images:
        return None
    columns = 3 if len(images) > 2 else len(images)
    rows = math.ceil(len(images) / columns)
    tile_height = max(image.height for image in images)
    canvas = Image.new("RGB", (columns * tile_width, rows * tile_height), (24, 24, 24))
    for index, image in enumerate(images):
        column, row = index % columns, index // columns
        offset_x = column * tile_width + (tile_width - image.width) // 2
        offset_y = row * tile_height + (tile_height - image.height) // 2
        canvas.paste(image, (offset_x, offset_y))
    buffer = BytesIO()
    canvas.save(buffer, "JPEG", quality=82)
    return buffer.getvalue()


def _analyze_sync(path: Path, frames: int, max_seconds: int) -> VideoAnalysis | None:
    try:
        duration = _probe_duration(path, timeout=15)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if duration is None:
        return None
    sampled = min(duration, float(max_seconds))
    with tempfile.TemporaryDirectory(prefix="video-") as directory:
        extracted = _extract_frames(path, sampled, frames, Path(directory))
    montage = _montage(extracted)
    if not montage:
        return None
    return VideoAnalysis(duration=duration, frame_count=len(extracted), montage=montage)


async def analyze_video(path: Path, *, frames: int = 6, max_seconds: int = 180) -> VideoAnalysis | None:
    """Sample key frames and return a JPEG montage, or None if ffmpeg is unavailable."""
    if not available() or not path.is_file():
        return None
    try:
        return await asyncio.to_thread(_analyze_sync, path, frames, max_seconds)
    except Exception as exc:
        logger.info("视频解析失败 (%s)", type(exc).__name__)
        return None
