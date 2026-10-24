"""Application-level shutdown ordering and failure isolation."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import main as main_module
from main import ChatBot


@pytest.mark.asyncio
async def test_shutdown_quiesces_ingress_and_continues_after_component_failure(monkeypatch):
    events: list[str] = []
    bot = ChatBot.__new__(ChatBot)
    bot._stopping = False
    bot._stop_event = asyncio.Event()
    bot.learning_scheduler = SimpleNamespace(stop=lambda: events.append("scheduler"))

    async def stop_web():
        events.append("web")

    async def stop_platforms():
        events.append("platforms")
        raise RuntimeError("platform cleanup failed")

    async def stop_engine(*, timeout):
        assert timeout == 15.0
        events.append("engine")

    async def stop_plugins():
        events.append("plugins")

    async def dispose_db(path):
        assert path == "test.db"
        events.append("database")

    bot._stop_web = stop_web
    bot.platform_manager = SimpleNamespace(stop_all=stop_platforms)
    bot.engine = SimpleNamespace(stop=stop_engine)
    bot.plugin_manager = SimpleNamespace(stop_all=stop_plugins)
    bot.config = SimpleNamespace(persistence=SimpleNamespace(database_target="test.db"))
    monkeypatch.setattr(main_module, "dispose_db", dispose_db)

    await bot.stop()
    await bot.stop()

    assert bot._stop_event.is_set()
    assert events == ["scheduler", "web", "platforms", "engine", "plugins", "database"]


@pytest.mark.asyncio
async def test_external_shutdown_signal_releases_application_waiter(monkeypatch):
    bot = ChatBot.__new__(ChatBot)
    bot._stopping = False
    bot._stop_event = asyncio.Event()
    bot.learning_scheduler = None
    bot._stop_web = AsyncMock()
    bot.platform_manager = SimpleNamespace(stop_all=AsyncMock())
    bot.engine = SimpleNamespace(stop=AsyncMock())
    bot.plugin_manager = SimpleNamespace(stop_all=AsyncMock())
    bot.config = SimpleNamespace(persistence=SimpleNamespace(database_target="test.db"))
    monkeypatch.setattr(main_module, "dispose_db", AsyncMock())

    waiter = asyncio.create_task(bot._stop_event.wait())
    await bot.stop()

    assert await asyncio.wait_for(waiter, timeout=0.1) is True


@pytest.mark.asyncio
async def test_cancelled_stop_waiter_does_not_cancel_shared_shutdown(monkeypatch):
    events: list[str] = []
    web_started = asyncio.Event()
    release_web = asyncio.Event()
    bot = ChatBot.__new__(ChatBot)
    bot._stopping = False
    bot._shutdown_task = None
    bot._stop_event = asyncio.Event()
    bot._web_server = None
    bot.learning_scheduler = None

    async def quiesce_platforms():
        events.append("quiesce")

    async def stop_web():
        events.append("web")
        web_started.set()
        await release_web.wait()

    async def stop_platforms():
        events.append("platforms")

    async def stop_engine(*, timeout):
        assert timeout == 15.0
        events.append("engine")

    async def stop_plugins():
        events.append("plugins")

    async def dispose_db(_target):
        events.append("database")

    bot._stop_web = stop_web
    bot.platform_manager = SimpleNamespace(quiesce_all=quiesce_platforms, stop_all=stop_platforms)
    bot.engine = SimpleNamespace(stop=stop_engine)
    bot.plugin_manager = SimpleNamespace(stop_all=stop_plugins)
    bot.config = SimpleNamespace(persistence=SimpleNamespace(database_target="test.db"))
    monkeypatch.setattr(main_module, "dispose_db", dispose_db)

    first_waiter = asyncio.create_task(bot.stop())
    await web_started.wait()
    first_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_waiter

    assert bot._shutdown_task is not None
    assert not bot._shutdown_task.done()
    release_web.set()
    await bot.stop()

    assert events == ["quiesce", "web", "platforms", "engine", "plugins", "database"]


@pytest.mark.asyncio
async def test_component_timeout_cancels_stuck_cleanup_and_continues(monkeypatch):
    events: list[str] = []
    web_cancelled = asyncio.Event()
    bot = ChatBot.__new__(ChatBot)
    bot._stopping = False
    bot._shutdown_task = None
    bot._stop_event = asyncio.Event()
    bot._web_server = None
    bot._COMPONENT_SHUTDOWN_TIMEOUT = 0.01
    bot._COMPONENT_CANCEL_TIMEOUT = 0.1
    bot.learning_scheduler = None

    async def quiesce_platforms():
        events.append("quiesce")

    async def stop_web():
        events.append("web")
        try:
            await asyncio.Event().wait()
        finally:
            web_cancelled.set()

    async def record(name):
        events.append(name)

    bot._stop_web = stop_web
    bot.platform_manager = SimpleNamespace(
        quiesce_all=quiesce_platforms,
        stop_all=lambda: record("platforms"),
    )
    bot.engine = SimpleNamespace(stop=lambda **_kwargs: record("engine"))
    bot.plugin_manager = SimpleNamespace(stop_all=lambda: record("plugins"))
    bot.config = SimpleNamespace(persistence=SimpleNamespace(database_target="test.db"))

    async def dispose_db(_target):
        await record("database")

    monkeypatch.setattr(main_module, "dispose_db", dispose_db)

    await asyncio.wait_for(bot.stop(), timeout=0.5)

    assert web_cancelled.is_set()
    assert events == ["quiesce", "web", "platforms", "engine", "plugins", "database"]


@pytest.mark.asyncio
async def test_web_shutdown_forces_and_reaps_server_task_after_grace_period():
    cancelled = asyncio.Event()

    async def serve_forever():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    bot = ChatBot.__new__(ChatBot)
    bot._WEB_GRACEFUL_TIMEOUT = 0.01
    bot._WEB_FORCE_TIMEOUT = 0.1
    server = SimpleNamespace(should_exit=False, force_exit=False)
    bot._web_server = server
    bot._web_task = asyncio.create_task(serve_forever())
    await asyncio.sleep(0)

    await asyncio.wait_for(bot._stop_web(), timeout=0.5)

    assert cancelled.is_set()
    assert server.should_exit is True
    assert server.force_exit is True
    assert bot._web_server is None
    assert bot._web_task is None


@pytest.mark.asyncio
async def test_web_readiness_waits_for_actual_server_start():
    release = asyncio.Event()
    server = SimpleNamespace(started=False)

    async def serve():
        await asyncio.sleep(0.01)
        server.started = True
        await release.wait()

    bot = ChatBot.__new__(ChatBot)
    bot._WEB_START_TIMEOUT = 0.2
    bot._web_server = server
    bot._web_task = asyncio.create_task(serve())

    await bot._wait_for_web_ready()
    assert server.started is True

    release.set()
    await bot._web_task


@pytest.mark.asyncio
async def test_web_readiness_surfaces_early_server_exit():
    bot = ChatBot.__new__(ChatBot)
    bot._WEB_START_TIMEOUT = 0.2
    bot._web_server = SimpleNamespace(started=False)
    bot._web_task = asyncio.create_task(asyncio.sleep(0))

    with pytest.raises(RuntimeError, match="before becoming ready"):
        await bot._wait_for_web_ready()
