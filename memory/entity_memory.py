"""Dialect-neutral persistent entity profile store."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database.db import get_session_factory, run_migrations
from database.models import EntityMemory as EntityMemoryRecord
from utils.logger import setup_logger

logger = setup_logger("entity_memory")


def _terms(text: str) -> set[str]:
    lowered = (text or "").lower()
    words = set(re.findall(r"[a-z0-9]+", lowered))
    chars = [char for char in lowered if "一" <= char <= "鿿"]
    words.update("".join(chars[index : index + 2]) for index in range(max(0, len(chars) - 1)))
    return words


@dataclass(slots=True)
class Entity:
    entity_type: str
    key: str
    value: str
    confidence: float = 1.0
    source: str = "auto"
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.updated_at is None:
            self.updated_at = datetime.now()


class EntityMemory:
    """Database-backed entity memory sharing the application's connection pool."""

    def __init__(
        self,
        database: str = "data/chatbot.db",
        *,
        db_path: str | None = None,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_timeout: float = 30.0,
        max_profile_chars: int = 1000,
        inject_top_k: int = 6,
    ):
        # db_path remains accepted for compatibility with existing callers.
        self._database = db_path or database
        self.max_profile_chars = max_profile_chars
        self.inject_top_k = max(1, inject_top_k)
        self._pool_options = {
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "pool_timeout": pool_timeout,
        }
        self._session_factory = get_session_factory(
            self._database,
            **self._pool_options,
        )
        self._schema_ready = False
        self._schema_lock = asyncio.Lock()

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            await run_migrations(self._database, **self._pool_options)
            self._schema_ready = True

    async def store(
        self,
        user_id: str,
        entity_type: str,
        key: str,
        value: str,
        confidence: float = 1.0,
        source: str = "auto",
    ) -> None:
        await self._ensure_schema()
        now = datetime.now()
        values = {
            "user_id": str(user_id),
            "entity_type": entity_type,
            "key": key,
            "value": value.replace("\x00", ""),
            "confidence": confidence,
            "source": source,
            "created_at": now,
            "updated_at": now,
        }
        try:
            async with self._session_factory() as database_session:
                async with database_session.begin():
                    dialect = database_session.bind.dialect.name
                    if dialect == "sqlite":
                        statement = sqlite_insert(EntityMemoryRecord).values(**values)
                    elif dialect == "postgresql":
                        statement = postgresql_insert(EntityMemoryRecord).values(**values)
                    else:
                        raise RuntimeError(f"unsupported entity-memory dialect: {dialect}")
                    await database_session.execute(
                        statement.on_conflict_do_update(
                            index_elements=[
                                EntityMemoryRecord.user_id,
                                EntityMemoryRecord.entity_type,
                                EntityMemoryRecord.key,
                            ],
                            set_={
                                "value": values["value"],
                                "confidence": confidence,
                                "source": source,
                                "updated_at": now,
                            },
                        )
                    )
        except Exception as exc:
            logger.error("实体记忆存储失败 (%s)", type(exc).__name__)

    async def query(self, user_id: str, entity_type: str | None = None) -> list[Entity]:
        await self._ensure_schema()
        try:
            async with self._session_factory() as database_session:
                statement = select(EntityMemoryRecord).where(EntityMemoryRecord.user_id == str(user_id))
                if entity_type:
                    statement = statement.where(EntityMemoryRecord.entity_type == entity_type)
                statement = statement.order_by(EntityMemoryRecord.id.asc())
                records = (await database_session.execute(statement)).scalars().all()
            return [
                Entity(
                    entity_type=record.entity_type,
                    key=record.key,
                    value=record.value,
                    confidence=record.confidence,
                    source=record.source,
                    updated_at=record.updated_at,
                )
                for record in records
            ]
        except Exception as exc:
            logger.error("实体记忆查询失败 (%s)", type(exc).__name__)
            return []

    async def get_profile_text(self, user_id: str) -> str:
        entities = await self.query(user_id)
        if not entities:
            return ""
        lines = ["用户画像:"]
        lines.extend(f"- {entity.key}: {entity.value}" for entity in entities)
        return "\n".join(lines)[: self.max_profile_chars]

    async def get_relevant_profile_text(self, user_id: str, query: str = "") -> str:
        """Inject only the entities relevant to this turn, never the full profile."""

        entities = await self.query(user_id)
        if not entities:
            return ""
        terms = _terms(query)
        # Nicknames, promises and shared experiences matter even when the
        # current sentence shares no keywords, so keep a small core block.
        core = [entity for entity in entities if entity.entity_type == "relationship" and entity.value][:2]
        core_keys = {(entity.entity_type, entity.key) for entity in core}

        scored: list[tuple[int, Entity]] = []
        for entity in entities:
            if (entity.entity_type, entity.key) in core_keys:
                continue
            overlap = len(_terms(f"{entity.key} {entity.value}") & terms)
            if overlap > 0:
                scored.append((overlap, entity))
        if not scored and not core:
            return ""
        ranked = sorted(
            scored,
            key=lambda item: (item[0], item[1].confidence, item[1].updated_at),
            reverse=True,
        )
        remaining = max(0, self.inject_top_k - len(core))
        selected = core + [entity for _overlap, entity in ranked[:remaining]]
        lines = ["用户画像:"]
        lines.extend(f"- {entity.key}: {entity.value}" for entity in selected)
        return "\n".join(lines)[: self.max_profile_chars]

    async def forget(self, user_id: str, key: str) -> bool:
        await self._ensure_schema()
        try:
            async with self._session_factory() as database_session:
                async with database_session.begin():
                    result = await database_session.execute(
                        delete(EntityMemoryRecord).where(
                            EntityMemoryRecord.user_id == str(user_id),
                            EntityMemoryRecord.key == key,
                        )
                    )
                    return bool(result.rowcount)
        except Exception as exc:
            logger.error("实体记忆删除失败 (%s)", type(exc).__name__)
            return False

    async def clear(self, user_id: str) -> None:
        await self._ensure_schema()
        try:
            async with self._session_factory() as database_session:
                async with database_session.begin():
                    await database_session.execute(
                        delete(EntityMemoryRecord).where(EntityMemoryRecord.user_id == str(user_id))
                    )
        except Exception as exc:
            logger.error("实体记忆清空失败 (%s)", type(exc).__name__)

    async def stats(self) -> dict[str, Any]:
        await self._ensure_schema()
        async with self._session_factory() as database_session:
            entities, users = (
                await database_session.execute(
                    select(
                        func.count(EntityMemoryRecord.id),
                        func.count(func.distinct(EntityMemoryRecord.user_id)),
                    )
                )
            ).one()
        return {"entities": int(entities or 0), "users": int(users or 0)}
