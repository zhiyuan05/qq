"""Image sniffing and vision-payload normalization."""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass

from utils.logger import setup_logger

logger = setup_logger("media.images")


@dataclass(frozen=True)
class ImageInfo:
    mime: str
    width: int
    height: int
    frames: int = 1
    format: str = ""


def sniff_image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def image_info(data: bytes) -> ImageInfo | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            return ImageInfo(
                mime=sniff_image_mime(data) or Image.MIME.get(image.format or "", ""),
                width=image.width,
                height=image.height,
                frames=max(1, int(getattr(image, "n_frames", 1))),
                format=str(image.format or "").lower(),
            )
    except Exception:
        return None


def describe_image(data: bytes) -> str:
    info = image_info(data)
    if info is None:
        return ""
    parts = [f"{info.format or info.mime} {info.width}x{info.height}", f"{max(1, len(data) // 1024)}KB"]
    if info.frames > 1:
        parts.append(f"动图 {info.frames} 帧")
    return "，".join(parts)


def prepare_for_vision(
    data: bytes,
    *,
    max_side: int = 1280,
    max_bytes: int = 1024 * 1024,
) -> tuple[bytes, str] | None:
    """Return a JPEG/PNG payload within the model budget, or None on failure.

    Mirrors qq-bridge's media-normalize: small images pass through untouched,
    animated images become a sampled montage, static images are retried at
    progressively smaller edges, and transparent images stay PNG.
    """

    mime = sniff_image_mime(data)
    if not mime:
        return None
    info = image_info(data)
    if info is not None and info.mime:
        mime = info.mime
    try:
        from PIL import Image, ImageOps
    except ImportError:
        if info is None:
            return (data, mime) if len(data) <= max_bytes else None
        if len(data) <= max_bytes and max(info.width, info.height) <= max_side and info.frames <= 1:
            return (data, mime)
        return None

    # Fast path: already small enough.
    if info is not None and len(data) <= max_bytes and max(info.width, info.height) <= max_side and info.frames <= 1:
        return (data, mime)

    try:
        with Image.open(io.BytesIO(data)) as source:
            if getattr(source, "is_animated", False) and getattr(source, "n_frames", 1) > 1:
                image = _animated_montage(source, tile=240)
                for quality in (85, 75, 65):
                    buffer = io.BytesIO()
                    image.save(buffer, format="JPEG", quality=quality, optimize=True)
                    payload = buffer.getvalue()
                    if len(payload) <= max_bytes:
                        return payload, "image/jpeg"
                return None

            # JPEG can decode at a reduced scale, avoiding a full 64MP RGBA buffer.
            try:
                source.draft("RGB", (max_side, max_side))
            except Exception:
                pass
            image = ImageOps.exif_transpose(source)
            has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info

            # Source-faithful shrink loop: edge 1280 -> 896 -> 627 (0.7x per try).
            for attempt in range(3):
                edge = max(256, int(max_side * (0.7**attempt)))
                current = image.copy()
                width, height = current.size
                if max(width, height) > edge:
                    scale = edge / max(width, height)
                    current = current.resize((max(1, int(width * scale)), max(1, int(height * scale))))
                if has_alpha:
                    buffer = io.BytesIO()
                    current.save(buffer, format="PNG", optimize=True)
                    payload = buffer.getvalue()
                    if len(payload) <= max_bytes:
                        return payload, "image/png"
                rgb = current.convert("RGB")
                for quality in (85, 75, 65):
                    buffer = io.BytesIO()
                    rgb.save(buffer, format="JPEG", quality=quality, optimize=True)
                    payload = buffer.getvalue()
                    if len(payload) <= max_bytes:
                        return payload, "image/jpeg"
            return None
    except Exception as exc:
        logger.debug("图片归一化失败 (%s)", type(exc).__name__)
        return None


def _animated_montage(source, tile: int = 240):
    """Sample up to six frames into a padded grid; rows are read left-to-right."""

    from PIL import Image

    frame_count = max(1, int(getattr(source, "n_frames", 1)))
    samples = min(6, frame_count)
    if samples <= 1:
        return source.convert("RGB")
    layouts = {2: (2, 1), 3: (3, 1), 4: (2, 2), 5: (3, 2), 6: (3, 2)}
    columns, rows = layouts.get(samples, (3, 2))
    padding = 4
    margin = 4
    canvas = Image.new(
        "RGB",
        (columns * tile + (columns - 1) * padding + margin * 2,
         rows * tile + (rows - 1) * padding + margin * 2),
        (255, 255, 255),
    )
    indexes = [round(index * (frame_count - 1) / (samples - 1)) for index in range(samples)]
    for slot, frame_index in enumerate(indexes):
        source.seek(frame_index)
        frame = source.convert("RGBA")
        frame.thumbnail((tile, tile))
        background = Image.new("RGBA", frame.size, (255, 255, 255, 255))
        background.alpha_composite(frame)
        column = slot % columns
        row = slot // columns
        x = margin + column * (tile + padding) + (tile - frame.width) // 2
        y = margin + row * (tile + padding) + (tile - frame.height) // 2
        canvas.paste(background.convert("RGB"), (x, y))
    return canvas


def data_url(payload: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"
