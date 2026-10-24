from __future__ import annotations
"""Message pipeline with middleware chain."""
from abc import ABC
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
    has_attachments: bool = False
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
    """4-stage middleware chain: on_receive → on_llm → on_response → on_send.

    Call `run_stage()` for incremental execution (Engine's use case),
    or `process()` for a single-shot call with all stages.
    """

    def __init__(self, middleware: Optional[list] = None):
        self._middleware: list[Middleware] = middleware or []

    def add(self, mw: Middleware):
        self._middleware.append(mw)

    async def run_stage(self, stage: str, ctx: PipelineContext) -> bool:
        """Run a single stage across all middleware in order.

        Returns False if any middleware blocks the chain (sets ctx.blocked).
        """
        for mw in self._middleware:
            method = getattr(mw, stage, None)
            if method is not None and not await method(ctx):
                return False
        return True

    async def process(self, ctx: PipelineContext) -> Optional[str]:
        if not await self.run_stage("on_receive", ctx):
            logger.debug(f"pipeline blocked at on_receive: {ctx.block_reason}")
            return None
        if not await self.run_stage("on_llm", ctx):
            logger.debug(f"pipeline blocked at on_llm: {ctx.block_reason}")
            return None
        if not await self.run_stage("on_response", ctx):
            logger.debug(f"pipeline blocked at on_response: {ctx.block_reason}")
            return None
        if not await self.run_stage("on_send", ctx):
            logger.debug(f"pipeline blocked at on_send: {ctx.block_reason}")
            return None
        return ctx.response
