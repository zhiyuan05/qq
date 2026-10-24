"""Dialect-neutral, optimistic-concurrency session persistence."""
from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Protocol

from sqlalchemy import and_, delete, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.orm.exc import StaleDataError

from core.session import ChatMessage, Session
from database.db import get_session_factory, resolve_database
from database.models import ChatSessionRecord, Conversation, User
from platforms.models import attachment_from_dict, attachment_to_dict
from utils.logger import setup_logger

logger = setup_logger("session_store")


class SessionConflictError(RuntimeError):
    """A stale snapshot attempted to overwrite a newer committed session."""

    def __init__(self, session_id: str, expected: int, actual: int | None = None):
        detail = f"session {session_id!r} revision conflict: expected {expected}"
        if actual is not None:
            detail += f", actual {actual}"
        super().__init__(detail)
        self.session_id = session_id
        self.expected = expected
        self.actual = actual


class SessionStore(Protocol):
    async def load_sessions(self, limit: int = 200, messages_per_session: int = 20) -> list[Session]: ...

    async def load_session(self, session_id: str, messages_per_session: int = 20) -> Session | None: ...

    async def save_session(self, session: Session) -> int: ...

    async def delete_session(self, session_id: str) -> None: ...

    async def health_check(self) -> bool: ...

    async def stats(self) -> dict[str, Any]: ...


