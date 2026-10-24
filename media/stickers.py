"""Sticker library, auto-collection, review queue and tag selection."""
from __future__ import annotations

import asyncio
import hashlib
import os
import random
import re
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from media.images import image_info, sniff_image_mime
from utils.config import StickersConfig
from utils.logger import setup_logger

logger = setup_logger("stickers")

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_TAG_SPLIT = re.compile(r"[^\w一-鿿]+")
_TAG_OK = re.compile(r"[A-Za-z0-9_一-鿿]{1,12}")
_TAG_BLOCK = re.compile(
    r"忽略|指令|规则|系统|提示|命令|色情|裸露|裸体|血腥|暴力|恐怖|自残|毒品|"
    r"ignore|system|prompt|assistant|nsfw|nude|porn|gore",
    re.I,
)


def sanitize_tag(value: str) -> str | None:
    tag = (value or "").strip()
    if not tag or not _TAG_OK.fullmatch(tag) or _TAG_BLOCK.search(tag):
        return None
    return tag


@dataclass(frozen=True)
class Sticker:
    hash: str
    path: str
    tags: tuple[str, ...]
    emotion: str
    source: str
    status: str
    used_count: int


def default_tags(path: Path) -> list[str]:
    stem = _TAG_SPLIT.sub(" ", path.stem).strip()
    tags = [part for part in stem.split() if part][:5]
    tags.append("默认")
    return list(dict.fromkeys(tags))


