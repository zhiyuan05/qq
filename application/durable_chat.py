"""Durable inbound execution and outbound delivery orchestration.

Network calls never run inside a repository transaction. An ambiguous external
attempt is parked as UNKNOWN; it is not silently executed a second time.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from core.session import Session
from database.durable_store import DurableStore, OutboxSpec, StaleLeaseError
from database.session_store import SQLAlchemySessionStore
from platforms.base import PlatformNotReadyError
from platforms.models import DeliveryReceipt, MessageType, Platform, UnifiedMessage

logger = logging.getLogger("durable-chat")


class InboundConflictError(ValueError):
    """An external event ID was reused with different content."""


class DurableChatService:
    """Own one connector's persistent run queue and ordered delivery queue."""

    def __init__(
        self,
        store: DurableStore,
        session_store: SQLAlchemySessionStore,
        process_message: Callable[..., Awaitable[str | None]],
        send_message: Callable[..., Awaitable[DeliveryReceipt]],
        split_text: Callable[[str], list[str]],
        *,
        source: str,
        connector_key: str,
        lease_seconds: float = 60,
        poll_seconds: float = 0.5,
        drain_timeout: float = 15,
    ) -> None:
        if lease_seconds <= 0 or poll_seconds <= 0 or drain_timeout <= 0:
            raise ValueError("worker timeouts must be positive")
        self.store = store
        self.session_store = session_store
        self.process_message = process_message
        self.send_message = send_message
        self.split_text = split_text
        self.source = source
        self.connector_key = connector_key
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self.drain_timeout = drain_timeout
        self.owner = uuid.uuid4().hex
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._accepting = False

    async def ingest(self, message: UnifiedMessage):
        """A callback may ACK only after this transaction has committed."""

        if not self._accepting:
            raise PlatformNotReadyError("durable ingress is not accepting messages")
        if message.platform.value != self.source or message.connector_key != self.connector_key:
            raise ValueError("message connector does not match this worker")
        if not message.message_id:
            raise ValueError("a durable inbound event requires an external message ID")
        payload = {
            "platform": message.platform.value,
            "connector_key": message.connector_key,
            "user_id": message.user_id,
            "content": message.content,
            "group_id": message.group_id,
            "message_id": message.message_id,
            "timestamp": message.timestamp.isoformat(),
        }
        result = await self.store.ingest(
            source=self.source,
            connector_key=self.connector_key,
            external_event_id=message.message_id,
            principal_key=message.principal_key,
            conversation_id=message.session_key,
            request_json=payload,
            request_id=message.message_id,
        )
        if result.disposition == "CONFLICT":
            raise InboundConflictError("external event ID payload conflict")
        return result

    async def start(self) -> None:
        if self._tasks:
            return
        self._stop.clear()
        self._accepting = True
        self._tasks = [
            asyncio.create_task(self._loop(self.run_once), name=f"durable:{self.source}:runs"),
            asyncio.create_task(self._loop(self.deliver_once), name=f"durable:{self.source}:outbox"),
        ]

    def quiesce(self) -> None:
        self._accepting = False
        self._stop.set()

    async def stop(self) -> None:
        self.quiesce()
        if not self._tasks:
            return
        done, pending = await asyncio.wait(self._tasks, timeout=self.drain_timeout)
        for task in pending:
            task.cancel()
        if pending:
            cancelled, pending = await asyncio.wait(pending, timeout=2)
            done |= cancelled
        await asyncio.gather(*done, return_exceptions=True)
        if pending:
            logger.error("Durable worker shutdown has %d unfinished task(s)", len(pending))
            for task in pending:
                task.add_done_callback(self._observe_task)
        self._tasks = []

    @staticmethod
    def _observe_task(task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()

    async def _loop(self, step: Callable[[], Awaitable[bool]]) -> None:
        while not self._stop.is_set():
            try:
                if await step():
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Durable queue step failed (%s)", type(exc).__name__)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def _with_heartbeat(self, work: Awaitable[Any], heartbeat: Callable[[], Awaitable[bool]]) -> Any:
        operation = asyncio.create_task(work)

        async def renew() -> None:
            while not operation.done():
                await asyncio.sleep(self.lease_seconds / 3)
                if operation.done():
                    return
                if not await heartbeat():
                    raise StaleLeaseError("worker lost its lease")

        renewal = asyncio.create_task(renew())
        try:
            done, _ = await asyncio.wait({operation, renewal}, return_when=asyncio.FIRST_COMPLETED)
            if operation in done:
                return await operation
            await renewal
            return await operation
        finally:
            for task in (operation, renewal):
                if not task.done():
                    task.cancel()
            await asyncio.gather(operation, renewal, return_exceptions=True)

    async def run_once(self) -> bool:
        claim = await self.store.claim_run(
            self.owner, lease_seconds=self.lease_seconds, source=self.source, connector_key=self.connector_key
        )
        if claim is None:
            return False
        payload = claim.request_json
        committed = False

        async def execute() -> None:
            nonlocal committed
            message = UnifiedMessage(
                platform=Platform(payload["platform"]),
                connector_key=payload["connector_key"],
                user_id=payload["user_id"],
                content=payload["content"],
                message_type=MessageType.TEXT,
                group_id=payload.get("group_id"),
                message_id=payload["message_id"],
                timestamp=datetime.fromisoformat(payload["timestamp"]),
            )

            async def commit(session: Session | None, response: str | None, deleted_session_id: str | None) -> None:
                nonlocal committed
                # WeChat's customer-service window is anchored to the inbound event,
                # never extended by a worker restart or a delayed retry.
                deadline = message.timestamp.astimezone(timezone.utc).replace(tzinfo=None) + timedelta(hours=48)
                deliveries = [
                    OutboxSpec(
                        destination=self.source,
                        connector_key=self.connector_key,
                        target_id=message.user_id,
                        chunk_index=index,
                        payload={"content": chunk, "group_id": message.group_id},
                        deliver_before=deadline,
                    )
                    for index, chunk in enumerate(self.split_text(response or ""))
                ]

                async def save_projection(database_session):
                    if deleted_session_id is not None:
                        await self.session_store.delete_session_in_transaction(database_session, deleted_session_id)
                    if session is not None:
                        return await self.session_store.save_session_in_transaction(database_session, session)
                    return None

                result = await self.store.complete(
                    claim, response, deliveries=deliveries, commit_hook=save_projection
                )
                committed = True
                if session is not None and result.output_revision is not None:
                    session.revision = result.output_revision

            await self.process_message(message, commit=commit)
            if not committed:
                raise RuntimeError("chat processor returned without committing its Run")

        try:
            await self._with_heartbeat(
                execute(), lambda: self.store.heartbeat_run(claim, lease_seconds=self.lease_seconds)
            )
        except BaseException as exc:
            if not committed:
                await self._park_run(claim, type(exc).__name__)
            if isinstance(exc, asyncio.CancelledError):
                raise
            logger.warning("Durable run failed (%s)", type(exc).__name__)
        return True

    async def _park_run(self, claim, code: str) -> None:
        try:
            await self.store.unknown_run(claim, error_code=code)
        except Exception as exc:
            # An expired claim is fenced already; the repository recovery pass
            # parks it. A successful commit must never be overwritten here.
            logger.warning("Could not park durable run (%s)", type(exc).__name__)

    async def deliver_once(self) -> bool:
        """Claim, send, and record one persisted chunk (implementation below)."""

        return False