class SQLAlchemySessionStore:
    """Snapshot store supporting SQLite and PostgreSQL through one contract."""

    def __init__(
        self,
        database: str = "data/chatbot.db",
        *,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_timeout: float = 30.0,
    ):
        self.database = database
        self.location = resolve_database(database)
        self._session_factory = get_session_factory(
            database,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
        )

    @staticmethod
    def _clean_text(value: str) -> str:
        return value.replace("\x00", "")

    @staticmethod
    def _parse_attachments(raw: str | None) -> list:
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [attachment_from_dict(item) for item in data if isinstance(item, dict)]

    @classmethod
    def _to_session(cls, record: ChatSessionRecord, messages: Sequence[Conversation]) -> Session:
        return Session(
            session_id=record.session_id,
            user_id=record.user_id,
            platform=record.platform,
            character_card=record.character_card or "default",
            style=record.style or "casual",
            messages=[
                ChatMessage(
                    role=message.role,
                    content=message.content,
                    timestamp=message.created_at,
                    token_count=message.token_count or 0,
                    attachments=cls._parse_attachments(message.attachments_json),
                )
                for message in messages
            ],
            summary=record.summary or "",
            summary_until_id=record.summary_until_id or 0,
            created_at=record.created_at,
            last_active=record.last_active,
            revision=record.revision,
        )

    @staticmethod
    def _recent_window(messages_per_session: int):
        """Ids of the newest N conversations per session, in one portable query."""

        ranked = (
            select(
                Conversation.id.label("id"),
                func.row_number()
                .over(partition_by=Conversation.session_id, order_by=Conversation.id.desc())
                .label("row_number"),
            )
            .subquery()
        )
        return select(ranked.c.id).where(ranked.c.row_number <= messages_per_session)

    async def load_sessions(self, limit: int = 200, messages_per_session: int = 20) -> list[Session]:
        async with self._session_factory() as database_session:
            selected_sessions = (
                select(ChatSessionRecord)
                .order_by(ChatSessionRecord.last_active.desc())
                .limit(limit)
                .subquery()
            )
            session_record = aliased(ChatSessionRecord, selected_sessions)
            result = await database_session.execute(
                select(session_record, Conversation)
                .outerjoin(
                    Conversation,
                    and_(
                        Conversation.session_id == session_record.session_id,
                        Conversation.id.in_(self._recent_window(messages_per_session)),
                    ),
                )
                .order_by(session_record.last_active.desc(), Conversation.id.asc())
            )
            rows = result.all()
            if not rows:
                return []

            records: dict[str, ChatSessionRecord] = {}
            grouped: dict[str, list[Conversation]] = {}
            for record, message in rows:
                records.setdefault(record.session_id, record)
                if message is not None:
                    grouped.setdefault(record.session_id, []).append(message)
            return [self._to_session(record, grouped.get(record.session_id, [])) for record in records.values()]

    async def load_session(self, session_id: str, messages_per_session: int = 20) -> Session | None:
        async with self._session_factory() as database_session:
            result = await database_session.execute(
                select(ChatSessionRecord, Conversation)
                .outerjoin(
                    Conversation,
                    and_(
                        Conversation.session_id == ChatSessionRecord.session_id,
                        Conversation.id.in_(self._recent_window(messages_per_session)),
                    ),
                )
                .where(ChatSessionRecord.session_id == session_id)
                .order_by(Conversation.id.asc())
            )
            rows = result.all()
            if not rows:
                return None
            record = rows[0][0]
            messages = [message for _, message in rows if message is not None]
            return self._to_session(record, messages)

    async def save_session(self, session: Session) -> int:
        """Atomically replace a snapshot if its revision is still current."""

        async with self._session_factory() as database_session:
            async with database_session.begin():
                next_revision = await self.save_session_in_transaction(database_session, session)
        session.revision = next_revision
        return next_revision

    async def save_session_in_transaction(self, database_session: AsyncSession, session: Session) -> int:
        """Stage a CAS snapshot in the caller's transaction, without committing it.

        The caller must publish the returned revision to its in-memory session only
        after the outer transaction commits. This lets a Run, its session projection,
        and delivery commands succeed or roll back together.
        """

        try:
            platform_identity = f"{session.platform}:{session.user_id}"
            values = {
                "platform": session.platform,
                "platform_id": platform_identity,
                "last_active": session.last_active,
            }
            dialect = database_session.bind.dialect.name
            if dialect == "sqlite":
                user_insert = sqlite_insert(User).values(**values)
            elif dialect == "postgresql":
                user_insert = postgresql_insert(User).values(**values)
            else:
                raise RuntimeError(f"unsupported session-store dialect: {dialect}")
            await database_session.execute(
                user_insert.on_conflict_do_update(
                    index_elements=[User.platform_id],
                    set_={"platform": session.platform, "last_active": session.last_active},
                )
            )
            user_id = await database_session.scalar(select(User.id).where(User.platform_id == platform_identity))
            if user_id is None:
                raise RuntimeError("session user upsert returned no identity")

            record = await database_session.get(ChatSessionRecord, session.session_id)
            if record is None:
                if session.revision != 0:
                    raise SessionConflictError(session.session_id, session.revision, None)
                record = ChatSessionRecord(session_id=session.session_id, revision=1)
                database_session.add(record)
                next_revision = 1
            else:
                if session.revision != record.revision:
                    raise SessionConflictError(session.session_id, session.revision, record.revision)
                record.revision += 1
                next_revision = record.revision

            record.user_id = session.user_id
            record.platform = session.platform
            record.character_card = session.character_card
            record.style = session.style
            record.summary = self._clean_text(session.summary)
            record.summary_until_id = session.summary_until_id
            record.created_at = session.created_at
            record.last_active = session.last_active

            # Check the metadata version before touching the message projection.
            await database_session.flush()
            await database_session.execute(delete(Conversation).where(Conversation.session_id == session.session_id))
            database_session.add_all(
                [
                    Conversation(
                        user_id=user_id,
                        session_id=session.session_id,
                        role=message.role,
                        content=self._clean_text(message.content),
                        character_card=session.character_card,
                        style=session.style,
                        token_count=message.token_count,
                        attachments_json=json.dumps(
                            [attachment_to_dict(attachment) for attachment in message.attachments],
                            ensure_ascii=False,
                        ),
                        created_at=message.timestamp,
                    )
                    for message in session.messages
                ]
            )
            await database_session.flush()
        except SessionConflictError:
            raise
        except (IntegrityError, StaleDataError) as exc:
            raise SessionConflictError(session.session_id, session.revision) from exc

        return next_revision

    async def delete_session(self, session_id: str) -> None:
        async with self._session_factory() as database_session:
            async with database_session.begin():
                await self.delete_session_in_transaction(database_session, session_id)

    async def delete_session_in_transaction(self, database_session: AsyncSession, session_id: str) -> None:
        """Stage a reset in the same transaction as its Run completion."""

        await database_session.execute(delete(Conversation).where(Conversation.session_id == session_id))
        await database_session.execute(delete(ChatSessionRecord).where(ChatSessionRecord.session_id == session_id))

    async def health_check(self) -> bool:
        try:
            async with self._session_factory() as database_session:
                await database_session.execute(select(1))
            return True
        except Exception as exc:
            logger.warning("会话存储健康检查失败 (%s)", type(exc).__name__)
            return False

    async def stats(self) -> dict[str, Any]:
        async with self._session_factory() as database_session:
            session_count = await database_session.scalar(select(func.count()).select_from(ChatSessionRecord))
            message_count = await database_session.scalar(select(func.count()).select_from(Conversation))
        return {
            "backend": self.location.backend,
            "sessions": int(session_count or 0),
            "messages": int(message_count or 0),
        }


class SQLiteSessionStore(SQLAlchemySessionStore):
    """Backward-compatible constructor for callers passing a SQLite path."""

    def __init__(self, db_path: str = "data/chatbot.db", **kwargs: Any):
        location = resolve_database(db_path)
        if location.backend != "sqlite":
            raise ValueError("SQLiteSessionStore requires a SQLite path or URL")
        self.db_path = db_path
        super().__init__(db_path, **kwargs)


class PostgreSQLSessionStore(SQLAlchemySessionStore):
    def __init__(self, database_url: str, **kwargs: Any):
        location = resolve_database(database_url)
        if location.backend != "postgresql":
            raise ValueError("PostgreSQLSessionStore requires a PostgreSQL URL")
        super().__init__(database_url, **kwargs)


def create_session_store(config: Any) -> SQLAlchemySessionStore:
    database = config.database_target
    kwargs = {
        "pool_size": config.pool_size,
        "max_overflow": config.max_overflow,
        "pool_timeout": config.pool_timeout,
    }
    if resolve_database(database).backend == "postgresql":
        return PostgreSQLSessionStore(database, **kwargs)
    return SQLiteSessionStore(database, **kwargs)
