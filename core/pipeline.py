from __future__ import annotations
"""Message pipeline with middleware chain."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from utils.logger import setup_logger

logger = setup_logger("pipeline")


@dataclass
class PipelineContext:
    user_id: str
    content: str
    platform: str = ""
    group_id: Optional[str] = None
    is_owner: bool = False
    character_name: Optional[str] = None
    session_id: Optional[str] = None
    response: Optional[str] = None
    memories: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    blocked: bool = False
    block_reason: str = ""
    timestamp: datetime = field(default_factory=datetime.now)


class Middleware(ABC):
    async def on_receive(self, ctx: PipelineContext) -> bool:
        return True

    async def on_llm(self, ctx: PipelineContext) -> bool:
        return True

    async def on_response(self, ctx: PipelineContext) -> bool:
        return True

    async def on_send(self, ctx: PipelineContext) -> bool:
        return True


class Pipeline:
    def __init__(self, middleware: Optional[list] = None):
        self._middleware: list[Middleware] = middleware or []

    def add(self, mw: Middleware):
        self._middleware.append(mw)

    async def process(self, ctx: PipelineContext) -> Optional[str]:
        for mw in self._middleware:
            if not await mw.on_receive(ctx):
                logger.debug(f"管道被 {mw.__class__.__name__} 在 receive 阶段阻断")
                return None

        for mw in self._middleware:
            if not await mw.on_llm(ctx):
                logger.debug(f"管道被 {mw.__class__.__name__} 在 llm 阶段阻断")
                return None

        for mw in self._middleware:
            if not await mw.on_response(ctx):
                logger.debug(f"管道被 {mw.__class__.__name__} 在 response 阶段阻断")
                return None

        for mw in self._middleware:
            if not await mw.on_send(ctx):
                logger.debug(f"管道被 {mw.__class__.__name__} 在 send 阶段阻断")
                return None

        return ctx.response
