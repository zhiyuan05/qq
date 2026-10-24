import asyncio
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from core.engine import Engine, EngineNotReadyError
from core.session import Session
from database.session_store import SessionConflictError
from platforms.models import Platform, UnifiedMessage
from utils.config import AppConfig


def _config() -> AppConfig:
    return AppConfig(
        memory={"long_term": {"enabled": False}, "entity": {"auto_extract": False}},
        rate_limit={"enabled": False},
    )


def _message(content: str) -> UnifiedMessage:
    return UnifiedMessage(platform=Platform.CONSOLE, user_id="same-user", content=content)


@pytest.mark.asyncio
async def test_engine_restores_and_persists_sessions():
    restored = Session("console:same-user", "same-user", "console")
    restored.add_message("user", "previous")
    store = AsyncMock()
    store.load_sessions.return_value = [restored]
    llm = AsyncMock()
    llm.chat.return_value = "answer"
    engine = Engine(_config(), session_store=store, llm_client=llm)

    await engine.start()
    assert engine.is_ready is True
    assert engine.session_manager.get(restored.session_id) is restored
    assert engine.runtime_stats()["restored_sessions"] == 1

    result = await engine.process_message(_message("question"))
    assert "answer" in result
    store.save_session.assert_awaited_once_with(restored)

    await engine.process_message(_message("/new"))
    store.delete_session.assert_awaited_once_with(restored.session_id)
    await engine.stop()
    assert engine.is_ready is False


@pytest.mark.asyncio
async def test_persistence_write_failure_does_not_drop_reply():
    store = AsyncMock()
    store.load_session.return_value = None
    store.save_session.side_effect = RuntimeError("storage unavailable")
    llm = AsyncMock()
    llm.chat.return_value = "answer"
    engine = Engine(_config(), session_store=store, llm_client=llm)

    result = await engine.process_message(_message("question"))

    assert result.endswith("answer")
    assert engine.runtime_stats()["persistence_failures"] == 1


