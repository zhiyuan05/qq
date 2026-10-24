"""Versioned global/user persona profiles and trait retrieval."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_session_factory, run_migrations
from database.models import PersonaCursor, PersonaProfile, PersonaTrait, PersonaVersion
from utils.config import PersonaConfig
from utils.logger import setup_logger

logger = setup_logger("persona")

CATEGORIES = {
    "speech_style",
    "preference",
    "opinion",
    "relationship",
    "emotional_pattern",
    "boundary",
}
SCOPES = {"global", "user"}

_UNSAFE_PATTERNS = (
    re.compile(r"1[3-9]\d{9}"),
    re.compile(r"\d{17}[\dXx]"),
    re.compile(r"\d{16,19}"),
    re.compile(r"(忽略|无视|覆盖|修改|绕过).{0,6}(规则|提示词|系统|设定|指令)", re.IGNORECASE),
    re.compile(r"(ignore|disregard|override).{0,20}(rule|prompt|instruction)", re.IGNORECASE),
    re.compile(r"(密码|口令|银行卡|身份证|家庭住址|api[_ -]?key|secret|token)", re.IGNORECASE),
)


def is_unsafe_trait(*parts: str) -> bool:
    text = " ".join(part for part in parts if part)
    return any(pattern.search(text) for pattern in _UNSAFE_PATTERNS)


@dataclass(frozen=True)
class TraitCandidate:
    scope: str
    category: str
    trait_key: str
    value: str
    confidence: float
    evidence: str = ""
    scope_key: str = ""


@dataclass
class TraitView:
    id: int
    scope: str
    scope_key: str
    category: str
    trait_key: str
    value: str
    confidence: float
    evidence: str
    tier: str
    status: str
    source_sessions: tuple[str, ...] = field(default_factory=tuple)
    last_seen: datetime | None = None

    @property
    def prompt_line(self) -> str:
        return f"{self.category}: {self.value}"


def _parse_sessions(raw: str | None) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if item][:10]


def _terms(text: str) -> set[str]:
    lowered = text.lower()
    words = set(re.findall(r"[a-z0-9]+", lowered))
    chars = [char for char in lowered if "一" <= char <= "鿿"]
    words.update("".join(chars[index : index + 2]) for index in range(max(0, len(chars) - 1)))
    return words


class PersonaStore:
    """Dialect-neutral persona persistence with versioned rollback."""

    def __init__(
        self,
        database: str = "data/chatbot.db",
        *,
        config: PersonaConfig | None = None,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_timeout: float = 30.0,
    ):
        self.config = config or PersonaConfig()
        self._database = database
        self._session_factory = get_session_factory(
            database,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
        )
        self._schema_ready = False
        self._schema_lock = asyncio.Lock()
        self._scope_locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _scope_lock(self, scope: str, scope_key: str) -> asyncio.Lock:
        return self._scope_locks.setdefault((scope, scope_key), asyncio.Lock())

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if not self._schema_ready:
                await run_migrations(self._database)
                self._schema_ready = True

    @staticmethod
    def _view(row: PersonaTrait) -> TraitView:
        return TraitView(
            id=row.id,
            scope=row.scope,
            scope_key=row.scope_key,
            category=row.category,
            trait_key=row.trait_key,
            value=row.value,
            confidence=row.confidence or 0.0,
            evidence=row.evidence or "",
            tier=row.tier,
            status=row.status,
            source_sessions=tuple(_parse_sessions(row.source_sessions)),
            last_seen=row.last_seen,
        )

    @staticmethod
    def _scope_key(candidate: TraitCandidate, user_key: str) -> str:
        if candidate.scope == "global":
            return "global"
        return candidate.scope_key or user_key

    async def active_traits(self, scope: str, scope_key: str) -> list[TraitView]:
        await self._ensure_schema()
        async with self._session_factory() as database_session:
            rows = (
                await database_session.scalars(
                    select(PersonaTrait)
                    .where(
                        PersonaTrait.scope == scope,
                        PersonaTrait.scope_key == scope_key,
                        PersonaTrait.status == "active",
                    )
                    .order_by(PersonaTrait.confidence.desc())
                )
            ).all()
        return [self._view(row) for row in rows]

    async def show(self, scope: str, scope_key: str) -> list[TraitView]:
        await self._ensure_schema()
        async with self._session_factory() as database_session:
            rows = (
                await database_session.scalars(
                    select(PersonaTrait)
                    .where(PersonaTrait.scope == scope, PersonaTrait.scope_key == scope_key)
                    .order_by(PersonaTrait.confidence.desc())
                )
            ).all()
        return [self._view(row) for row in rows]

    async def injectable_traits(self, user_key: str, message: str) -> tuple[list[str], list[str]]:
        """Return (user_traits, global_traits) for prompt injection."""

        if not self.config.enabled:
            return [], []
        await self._ensure_schema()

        async with self._session_factory() as database_session:
            rows = (
                await database_session.scalars(
                    select(PersonaTrait)
                    .where(
                        PersonaTrait.status == "active",
                        or_(
                            and_(PersonaTrait.scope == "global", PersonaTrait.scope_key == "global"),
                            and_(PersonaTrait.scope == "user", PersonaTrait.scope_key == user_key),
                        ),
                    )
                    .order_by(PersonaTrait.confidence.desc())
                )
            ).all()
        global_traits = [self._view(row) for row in rows if row.scope == "global"]
        user_traits = [self._view(row) for row in rows if row.scope == "user"]
        terms = _terms(message)
        return (
            self._select(user_traits, terms, self.config.max_user_core, 6),
            self._select(global_traits, terms, self.config.max_global_core, 8),
        )

    @staticmethod
    def _select(traits: Iterable[TraitView], terms: set[str], core_limit: int, retrieved_limit: int) -> list[str]:
        traits = list(traits)
        core = sorted((trait for trait in traits if trait.tier == "core"), key=lambda t: -t.confidence)[:core_limit]
        remaining = [trait for trait in traits if trait not in core]

        def score(trait: TraitView) -> float:
            overlap = len(_terms(f"{trait.trait_key} {trait.value}") & terms)
            return overlap * 2 + trait.confidence

        retrieved = sorted(remaining, key=score, reverse=True)[:retrieved_limit]
        return [trait.prompt_line for trait in [*core, *retrieved]]

    async def _profile(self, database_session: AsyncSession, scope: str, scope_key: str) -> PersonaProfile:
        profile = await database_session.get(PersonaProfile, (scope, scope_key))
        if profile is None:
            profile = PersonaProfile(scope=scope, scope_key=scope_key, revision=0, profile_json="{}")
            database_session.add(profile)
            await database_session.flush()
        return profile

    async def _snapshot(self, database_session: AsyncSession, scope: str, scope_key: str, reason: str) -> int:
        profile = await self._profile(database_session, scope, scope_key)
        profile.revision += 1
        rows = (
            await database_session.scalars(
                select(PersonaTrait).where(
                    PersonaTrait.scope == scope,
                    PersonaTrait.scope_key == scope_key,
                    PersonaTrait.status.in_(("active", "candidate")),
                )
            )
        ).all()
        snapshot = [
            {
                "category": row.category,
                "trait_key": row.trait_key,
                "value": row.value,
                "confidence": row.confidence,
                "evidence": row.evidence,
                "source_sessions": _parse_sessions(row.source_sessions),
                "tier": row.tier,
                "status": row.status,
            }
            for row in rows
        ]
        database_session.add(
            PersonaVersion(
                scope=scope,
                scope_key=scope_key,
                revision=profile.revision,
                diff_json=json.dumps(snapshot, ensure_ascii=False),
                reason=reason,
            )
        )
        await database_session.flush()
        return profile.revision

    async def apply_candidates(
        self,
        candidates: Iterable[TraitCandidate],
        *,
        user_key: str,
        session_id: str = "",
        conversation_id: int | None = None,
        reason: str = "evolve",
    ) -> dict[str, int]:
        await self._ensure_schema()
        grouped: dict[tuple[str, str], list[TraitCandidate]] = {}
        for candidate in candidates:
            if candidate.scope not in SCOPES or candidate.category not in CATEGORIES:
                continue
            if not candidate.trait_key or not candidate.value:
                continue
            if len(candidate.value) > 100 or len(candidate.evidence) > 200:
                continue
            if is_unsafe_trait(candidate.trait_key, candidate.value, candidate.evidence):
                continue
            key = (candidate.scope, self._scope_key(candidate, user_key))
            grouped.setdefault(key, []).append(candidate)

        result = {"applied": 0, "candidates": 0, "sessions": len(grouped)}
        for (scope, scope_key), items in grouped.items():
            async with self._scope_lock(scope, scope_key):
                async with self._session_factory() as database_session:
                    async with database_session.begin():
                        await self._snapshot(database_session, scope, scope_key, reason)
                        for candidate in items:
                            applied, is_active = await self._apply_one(
                                database_session,
                                candidate,
                                scope_key,
                                session_id=session_id,
                                conversation_id=conversation_id,
                            )
                            if applied:
                                result["applied" if is_active else "candidates"] += 1
                        await self._enforce_limits(database_session, scope, scope_key)
        return result

    async def _apply_one(
        self,
        database_session: AsyncSession,
        candidate: TraitCandidate,
        scope_key: str,
        *,
        session_id: str,
        conversation_id: int | None,
    ) -> tuple[bool, bool]:
        scope = candidate.scope
        existing = (
            await database_session.scalars(
                select(PersonaTrait).where(
                    PersonaTrait.scope == scope,
                    PersonaTrait.scope_key == scope_key,
                    PersonaTrait.category == candidate.category,
                    PersonaTrait.trait_key == candidate.trait_key,
                )
            )
        ).all()
        exact = next((row for row in existing if row.value == candidate.value), None)
        now = datetime.now()
        sessions = [session_id] if session_id else []

        if exact is not None:
            exact.confidence = min(0.99, 1 - (1 - exact.confidence) * (1 - candidate.confidence))
            exact.evidence = candidate.evidence or exact.evidence
            exact.last_seen = now
            merged_sessions = list(dict.fromkeys([*_parse_sessions(exact.source_sessions), *sessions]))[:10]
            exact.source_sessions = json.dumps(merged_sessions, ensure_ascii=False)
            if scope == "user" and exact.confidence >= self.config.min_user_confidence:
                exact.status = "active"
            elif (
                scope == "global"
                and self.config.auto_approve_global
                and exact.confidence >= self.config.min_global_confidence
                and len(merged_sessions) >= 2
            ):
                exact.status = "active"
            return True, exact.status == "active"

        old_active = [row for row in existing if row.status == "active"]
        replace = bool(old_active) and candidate.confidence >= max(row.confidence for row in old_active) + 0.15
        if replace:
            for row in old_active:
                row.status = "faded"

        status = "candidate"
        if scope == "user" and candidate.confidence >= self.config.min_user_confidence:
            status = "active"
        elif (
            scope == "global"
            and self.config.auto_approve_global
            and candidate.confidence >= self.config.min_global_confidence
            and len(sessions) >= 2
        ):
            status = "active"
        database_session.add(
            PersonaTrait(
                scope=scope,
                scope_key=scope_key,
                category=candidate.category,
                trait_key=candidate.trait_key,
                value=candidate.value,
                confidence=candidate.confidence,
                evidence=candidate.evidence,
                source_conversation_id=conversation_id,
                source_sessions=json.dumps(sessions, ensure_ascii=False),
                tier="retrieved",
                status=status if replace or not old_active else "candidate",
                last_seen=now,
            )
        )
        await database_session.flush()
        return True, status == "active"

    async def _enforce_limits(self, database_session: AsyncSession, scope: str, scope_key: str) -> None:
        active_limit = self.config.max_global_active if scope == "global" else self.config.max_user_active
        core_limit = self.config.max_global_core if scope == "global" else self.config.max_user_core

        active = (
            await database_session.scalars(
                select(PersonaTrait)
                .where(
                    PersonaTrait.scope == scope,
                    PersonaTrait.scope_key == scope_key,
                    PersonaTrait.status == "active",
                )
                .order_by(PersonaTrait.confidence.desc())
            )
        ).all()
        for row in active[active_limit:]:
            row.status = "candidate"

        core = sorted(
            (row for row in active if row.tier == "core" and row.status == "active"),
            key=lambda row: row.confidence,
            reverse=True,
        )
        for row in core[core_limit:]:
            row.tier = "retrieved"

        for row in active[:active_limit]:
            required_sessions = 5 if scope == "global" else 3
            threshold = self.config.core_confidence if scope == "global" else 0.8
            if row.confidence >= threshold and len(_parse_sessions(row.source_sessions)) >= required_sessions:
                row.tier = "core"

    async def add_admin_trait(
        self,
        category: str,
        trait_key: str,
        value: str,
        *,
        scope: str = "global",
        user_key: str = "",
        confidence: float = 0.95,
    ) -> bool:
        """Admin-authored trait, active immediately and versioned."""

        if category not in CATEGORIES or scope not in SCOPES:
            return False
        trait_key = trait_key.strip()[:60]
        value = value.strip()[:100]
        if not trait_key or not value or is_unsafe_trait(trait_key, value):
            return False
        scope_key = "global" if scope == "global" else (user_key or "global")
        await self._ensure_schema()
        async with self._scope_lock(scope, scope_key):
            async with self._session_factory() as database_session:
                async with database_session.begin():
                    await self._snapshot(database_session, scope, scope_key, "admin")
                    row = (
                        await database_session.scalars(
                            select(PersonaTrait).where(
                                PersonaTrait.scope == scope,
                                PersonaTrait.scope_key == scope_key,
                                PersonaTrait.category == category,
                                PersonaTrait.trait_key == trait_key,
                                PersonaTrait.value == value,
                            )
                        )
                    ).first()
                    if row is None:
                        database_session.add(
                            PersonaTrait(
                                scope=scope,
                                scope_key=scope_key,
                                category=category,
                                trait_key=trait_key,
                                value=value,
                                confidence=min(1.0, max(0.0, confidence)),
                                evidence="管理员指定",
                                source_sessions=json.dumps(["admin"], ensure_ascii=False),
                                tier="retrieved",
                                status="active",
                            )
                        )
                    else:
                        row.confidence = min(1.0, max(row.confidence, confidence))
                        row.status = "active"
                        row.last_seen = datetime.now()
                    await database_session.flush()
                    await self._enforce_limits(database_session, scope, scope_key)
        return True

    async def approve(self, scope: str, scope_key: str, trait_id: int) -> bool:
        await self._ensure_schema()
        async with self._session_factory() as database_session:
            async with database_session.begin():
                row = await database_session.get(PersonaTrait, trait_id)
                if row is None or row.scope != scope or row.scope_key != scope_key:
                    return False
                if row.status != "candidate":
                    return row.status == "active"
                if row.confidence < self.config.min_global_confidence:
                    return False
                if len(_parse_sessions(row.source_sessions)) < 2:
                    return False
                await self._snapshot(database_session, scope, scope_key, "approve")
                row.status = "active"
                await self._enforce_limits(database_session, scope, scope_key)
        return True

    async def forget(self, scope: str, scope_key: str, keyword: str) -> int:
        await self._ensure_schema()
        async with self._session_factory() as database_session:
            async with database_session.begin():
                await self._snapshot(database_session, scope, scope_key, "forget")
                result = await database_session.execute(
                    delete(PersonaTrait).where(
                        PersonaTrait.scope == scope,
                        PersonaTrait.scope_key == scope_key,
                        (PersonaTrait.trait_key.contains(keyword)) | (PersonaTrait.value.contains(keyword)),
                    )
                )
                removed = int(result.rowcount or 0)
        return removed

    async def rollback(self, scope: str, scope_key: str, steps: int = 1) -> bool:
        await self._ensure_schema()
        async with self._session_factory() as database_session:
            async with database_session.begin():
                versions = (
                    await database_session.scalars(
                        select(PersonaVersion)
                        .where(PersonaVersion.scope == scope, PersonaVersion.scope_key == scope_key)
                        .order_by(PersonaVersion.revision.desc())
                        .limit(max(1, steps))
                    )
                ).all()
                if not versions:
                    return False
                target = versions[-1]
                snapshot = json.loads(target.diff_json or "[]")
                await database_session.execute(
                    delete(PersonaTrait).where(
                        PersonaTrait.scope == scope,
                        PersonaTrait.scope_key == scope_key,
                    )
                )
                for item in snapshot:
                    database_session.add(
                        PersonaTrait(
                            scope=scope,
                            scope_key=scope_key,
                            category=item["category"],
                            trait_key=item["trait_key"],
                            value=item["value"],
                            confidence=float(item.get("confidence", 0)),
                            evidence=item.get("evidence", ""),
                            source_sessions=json.dumps(item.get("source_sessions", []), ensure_ascii=False),
                            tier=item.get("tier", "retrieved"),
                            status=item.get("status", "candidate"),
                            last_seen=datetime.now(),
                        )
                    )
                await database_session.flush()
                await self._snapshot(database_session, scope, scope_key, "rollback")
        return True

    async def reset(self, scope: str, scope_key: str) -> None:
        await self._ensure_schema()
        async with self._session_factory() as database_session:
            async with database_session.begin():
                await self._snapshot(database_session, scope, scope_key, "reset")
                await database_session.execute(
                    delete(PersonaTrait).where(
                        PersonaTrait.scope == scope,
                        PersonaTrait.scope_key == scope_key,
                    )
                )

    async def decay(self) -> int:
        await self._ensure_schema()
        cutoff = datetime.now() - timedelta(days=7)
        changed = 0
        async with self._session_factory() as database_session:
            async with database_session.begin():
                rows = (
                    await database_session.scalars(
                        select(PersonaTrait).where(
                            PersonaTrait.status == "active",
                            PersonaTrait.last_seen < cutoff,
                        )
                    )
                ).all()
                for row in rows:
                    row.confidence = max(0.0, row.confidence - self.config.decay_per_week)
                    row.last_seen = datetime.now()
                    if row.confidence < 0.2:
                        row.status = "archived"
                    elif row.confidence < 0.35:
                        row.status = "faded"
                    changed += 1
        return changed

    async def get_cursor(self, session_id: str) -> int:
        await self._ensure_schema()
        async with self._session_factory() as database_session:
            row = await database_session.get(PersonaCursor, session_id)
        return int(row.last_conversation_id if row else 0)

    async def set_cursor(self, session_id: str, conversation_id: int) -> None:
        await self._ensure_schema()
        async with self._session_factory() as database_session:
            async with database_session.begin():
                dialect = database_session.bind.dialect.name
                values = {
                    "session_id": session_id,
                    "last_conversation_id": conversation_id,
                    "updated_at": datetime.now(),
                }
                if dialect == "sqlite":
                    statement = sqlite_insert(PersonaCursor).values(**values)
                elif dialect == "postgresql":
                    statement = postgresql_insert(PersonaCursor).values(**values)
                else:
                    raise RuntimeError(f"unsupported persona dialect: {dialect}")
                await database_session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[PersonaCursor.session_id],
                        set_={
                            "last_conversation_id": conversation_id,
                            "updated_at": values["updated_at"],
                        },
                    )
                )

    async def stats(self) -> dict[str, Any]:
        await self._ensure_schema()
        async with self._session_factory() as database_session:
            active = await database_session.scalar(
                select(func.count()).select_from(PersonaTrait).where(PersonaTrait.status == "active")
            )
            candidates = await database_session.scalar(
                select(func.count()).select_from(PersonaTrait).where(PersonaTrait.status == "candidate")
            )
        return {"active": int(active or 0), "candidates": int(candidates or 0)}