class StickerLibrary:
    def __init__(
        self,
        config: StickersConfig,
        *,
        database_path: str = "data/stickers.db",
        tagger: Callable[[str], Awaitable[list[str]]] | None = None,
        rng: random.Random | None = None,
    ):
        self.config = config
        self.root = Path(config.library_dir)
        self.database_path = Path(database_path)
        self._tagger = tagger
        self._rng = rng or random.Random()
        self._last_session: dict[str, float] = {}
        self._last_hash: dict[str, float] = {}
        self._tags_cache: tuple[float, list[str]] = (0.0, [])

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS stickers (
                hash TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                tags TEXT NOT NULL,
                emotion TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'library',
                status TEXT NOT NULL DEFAULT 'active',
                enabled INTEGER NOT NULL DEFAULT 1,
                used_count INTEGER NOT NULL DEFAULT 0,
                size INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(stickers)")}
        if "tag_attempts" not in columns:
            connection.execute(
                "ALTER TABLE stickers ADD COLUMN tag_attempts INTEGER NOT NULL DEFAULT 0"
            )
        if "auto_tagged" not in columns:
            connection.execute(
                "ALTER TABLE stickers ADD COLUMN auto_tagged INTEGER NOT NULL DEFAULT 0"
            )
        connection.commit()
        return connection

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _sticker(row: sqlite3.Row) -> Sticker:
        return Sticker(
            hash=row["hash"],
            path=row["path"],
            tags=tuple(tag for tag in row["tags"].split(",") if tag),
            emotion=row["emotion"],
            source=row["source"],
            status=row["status"],
            used_count=row["used_count"],
        )

    def _index_blocking(self) -> int:
        if not self.root.exists():
            return 0
        connection = self._connect()
        inserted = 0
        try:
            # Disable rows whose files disappeared instead of selecting dead paths.
            for digest, path in connection.execute("SELECT hash, path FROM stickers").fetchall():
                if not Path(path).is_file():
                    connection.execute("UPDATE stickers SET enabled = 0 WHERE hash = ?", (digest,))
            known = {row[0] for row in connection.execute("SELECT hash FROM stickers")}
            # Sort so identical files resolve to the same sticker on every filesystem.
            for path in sorted(self.root.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
                    continue
                try:
                    digest = self._hash_file(path)
                except OSError:
                    continue
                if digest in known:
                    continue
                connection.execute(
                    "INSERT INTO stickers (hash, path, tags, source, status, size, created_at) "
                    "VALUES (?, ?, ?, 'library', 'active', ?, datetime('now'))",
                    (digest, str(path), ",".join(default_tags(path)), path.stat().st_size),
                )
                known.add(digest)
                inserted += 1
            connection.commit()
        finally:
            connection.close()
        return inserted

    async def index(self) -> int:
        return await asyncio.to_thread(self._index_blocking)

    async def tag_missing(self, limit: int = 20) -> int:
        if self._tagger is None:
            return 0
        rows = await asyncio.to_thread(self._pending_tags_blocking, limit)
        updated = 0
        for digest, path in rows:
            try:
                tags = await self._tagger(path)
            except Exception as exc:
                logger.debug("表情包打标失败 (%s)", type(exc).__name__)
                await asyncio.to_thread(self._bump_tag_attempt_blocking, digest)
                continue
            if tags:
                await asyncio.to_thread(self._set_tags_blocking, digest, tags)
                updated += 1
            else:
                # Cap retries so unreadable images do not burn a vision call forever.
                await asyncio.to_thread(self._bump_tag_attempt_blocking, digest)
        return updated

    def _pending_tags_blocking(self, limit: int) -> list[tuple[str, str]]:
        connection = self._connect()
        try:
            return [
                (row["hash"], row["path"])
                for row in connection.execute(
                    "SELECT hash, path FROM stickers "
                    "WHERE auto_tagged = 0 AND tag_attempts < 3 LIMIT ?",
                    (limit,),
                )
            ]
        finally:
            connection.close()

    def _bump_tag_attempt_blocking(self, digest: str) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "UPDATE stickers SET tag_attempts = tag_attempts + 1 WHERE hash = ?",
                (digest,),
            )
            connection.commit()
        finally:
            connection.close()

    def _set_tags_blocking(self, digest: str, tags: list[str]) -> None:
        connection = self._connect()
        try:
            row = connection.execute("SELECT tags FROM stickers WHERE hash = ?", (digest,)).fetchone()
            existing = [tag for tag in (row["tags"] if row else "").split(",") if tag]
            merged = list(dict.fromkeys([*existing, *tags]))[:8]
            connection.execute(
                "UPDATE stickers SET tags = ?, auto_tagged = 1, tag_attempts = tag_attempts + 1 "
                "WHERE hash = ?",
                (",".join(merged), digest),
            )
            connection.commit()
        finally:
            connection.close()

    async def collect(self, path: str, *, source: str = "collected") -> bool:
        if not self.config.enabled or not self.config.auto_collect:
            return False
        try:
            data = Path(path).read_bytes()
        except OSError:
            return False
        mime = sniff_image_mime(data)
        if not mime:
            return False
        info = image_info(data)
        if info is None:
            return False
        suffix = Path(path).suffix.lower()
        animated = suffix in {".gif", ".webp"}
        if not animated and (max(info.width, info.height) > 512 or len(data) > self.config.max_bytes):
            return False
        return await asyncio.to_thread(self._collect_blocking, data, suffix, source)

    def _collect_blocking(self, data: bytes, suffix: str, source: str) -> bool:
        digest = hashlib.sha256(data).hexdigest()
        connection = self._connect()
        try:
            if connection.execute("SELECT 1 FROM stickers WHERE hash = ?", (digest,)).fetchone():
                return False
            today = date.today().isoformat()
            count = connection.execute(
                "SELECT COUNT(*) FROM stickers WHERE source = ? AND created_at >= ?",
                (source, today),
            ).fetchone()[0]
            if count >= self.config.max_per_day:
                return False
            total = connection.execute("SELECT COALESCE(SUM(size), 0) FROM stickers").fetchone()[0]
            if total + len(data) > self.config.max_total_bytes:
                return False
            target_dir = self.root / "collected"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{digest[:16]}{suffix or '.img'}"
            temp = target.with_suffix(target.suffix + ".tmp")
            temp.write_bytes(data)
            os.replace(temp, target)
            status = "candidate" if self.config.review_required else "active"
            connection.execute(
                "INSERT INTO stickers (hash, path, tags, source, status, size, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
                (digest, str(target), "默认", source, status, len(data)),
            )
            connection.commit()
            return True
        finally:
            connection.close()

    async def pick(self, tag: str, session_key: str) -> Sticker | None:
        if not self.config.enabled or not tag:
            return None
        return await asyncio.to_thread(self._pick_blocking, tag, session_key)

    def _pick_blocking(self, tag: str, session_key: str) -> Sticker | None:
        now = time.monotonic()
        last_session = self._last_session.get(session_key)
        if last_session is not None and now - last_session < self.config.cooldown_seconds:
            return None
        connection = self._connect()
        try:
            rows = [
                self._sticker(row)
                for row in connection.execute(
                    "SELECT * FROM stickers WHERE status = 'active' AND enabled = 1"
                )
            ]
        finally:
            connection.close()
        if not rows:
            return None
        exact = [row for row in rows if tag in row.tags]
        partial = [row for row in rows if any(tag in existing or existing in tag for existing in row.tags)]
        candidates = exact or partial
        if not candidates:
            if self._rng.random() > self.config.send_probability:
                return None
            candidates = rows
        candidates = [
            row
            for row in candidates
            if (last_hash := self._last_hash.get(row.hash)) is None
            or now - last_hash >= self.config.repeat_window_seconds
        ]
        if not candidates:
            return None
        chosen = self._rng.choice(sorted(candidates, key=lambda row: row.used_count))
        self._last_session[session_key] = now
        self._last_hash[chosen.hash] = now
        return chosen

    async def mark_used(self, sticker: Sticker) -> None:
        await asyncio.to_thread(self._mark_used_blocking, sticker.hash)

    async def mark_used_hash(self, digest: str) -> None:
        await asyncio.to_thread(self._mark_used_blocking, digest)

    def _mark_used_blocking(self, digest: str) -> None:
        connection = self._connect()
        try:
            connection.execute("UPDATE stickers SET used_count = used_count + 1 WHERE hash = ?", (digest,))
            connection.commit()
        finally:
            connection.close()

    async def approve(self, digest: str) -> bool:
        return await asyncio.to_thread(self._set_status_blocking, digest, "active")

    async def reject(self, digest: str) -> bool:
        return await asyncio.to_thread(self._set_status_blocking, digest, "rejected")

    def _set_status_blocking(self, digest: str, status: str) -> bool:
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE stickers SET status = ?, enabled = ? WHERE hash = ?",
                (status, 0 if status == "rejected" else 1, digest),
            )
            connection.commit()
            return cursor.rowcount > 0
        finally:
            connection.close()

    async def list(self, status: str = "", limit: int = 500) -> list[Sticker]:
        return await asyncio.to_thread(self._list_blocking, status, limit)

    def _list_blocking(self, status: str, limit: int) -> list[Sticker]:
        connection = self._connect()
        try:
            if status:
                rows = connection.execute(
                    "SELECT * FROM stickers WHERE status = ? ORDER BY used_count DESC LIMIT ?",
                    (status, limit),
                )
            else:
                rows = connection.execute(
                    "SELECT * FROM stickers ORDER BY used_count DESC LIMIT ?",
                    (limit,),
                )
            return [self._sticker(row) for row in rows]
        finally:
            connection.close()

    async def top_tags(self, limit: int = 12, *, ttl: float = 300.0) -> list[str]:
        """Most common usable tags for teaching the model the sticker protocol."""
        cached_at, cached = self._tags_cache
        if cached and time.monotonic() - cached_at < ttl:
            return list(cached)
        try:
            stickers = await self.list(status="active", limit=300)
        except Exception:
            return []
        counts: dict[str, int] = {}
        for sticker in stickers:
            for raw_tag in sticker.tags:
                tag = sanitize_tag(raw_tag)
                if tag and tag != "默认":
                    counts[tag] = counts.get(tag, 0) + 1
        tags = [
            tag
            for tag, _ in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]
        ]
        self._tags_cache = (time.monotonic(), tags)
        return list(tags)

    async def stats(self) -> dict[str, int]:
        return await asyncio.to_thread(self._stats_blocking)

    def _stats_blocking(self) -> dict[str, int]:
        connection = self._connect()
        try:
            result = {
                row[0]: row[1]
                for row in connection.execute("SELECT status, COUNT(*) FROM stickers GROUP BY status")
            }
        finally:
            connection.close()
        return {str(key): int(value) for key, value in result.items()}
