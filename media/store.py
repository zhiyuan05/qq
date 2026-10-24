"""Content-addressed inbound media storage."""
from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import datetime, timedelta
from pathlib import Path

from media.images import image_info, sniff_image_mime
from media.net import MediaFetchError, fetch_bytes
from media.video import video_extension
from platforms.models import Attachment, MessageType
from utils.config import MediaConfig
from utils.logger import setup_logger

logger = setup_logger("media.store")

_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}


class MediaStore:
    def __init__(self, config: MediaConfig):
        self.config = config
        self.root = Path(config.storage_dir)

    def _month_dir(self) -> Path:
        path = self.root / datetime.now().strftime("%Y-%m")
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _hash(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _write(self, data: bytes, digest: str, extension: str) -> Path:
        target = self._month_dir() / f"{digest[:32]}.{extension}"
        if target.exists():
            return target
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_bytes(data)
        os.replace(temp, target)
        return target

    async def store_image(self, data: bytes, *, source: str = "") -> Attachment | None:
        mime = sniff_image_mime(data)
        if not mime:
            logger.debug("拒绝非图片内容: %s", source[:80])
            return None
        info = image_info(data)
        if info is not None and info.width * info.height > self.config.max_image_pixels:
            logger.debug("图片像素超限: %sx%s", info.width, info.height)
            return None
        digest = self._hash(data)
        path = await asyncio.to_thread(self._write, data, digest, _EXTENSIONS.get(mime, "bin"))
        return Attachment(
            kind=MessageType.IMAGE,
            path=str(path),
            mime=mime,
            size=len(data),
            sha256=digest,
        )

    async def store_video(self, data: bytes, *, content_type: str = "") -> Attachment | None:
        extension = video_extension(content_type, data)
        if not extension:
            logger.debug("拒绝非视频内容")
            return None
        if len(data) > self.config.max_video_bytes:
            logger.debug("视频体积超限: %s", len(data))
            return None
        digest = self._hash(data)
        path = await asyncio.to_thread(self._write, data, digest, extension)
        return Attachment(
            kind=MessageType.VIDEO,
            path=str(path),
            mime=(content_type or f"video/{extension}").split(";")[0].strip(),
            size=len(data),
            sha256=digest,
        )

    async def fetch_video(self, url: str) -> Attachment | None:
        if not self.config.enabled or not url:
            return None
        try:
            result = await fetch_bytes(
                url,
                max_bytes=self.config.max_video_bytes,
                total_timeout=max(self.config.fetch_total_timeout, 30.0),
                attempt_timeout=max(self.config.fetch_attempt_timeout, 10.0),
                max_redirects=self.config.max_redirects,
            )
        except MediaFetchError as exc:
            logger.info("视频下载失败: %s", exc)
            return None
        return await self.store_video(result.content, content_type=result.content_type)

    async def fetch_image(self, url: str) -> Attachment | None:
        if not self.config.enabled or not url:
            return None
        try:
            result = await fetch_bytes(
                url,
                max_bytes=self.config.max_fetch_bytes,
                total_timeout=self.config.fetch_total_timeout,
                attempt_timeout=self.config.fetch_attempt_timeout,
                max_redirects=self.config.max_redirects,
            )
        except MediaFetchError as exc:
            logger.info("图片下载失败: %s", exc)
            return None
        return await self.store_image(result.content, source=result.final_url)

    async def cleanup(self, keep_paths: set[str] | None = None) -> int:
        keep = {str(Path(path).resolve()) for path in (keep_paths or set())}
        cutoff = datetime.now() - timedelta(days=self.config.retention_days)
        removed = 0

        def _cleanup() -> int:
            nonlocal removed
            if not self.root.exists():
                return 0
            for path in self.root.rglob("*"):
                if not path.is_file() or path.suffix == ".tmp":
                    continue
                if str(path.resolve()) in keep:
                    continue
                modified = datetime.fromtimestamp(path.stat().st_mtime)
                if modified < cutoff:
                    try:
                        path.unlink()
                        removed += 1
                    except OSError:
                        continue
            return removed

        return await asyncio.to_thread(_cleanup)


def validate_local_file(root: str, path: str) -> Path | None:
    """Resolve a path and require it to stay inside ``root``."""

    try:
        root_path = Path(root).resolve()
        candidate = Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if candidate != root_path and root_path not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None
