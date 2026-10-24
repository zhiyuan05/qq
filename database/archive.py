"""Append-only conversation archive used by summary and persona cursors."""
from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database.db import get_session_factory, resolve_database
from database.models import ConversationArchive
from platforms.models import Attachment, attachment_to_dict
from utils.logger import setup_logger

logger = setup_logger("archive")


class ConversationArchiveStore:
    """Idempotent append-only transcript.

    The live ``conversations`` table is a replaceable snapshot; this store keeps
    stable ids for incremental summaries and persona extraction.
    """

    def __init__(self, database: str, *, pool_size: int = 5, max_overflow: int = 10, pool_timeout: float = 30.0):
        self.database = database
        self.location = resolve_database(database)
        self._session_factory = get_session_factory(
            database,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
        )

    async def append_turns(
        self,
        session_id: str,
        user_id: str,
        request_id: str,
        turns: Sequence[tuple[str, str, Sequence[Attachment]]],
        *,
        created_at: datetime | None = None,
    ) -> int:
        """Insert user/assistant rows, ignoring turns already archived."""

        if not turns:
            return 0
        timestamp = created_at or datetime.now()
        rows = [
            {
                "session_id": session_id,
                "user_id": user_id,
                "request_id": request_id[:255],
                "turn_index": index,
                "role": role,
                "content": content,
                "attachments_json": json.dumps(
                    [attachment_to_dict(attachment) for attachment in attachments],
                    ensure_ascii=False,
                ),
                "created_at": timestamp,
            }
            for index, (role, content, attachments) in enumerate(turns)
        ]
        async with self._session_factory() as database_session:
            async with database_session.begin():
                dialect = database_session.bind.dialect.name
                if dialect == "sqlite":
                    statement = sqlite_insert(ConversationArchive).values(rows)
                elif dialect == "postgresql":
                    statement = postgresql_insert(ConversationArchive).values(rows)
                else:
                    raise RuntimeError(f"unsupported archive dialect: {dialect}")
                await database_session.execute(statement.on_conflict_do_nothing())
        return len(rows)

    async def fetch_after(self, session_id: str, after_id: int, limit: int = 200) -> list[dict[str, Any]]:
        async with self._session_factory() as database_session:
            rows = (
                await database_session.scalars(
                    select(ConversationArchive)
                    .where(
                        ConversationArchive.session_id == session_id,
                        ConversationArchive.id > after_id,
                    )
                    .order_by(ConversationArchive.id.asc())
                    .limit(limit)
                )
            ).all()
        return [
            {
                "id": row.id,
                "role": row.role,
                "content": row.content,
                "attachments_json": row.attachments_json,
                "created_at": row.created_at,
            }
            for row in rows
        ]

    async def latest_id(self, session_id: str) -> int:
        async with self._session_factory() as database_session:
            value = await database_session.scalar(
                select(func.max(ConversationArchive.id)).where(ConversationArchive.session_id == session_id)
            )
        return int(value or 0)

    async def prune(self, retention_days: int, max_rows: int | None = None) -> int:
        """Delete old rows, keeping the newest ``max_rows`` when configured."""

        removed = 0
        cutoff = datetime.now() - timedelta(days=retention_days)
        async with self._session_factory() as database_session:
            async with database_session.begin():
                result = await database_session.execute(
                    delete(ConversationArchive).where(ConversationArchive.created_at < cutoff)
                )
                removed += int(result.rowcount or 0)
                if max_rows is not None:
                    total = await database_session.scalar(select(func.count()).select_from(ConversationArchive))
                    excess = int(total or 0) - max_rows
                    if excess > 0:
                        keep_ids = select(ConversationArchive.id).order_by(ConversationArchive.id.desc()).limit(max_rows)
                        result = await database_session.execute(
                            delete(ConversationArchive).where(ConversationArchive.id.not_in(keep_ids))
                        )
                        removed += int(result.rowcount or 0)
        return removed

    async def stats(self) -> dict[str, Any]:
        async with self._session_factory() as database_session:
            rows = await database_session.scalar(select(func.count()).select_from(ConversationArchive))
        return {"backend": self.location.backend, "rows": int(rows or 0)}
