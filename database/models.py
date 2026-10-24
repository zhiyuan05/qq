from __future__ import annotations

"""SQLAlchemy 数据模型"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(20), nullable=False)
    platform_id = Column(String(200), nullable=False, unique=True)
    nickname = Column(String(100), default="")
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
    last_active = Column(DateTime, default=datetime.now)


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    session_id = Column(String(200), nullable=False, index=True)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    character_card = Column(String(100), default="")
    style = Column(String(50), default="")
    token_count = Column(Integer, default=0)
    attachments_json = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, default=datetime.now)


class ChatSessionRecord(Base):
    """可恢复的会话元数据；消息正文仍存放在 conversations 表。"""

    __tablename__ = "chat_sessions"

    session_id = Column(String(200), primary_key=True)
    user_id = Column(String(200), nullable=False, index=True)
    platform = Column(String(20), nullable=False, index=True)
    character_card = Column(String(100), default="default")
    style = Column(String(50), default="casual")
    summary = Column(Text, default="")
    summary_until_id = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.now)
    last_active = Column(DateTime, default=datetime.now, index=True)
    revision = Column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": revision, "version_id_generator": False}


class ConversationArchive(Base):
    """Append-only transcript used by summary and persona cursors.

    ``conversations`` is a replaceable snapshot, so its ids are not stable. This
    table never participates in the live prompt and only grows by unique turn.
    """

    __tablename__ = "conversation_archive"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "request_id",
            "turn_index",
            name="uq_archive_session_request_turn",
        ),
        Index("ix_archive_session_id_id", "session_id", "id"),
        Index("ix_archive_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(200), nullable=False)
    user_id = Column(String(200), nullable=False)
    request_id = Column(String(255), nullable=False)
    turn_index = Column(Integer, nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    attachments_json = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, default=datetime.now)


class PersonaProfile(Base):
    __tablename__ = "persona_profiles"

    scope = Column(String(20), primary_key=True)
    scope_key = Column(String(200), primary_key=True)
    revision = Column(Integer, nullable=False, default=0)
    profile_json = Column(Text, nullable=False, default="{}")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class PersonaTrait(Base):
    __tablename__ = "persona_traits"
    __table_args__ = (
        UniqueConstraint(
            "scope",
            "scope_key",
            "category",
            "trait_key",
            "value",
            name="uq_persona_trait_identity",
        ),
        Index("ix_persona_traits_active", "scope", "scope_key", "status", "confidence"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope = Column(String(20), nullable=False)
    scope_key = Column(String(200), nullable=False)
    category = Column(String(50), nullable=False)
    trait_key = Column(String(200), nullable=False)
    value = Column(Text, nullable=False)
    confidence = Column(Float, nullable=False, default=0.0)
    evidence = Column(Text, nullable=False, default="")
    source_conversation_id = Column(Integer, nullable=True)
    source_sessions = Column(Text, nullable=False, default="[]")
    tier = Column(String(20), nullable=False, default="retrieved")
    status = Column(String(20), nullable=False, default="candidate")
    created_at = Column(DateTime, default=datetime.now)
    last_seen = Column(DateTime, default=datetime.now)


class PersonaVersion(Base):
    __tablename__ = "persona_versions"
    __table_args__ = (UniqueConstraint("scope", "scope_key", "revision", name="uq_persona_version"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope = Column(String(20), nullable=False)
    scope_key = Column(String(200), nullable=False)
    revision = Column(Integer, nullable=False)
    diff_json = Column(Text, nullable=False, default="{}")
    reason = Column(String(200), nullable=False, default="evolve")
    created_at = Column(DateTime, default=datetime.now)


class PersonaCursor(Base):
    __tablename__ = "persona_cursors"

    session_id = Column(String(200), primary_key=True)
    last_conversation_id = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class LLMUsageEvent(Base):
    __tablename__ = "llm_usage_events"
    __table_args__ = (
        Index("ix_usage_session_time", "session_id", "occurred_at"),
        Index("ix_usage_time", "occurred_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_key = Column(String(64), nullable=False, unique=True)
    request_id = Column(String(255), nullable=False, default="")
    session_id = Column(String(200), nullable=False, default="")
    user_id = Column(String(200), nullable=False, default="")
    model = Column(String(200), nullable=False, default="")
    cache_miss_tokens = Column(Integer, nullable=False, default=0)
    cache_hit_tokens = Column(Integer, nullable=False, default=0)
    cache_write_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    cost_micros = Column(BigInteger, nullable=False, default=0)
    currency = Column(String(8), nullable=False, default="CNY")
    occurred_at = Column(DateTime, default=datetime.now)


class EntityMemory(Base):
    __tablename__ = "entity_memories"
    __table_args__ = (
        UniqueConstraint("user_id", "entity_type", "key", name="uq_entity_user_type_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(200), nullable=False, index=True)
    entity_type = Column(String(50), nullable=False)
    key = Column(String(200), nullable=False)
    value = Column(Text, nullable=False)
    confidence = Column(Float, default=1.0)
    source = Column(String(20), default="auto")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class LearningReport(Base):
    __tablename__ = "learning_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    character_card = Column(String(100), nullable=False)
    conversations_analyzed = Column(Integer, default=0)
    traits_learned = Column(JSON, default=dict)
    summary = Column(Text, default="")
    applied = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    level = Column(String(20), nullable=False)  # INFO, WARNING, CRITICAL
    title = Column(String(200), nullable=False)
    message = Column(Text, nullable=False)
    metadata_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now)


class Metric(Base):
    __tablename__ = "metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    metric_type = Column(String(50), nullable=False)  # msg_sent, msg_received, llm_call, etc.
    value = Column(Float, nullable=False)
    platform = Column(String(20))
    created_at = Column(DateTime, default=datetime.now)


class InboundEvent(Base):
    """A durably accepted transport event.

    ``external_event_id`` is scoped by the source and connector. ``payload_hash``
    prevents an idempotency key from being reused for different content.
    """

    __tablename__ = "inbound_events"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "connector_key",
            "external_event_id",
            name="uq_inbound_source_connector_external",
        ),
        CheckConstraint(
            "status IN ('RECEIVED', 'PROCESSING', 'COMPLETED', 'FAILED', 'IGNORED', 'UNKNOWN')",
            name="ck_inbound_events_status",
        ),
        Index("ix_inbound_events_queue", "status", "next_attempt_at", "received_at"),
        Index("ix_inbound_events_conversation", "conversation_id", "received_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    dedupe_key = Column(String(64), nullable=False, unique=True)
    source = Column(String(32), nullable=False)
    connector_key = Column(String(128), nullable=False)
    external_event_id = Column(String(255), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    principal_key = Column(String(255), nullable=False)
    conversation_id = Column(String(255), nullable=False)
    request_json = Column(JSON, nullable=False, default=dict)
    status = Column(String(20), nullable=False, default="RECEIVED")
    # Run owns the relationship. Avoiding a circular FK keeps SQLite migration
    # ordering and PostgreSQL metadata bootstrap straightforward.
    run_id = Column(String(36), nullable=False, unique=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=True)
    lease_owner = Column(String(128), nullable=True)
    lease_token = Column(BigInteger, nullable=False, default=0)
    lease_expires_at = Column(DateTime, nullable=True)
    error_code = Column(String(128), nullable=True)
    received_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class Run(Base):
    """One logical execution for exactly one accepted inbound event."""

    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("conversation_id", "turn_no", name="uq_runs_conversation_turn"),
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'RETRY_WAIT', 'SUCCEEDED', 'FAILED', "
            "'CANCELLED', 'UNKNOWN', 'SUPERSEDED')",
            name="ck_runs_status",
        ),
        Index("ix_runs_claim", "status", "next_attempt_at", "created_at"),
        Index("ix_runs_request_id", "request_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    inbound_event_id = Column(
        String(36),
        ForeignKey("inbound_events.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    conversation_id = Column(String(255), nullable=False)
    turn_no = Column(BigInteger, nullable=False)
    request_id = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default="QUEUED")
    attempt_count = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=True)
    lease_owner = Column(String(128), nullable=True)
    lease_token = Column(BigInteger, nullable=False, default=0)
    lease_expires_at = Column(DateTime, nullable=True)
    input_revision = Column(Integer, nullable=True)
    output_revision = Column(Integer, nullable=True)
    agent_version = Column(String(100), nullable=True)
    prompt_version = Column(String(100), nullable=True)
    provider = Column(String(100), nullable=True)
    model = Column(String(200), nullable=True)
    provider_request_id = Column(String(255), nullable=True)
    prompt_hash = Column(String(64), nullable=True)
    response_text = Column(Text, nullable=True)
    response_hash = Column(String(64), nullable=True)
    completion_hash = Column(String(64), nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    cost_micros = Column(BigInteger, nullable=True)
    cost_currency = Column(String(8), nullable=True)
    error_type = Column(String(100), nullable=True)
    error_code = Column(String(128), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    first_token_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class RunEvent(Base):
    """Append-only, ordered execution milestone."""

    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_run_events_run_sequence"),
        Index("ix_run_events_run_created", "run_id", "created_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id = Column(String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    sequence = Column(BigInteger, nullable=False)
    event_type = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class OutboxDelivery(Base):
    """A deterministic delivery command committed with a successful Run."""

    __tablename__ = "outbox_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "destination",
            "connector_key",
            "target_id",
            "chunk_index",
            name="uq_outbox_run_destination_chunk",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'SENDING', 'RETRY_WAIT', 'DELIVERED', "
            "'UNKNOWN', 'DEAD', 'CANCELLED', 'EXPIRED')",
            name="ck_outbox_deliveries_status",
        ),
        CheckConstraint("chunk_index >= 0", name="ck_outbox_chunk_index"),
        CheckConstraint("attempt_count >= 0", name="ck_outbox_attempt_count"),
        CheckConstraint("max_attempts > 0", name="ck_outbox_max_attempts"),
        Index("ix_outbox_deliveries_queue", "status", "available_at", "created_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id = Column(String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    destination = Column(String(32), nullable=False)
    connector_key = Column(String(128), nullable=False)
    target_id = Column(String(255), nullable=False)
    chunk_index = Column(Integer, nullable=False, default=0)
    payload = Column(JSON, nullable=False)
    payload_hash = Column(String(64), nullable=False)
    idempotency_key = Column(String(200), nullable=False, unique=True)
    status = Column(String(20), nullable=False, default="PENDING")
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    available_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    lease_owner = Column(String(128), nullable=True)
    lease_token = Column(BigInteger, nullable=False, default=0)
    lease_expires_at = Column(DateTime, nullable=True)
    remote_message_id = Column(String(255), nullable=True)
    accepted_at = Column(DateTime, nullable=True)
    deliver_before = Column(DateTime, nullable=True)
    last_error_type = Column(String(100), nullable=True)
    last_error_code = Column(String(128), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