class _SlowLLM:
    def __init__(self):
        self.active = 0
        self.max_active = 0

    async def chat(self, messages, **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        return "ok"


@pytest.mark.asyncio
async def test_same_session_messages_are_serialized():
    llm = _SlowLLM()
    engine = Engine(_config(), llm_client=llm)

    results = await asyncio.gather(
        engine.process_message(_message("first")),
        engine.process_message(_message("second")),
    )

    assert all(result.endswith("ok") for result in results)
    assert llm.max_active == 1
    session = engine.session_manager.get("console:same-user")
    assert [message.role for message in session.messages] == ["user", "assistant", "user", "assistant"]
    assert session.messages[0].content == "first"
    assert session.messages[1].content.endswith("ok")
    assert session.messages[2].content == "second"
    assert session.messages[3].content.endswith("ok")


@pytest.mark.asyncio
async def test_injected_llm_is_instrumented_without_method_replacement():
    llm = AsyncMock()
    llm.chat.return_value = "answer"
    llm.last_call_succeeded = True
    original_chat = llm.chat
    engine = Engine(_config(), llm_client=llm)

    result = await engine.process_message(_message("question"))

    assert result.endswith("answer")
    assert llm.chat is original_chat
    assert engine.runtime_stats()["pending_llm_calls"] == 0
    assert engine.runtime_stats()["circuit_breaker"]["total_requests"] == 1
    assert engine.summarizer._llm is engine._get_llm()


@pytest.mark.asyncio
async def test_llm_failed_outcome_reaches_engine_circuit_breaker():
    llm = AsyncMock()
    llm.chat.return_value = "抱歉，我暂时无法回复，请稍后再试。"
    llm.last_call_succeeded = False
    engine = Engine(_config(), llm_client=llm)

    await engine.process_message(_message("question"))

    breaker = engine.runtime_stats()["circuit_breaker"]
    assert breaker["total_requests"] == 1
    assert breaker["total_failures"] == 1
    assert engine.runtime_stats()["pending_llm_calls"] == 0


@pytest.mark.asyncio
async def test_engine_closes_only_the_llm_client_it_owns():
    owned = AsyncMock()
    owned.last_call_succeeded = True
    with patch("core.engine.LLMClient.from_config", return_value=owned):
        engine = Engine(_config())
        engine._get_llm()
        await engine.stop()
    owned.aclose.assert_awaited_once()

    injected = AsyncMock()
    injected.last_call_succeeded = True
    engine = Engine(_config(), llm_client=injected)
    engine._get_llm()
    await engine.stop()
    injected.aclose.assert_not_awaited()


class _DistributedCoordinator:
    distributed = True

    def __init__(self):
        self.keys = []

    @asynccontextmanager
    async def lock(self, session_key):
        self.keys.append(session_key)
        yield

    def stats(self):
        return {"backend": "test", "distributed": True}


@pytest.mark.asyncio
async def test_distributed_coordinator_refreshes_session_after_lock_acquisition():
    latest = Session("console:same-user", "same-user", "console", revision=4)
    latest.add_message("user", "persisted question")
    latest.add_message("assistant", "persisted answer")
    store = AsyncMock()
    store.load_session.return_value = latest
    llm = AsyncMock()
    llm.chat.return_value = "new answer"
    coordinator = _DistributedCoordinator()
    engine = Engine(
        _config(),
        session_store=store,
        session_coordinator=coordinator,
        llm_client=llm,
    )

    await engine.process_message(_message("new question"))

    store.load_session.assert_awaited_once_with("console:same-user", 20)
    assert coordinator.keys == ["console:same-user"]
    current = engine.session_manager.get("console:same-user")
    assert current is latest
    assert [message.content for message in current.messages] == [
        "persisted question",
        "persisted answer",
        "new question",
        "new answer",
    ]


@pytest.mark.asyncio
async def test_local_persistence_hydrates_a_cache_miss_before_processing():
    latest = Session("console:same-user", "same-user", "console", revision=7)
    latest.add_message("user", "old question")
    latest.add_message("assistant", "old answer")
    store = AsyncMock()
    store.load_session.return_value = latest
    llm = AsyncMock()
    llm.chat.return_value = "new answer"
    engine = Engine(_config(), session_store=store, llm_client=llm)

    response = await engine.process_message(_message("new question"))

    assert response.endswith("new answer")
    store.load_session.assert_awaited_once_with("console:same-user", 20)
    store.save_session.assert_awaited_once_with(latest)
    assert [message.content for message in latest.messages] == [
        "old question",
        "old answer",
        "new question",
        "new answer",
    ]


@pytest.mark.asyncio
async def test_session_conflict_is_counted_and_reloads_committed_snapshot():
    committed = Session("console:same-user", "same-user", "console", revision=2)
    committed.add_message("assistant", "other instance won")
    store = AsyncMock()
    store.save_session.side_effect = SessionConflictError("console:same-user", 1, 2)
    store.load_session.return_value = committed
    llm = AsyncMock()
    llm.chat.return_value = "local answer"
    engine = Engine(_config(), session_store=store, llm_client=llm)
    engine.session_manager.replace(Session("console:same-user", "same-user", "console", revision=1))

    response = await engine.process_message(_message("question"))

    assert response.endswith("local answer")
    assert engine.session_manager.get("console:same-user") is committed
    assert engine.runtime_stats()["persistence_conflicts"] == 1
    assert engine.runtime_stats()["persistence_failures"] == 1


class _CancelledThenSuccessfulLLM:
    def __init__(self):
        self.calls = 0
        self.entered = asyncio.Event()

    async def chat(self, _messages, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            await asyncio.Event().wait()
        return "retry answer"


@pytest.mark.asyncio
async def test_cancelled_engine_operation_does_not_poison_router_retries():
    llm = _CancelledThenSuccessfulLLM()
    engine = Engine(_config(), llm_client=llm)
    message = _message("retry me")
    message.message_id = "stable-event-id"

    first = asyncio.create_task(engine.process_message(message))
    await llm.entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    response = await engine.process_message(message)

    assert response.endswith("retry answer")
    assert llm.calls == 2
    session = engine.session_manager.get(message.session_key)
    assert session is not None
    assert [item.role for item in session.messages] == ["user", "assistant"]
    assert session.messages[0].content == "retry me"
    assert session.messages[1].content.endswith("retry answer")


@pytest.mark.asyncio
async def test_engine_scopes_all_memory_access_by_platform_identity():
    entity_memory = AsyncMock()
    entity_memory.get_relevant_profile_text.return_value = ""
    long_term_memory = MagicMock()
    long_term_memory.search.return_value = []
    llm = AsyncMock()
    llm.chat.return_value = "answer"
    engine = Engine(
        _config(),
        entity_memory=entity_memory,
        long_term_memory=long_term_memory,
        llm_client=llm,
    )
    qq = UnifiedMessage(platform=Platform.QQ, user_id="42", content="qq question")
    telegram = UnifiedMessage(platform=Platform.TELEGRAM, user_id="42", content="telegram question")

    await engine.process_message(qq)
    await engine.process_message(telegram)
    await engine.drain_post_chat_effects()

    entity_memory.get_relevant_profile_text.assert_has_awaits(
        [call("qq:42", "qq question"), call("telegram:42", "telegram question")]
    )
    assert [item.kwargs["user_id"] for item in long_term_memory.search.call_args_list] == [
        "qq:42",
        "telegram:42",
    ]
    assert [item.kwargs["metadata"]["user_id"] for item in long_term_memory.store.call_args_list] == [
        "qq:42",
        "telegram:42",
    ]


@pytest.mark.asyncio
async def test_disabled_entity_memory_is_not_read_or_extracted():
    config = _config()
    config.memory.entity.enabled = False
    config.memory.entity.auto_extract = True
    entity_memory = AsyncMock()
    llm = AsyncMock()
    llm.chat.return_value = "answer"
    engine = Engine(config, entity_memory=entity_memory, llm_client=llm)

    await engine.process_message(_message("question"))

    entity_memory.get_relevant_profile_text.assert_not_awaited()
    entity_memory.store.assert_not_awaited()


class _SlowSynchronousMemory:
    def search(self, _query, *, user_id):
        time.sleep(0.2)
        return []

    def store(self, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_synchronous_vector_search_does_not_block_the_event_loop():
    llm = AsyncMock()
    llm.chat.return_value = "answer"
    engine = Engine(_config(), long_term_memory=_SlowSynchronousMemory(), llm_client=llm)

    started = time.perf_counter()
    processing = asyncio.create_task(engine.process_message(_message("question")))
    await asyncio.sleep(0.01)
    tick_elapsed = time.perf_counter() - started

    assert tick_elapsed < 0.1
    assert (await processing).endswith("answer")


@pytest.mark.asyncio
async def test_shared_mutation_commands_require_scoped_admin_or_group_admin():
    config = _config()
    config.security.admin_users = ["qq:owner"]
    engine = Engine(config, llm_client=AsyncMock())

    denied_card = await engine.process_message(
        UnifiedMessage(platform=Platform.QQ, user_id="member", content="/card create forbidden-card")
    )
    denied_group = await engine.process_message(
        UnifiedMessage(
            platform=Platform.QQ,
            user_id="member",
            group_id="group-1",
            content="/group style professional",
        )
    )
    engine.group_manager.add_admin("qq", "group-1", "member")
    allowed_group = await engine.process_message(
        UnifiedMessage(
            platform=Platform.QQ,
            user_id="member",
            group_id="group-1",
            content="/group style professional",
            message_id="second-command",
        )
    )
    scoped_admin = await engine.process_message(
        UnifiedMessage(platform=Platform.QQ, user_id="owner", content="/admin users")
    )
    same_id_other_platform = await engine.process_message(
        UnifiedMessage(platform=Platform.TELEGRAM, user_id="owner", content="/admin users")
    )

    assert denied_card == "权限不足，仅管理员可创建共享角色卡"
    assert denied_group == "权限不足"
    assert allowed_group == "群风格已设为: professional"
    assert scoped_admin.startswith("活跃会话数:")
    assert same_id_other_platform == "权限不足，仅管理员可用"


@pytest.mark.asyncio
async def test_engine_stop_drains_complete_non_llm_operation_and_rejects_new_work():
    engine = Engine(_config(), llm_client=AsyncMock())
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocking_operation(_message):
        entered.set()
        await release.wait()
        return "finished after persistence"

    engine._process_message_unlocked = blocking_operation
    processing = asyncio.create_task(engine.process_message(_message("first")))
    await entered.wait()

    stopping = asyncio.create_task(engine.stop(timeout=0.2))
    await asyncio.sleep(0)

    assert engine.runtime_stats()["active_work"] == 1
    assert engine.runtime_stats()["accepting_work"] is False
    assert not stopping.done()
    with pytest.raises(EngineNotReadyError, match="shutting down"):
        await engine.process_message(_message("late"))

    release.set()
    assert await processing == "finished after persistence"
    await stopping

    assert engine.runtime_stats()["active_work"] == 0
    assert engine.runtime_stats()["shutdown_drain_timed_out"] is False


@pytest.mark.asyncio
async def test_engine_stop_cancels_operation_after_bounded_timeout():
    engine = Engine(_config(), llm_client=AsyncMock())
    engine._shutdown_cancel_timeout = 0.1
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocking_operation(_message):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    engine._process_message_unlocked = blocking_operation
    processing = asyncio.create_task(engine.process_message(_message("first")))
    await entered.wait()

    await asyncio.wait_for(engine.stop(timeout=0.01), timeout=0.5)

    with pytest.raises(asyncio.CancelledError):
        await processing
    assert cancelled.is_set()
    assert engine.runtime_stats()["active_work"] == 0
    assert engine.runtime_stats()["shutdown_drain_timed_out"] is True


def test_entity_extraction_is_throttled():
    config = _config()
    config.memory.entity.auto_extract = True
    config.memory.entity.extract_interval = 3
    engine = Engine(config, llm_client=AsyncMock())
    session = Session("console:throttle", "throttle", "console")

    results = [engine._should_extract_entities(session) for _ in range(4)]

    assert results == [False, False, True, False]


def test_request_identity_is_stable_for_retries():
    message = _message("question")
    message.message_id = ""

    first = Engine._request_identity(message)
    second = Engine._request_identity(message)
    message.message_id = "m1"

    assert first == second
    assert Engine._request_identity(message) == "m1"
