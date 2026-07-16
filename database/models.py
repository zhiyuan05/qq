"""SQLAlchemy 数据模型"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, Boolean, JSON
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
    created_at = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime, default=datetime.utcnow)


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    session_id = Column(String(100), nullable=False, index=True)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    character_card = Column(String(100), default="")
    style = Column(String(50), default="")
    token_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class EntityMemory(Base):
    __tablename__ = "entity_memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    entity_type = Column(String(50), nullable=False)
    key = Column(String(200), nullable=False)
    value = Column(Text, nullable=False)
    confidence = Column(Float, default=1.0)
    source = Column(String(20), default="auto")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LearningReport(Base):
    __tablename__ = "learning_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    character_card = Column(String(100), nullable=False)
    conversations_analyzed = Column(Integer, default=0)
    traits_learned = Column(JSON, default=dict)
    summary = Column(Text, default="")
    applied = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    level = Column(String(20), nullable=False)  # INFO, WARNING, CRITICAL
    title = Column(String(200), nullable=False)
    message = Column(Text, nullable=False)
    metadata_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow)


class Metric(Base):
    __tablename__ = "metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    metric_type = Column(String(50), nullable=False)  # msg_sent, msg_received, llm_call, etc.
    value = Column(Float, nullable=False)
    platform = Column(String(20))
    created_at = Column(DateTime, default=datetime.utcnow)
