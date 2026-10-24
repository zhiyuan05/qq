"""Application composition root and graceful lifecycle orchestration."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from core.engine import Engine
from database.db import dispose_db, init_db
from learning.scheduler import LearningScheduler
from media.store import MediaStore
from media.voice import VoiceService
from platforms.console import ConsolePlatform
from platforms.delivery import OutboundGate
from platforms.manager import PlatformManager
from platforms.qq import QQPlatform
from platforms.telegram import TelegramPlatform
from security.outbound_guard import OutboundGuard
from plugins.manager import PluginManager
from plugins.web_search import WebSearchPlugin
from security.alerting import AlertManager
from utils.config import load_config
from utils.delay import init_delay
from utils.logger import setup_logger
from utils.version import __version__

logger = setup_logger("main")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QQ + Telegram 多平台聊天机器人")
    parser.add_argument("--console", "-c", action="store_true", help="控制台模式（无需平台配置）")
    parser.add_argument("--version", "-v", action="version", version=f"聊天机器人 v{__version__}")
    return parser.parse_args()


class ChatBot:
    _COMPONENT_SHUTDOWN_TIMEOUT = 30.0
    _COMPONENT_CANCEL_TIMEOUT = 2.0
    _WEB_START_TIMEOUT = 10.0
    _WEB_GRACEFUL_TIMEOUT = 10.0
    _WEB_FORCE_TIMEOUT = 2.0

    def __init__(self, console_mode: bool = False):
        self.config = load_config()
        self.plugin_manager = PluginManager()
        self.plugin_manager.register(WebSearchPlugin(self.config.plugins.web_search.model_dump()))
        self.engine = Engine(self.config, plugin_manager=self.plugin_manager)
        self.platform_manager = PlatformManager(self.engine.process_message_plan)
        self.learning_scheduler: LearningScheduler | None = None
        self.console_mode = console_mode
        self._web_server = None
        self._web_task: asyncio.Task | None = None
        self._stopping = False
        self._shutdown_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._ensure_runtime_dirs()

        init_delay(
            min_seconds=self.config.delay.min_seconds,
            max_seconds=self.config.delay.max_seconds,
            chars_per_second=self.config.delay.chars_per_second,
            enabled=self.config.delay.enabled and not console_mode,
        )

        if self.config.learning.enabled:
            self.learning_scheduler = LearningScheduler(
                trigger_interval=self.config.learning.trigger_interval,
                trigger_schedule=self.config.learning.trigger_schedule,
            )

    def _ensure_runtime_dirs(self) -> None:
        """Create writable paths that may be hidden behind the mounted data volume."""

        paths = [
            self.config.media.storage_dir,
            self.config.media.outbound_dir,
            self.config.voice.tts_dir,
            self.config.memory.long_term.path,
            str(Path(self.config.logging.file).parent),
            "data/learning",
            "data/logs",
            "data/tmp",
            "data/hf",
            "data/whisper",
        ]
        temp_dir = os.getenv("TMPDIR")
        if temp_dir:
            paths.append(temp_dir)
        for value in paths:
            try:
                Path(value).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning("无法创建运行目录 %s (%s)", value, type(exc).__name__)

    @property
    def platforms(self):
        """Backward-compatible read-only view of registered adapters."""

        return self.platform_manager.platforms

    def _register_platform(self, platform):
        self.platform_manager.add(platform)
        self.engine.register_platform(platform)
        return platform

    def _configure_platforms(self) -> None:
        if self.console_mode:
            self._register_platform(ConsolePlatform())
            return

        if self.config.platforms.qq.enabled:
            media_store = self.engine.media_store or (
                MediaStore(self.config.media) if self.config.media.enabled else None
            )
            voice_service = (
                VoiceService(self.config.voice, self.config.media) if self.config.voice.enabled else None
            )
            secrets = [
                self.config.llm.api_key,
                self.config.platforms.telegram.token,
                self.config.platforms.qq.access_token,
                self.config.web.auth_token,
            ]
            from web.app import get_metrics

            metrics = get_metrics()
            self._register_platform(
                QQPlatform(
                    self.config.platforms.qq.model_dump(),
                    media_store=media_store,
                    sticker_library=self.engine.sticker_library,
                    outbound_guard=(
                        OutboundGuard(
                            [secret for secret in secrets if secret],
                            on_block=lambda: metrics.increment("outbound_blocked"),
                        )
                        if self.config.delivery.audit_enabled
                        else None
                    ),
                    delivery_gate=OutboundGate(self.config.delivery, self.config.voice),
                    voice_service=voice_service,
                    metrics=metrics,
                )
            )
        if self.config.platforms.telegram.enabled:
            self._register_platform(TelegramPlatform(self.config.platforms.telegram.model_dump()))

    def _database_options(self) -> dict:
        persistence = self.config.persistence
        return {
            "pool_size": persistence.pool_size,
            "max_overflow": persistence.max_overflow,
            "pool_timeout": persistence.pool_timeout,
        }

    async def start(self) -> None:
        if self._stopping:
            raise RuntimeError("ChatBot instances cannot be restarted after shutdown")
        logger.info("Starting %s...", self.config.bot.name)
        try:
            await init_db(self.config.persistence.database_target, **self._database_options())
            await self.engine.start()
            await self.plugin_manager.start_all()
            self._configure_platforms()

            results = await self.platform_manager.start_all()
            for name, error in results.items():
                if error is None:
                    logger.info("Platform %s started", name)

            if self.console_mode:
                console = self.platform_manager.get("console")
                while console is not None and getattr(console, "_running", False):
                    await asyncio.sleep(0.5)
                return

            telegram = self.platform_manager.get("telegram")
            if isinstance(telegram, TelegramPlatform) and telegram.is_running:
                alert_manager = getattr(self.engine, "_alert_manager", None)
                if alert_manager is None:
                    alert_manager = AlertManager()
                    self.engine._alert_manager = alert_manager
                alert_manager.on_alert(lambda alert: telegram.send_alert(f"{alert['title']}: {alert['message']}"))

            await self._start_web()
            if self.learning_scheduler:
                self.learning_scheduler.set_learn_callback(self.engine.run_learning_all)
                self.engine._on_conversation_end = self.learning_scheduler.on_conversation_end
                self.learning_scheduler.start()

            logger.info("%s is ready", self.config.bot.name)
            def request_stop() -> None:
                logger.info("Stop signal received")
                self._stop_event.set()

            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, request_stop)
                except NotImplementedError:
                    pass

            await self._stop_event.wait()
        finally:
            await self.stop()

    async def _start_web(self) -> None:
        if not self.config.web.enabled:
            return
        try:
            import uvicorn

            from web.app import app as web_app
            from web.app import set_engine

            set_engine(self.engine)
            web_config = uvicorn.Config(
                web_app,
                host=self.config.web.host,
                port=self.config.web.port,
                log_level="warning",
            )
            self._web_server = uvicorn.Server(web_config)
            self._web_task = asyncio.create_task(self._web_server.serve(), name="web-server")
            await self._wait_for_web_ready()
            logger.info("Web administration is listening on http://localhost:%s", self.config.web.port)
        except asyncio.CancelledError:
            await self._abort_web_start()
            raise
        except Exception as exc:
            await self._abort_web_start()
            logger.error("Web administration failed to start (%s)", type(exc).__name__)

    async def _wait_for_web_ready(self) -> None:
        server = self._web_server
        task = self._web_task
        if server is None or task is None:
            raise RuntimeError("web server task was not created")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._WEB_START_TIMEOUT
        while not server.started:
            if task.done():
                if task.cancelled():
                    raise RuntimeError("web server was cancelled during startup")
                error = task.exception()
                if error is not None:
                    raise error
                raise RuntimeError("web server exited before becoming ready")
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("web server did not become ready before timeout")
            await asyncio.sleep(min(0.01, remaining))

    async def _abort_web_start(self) -> None:
        server = self._web_server
        task = self._web_task
        if server is not None:
            server.should_exit = True
            server.force_exit = True
        if task is not None and not task.done():
            task.cancel()
            done, pending = await asyncio.wait({task}, timeout=self._WEB_FORCE_TIMEOUT)
            if done:
                await asyncio.gather(*done, return_exceptions=True)
            if pending:
                task.add_done_callback(self._observe_detached_task)
        self._web_task = None
        self._web_server = None

    async def _stop_web(self) -> None:
        server = self._web_server
        task = self._web_task
        if server is not None:
            server.should_exit = True
        if task is None:
            self._web_server = None
            return

        done, pending = await asyncio.wait({task}, timeout=self._WEB_GRACEFUL_TIMEOUT)
        if pending:
            logger.warning("Web server did not drain before timeout; forcing shutdown")
            if server is not None:
                server.force_exit = True
            task.cancel()
            done, pending = await asyncio.wait(pending, timeout=self._WEB_FORCE_TIMEOUT)
        if done:
            await asyncio.gather(*done, return_exceptions=True)
        if pending:
            logger.error("Web server task resisted cancellation and was detached")
            for pending_task in pending:
                pending_task.add_done_callback(self._observe_detached_task)
        self._web_task = None
        self._web_server = None

    @staticmethod
    def _observe_detached_task(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        with contextlib.suppress(asyncio.CancelledError):
            error = task.exception()
            if error is not None:
                logger.error("Detached shutdown task failed (%s)", type(error).__name__)

    async def _run_cleanup(
        self,
        name: str,
        cleanup: Callable[[], Awaitable[Any]],
        *,
        timeout: float | None = None,
    ) -> bool:
        """Run one cleanup with bounded graceful and cancellation phases."""

        try:
            task = asyncio.create_task(cleanup(), name=f"shutdown:{name}")
        except Exception as exc:
            logger.error("%s failed to begin shutdown (%s)", name, type(exc).__name__)
            return False

        done, pending = await asyncio.wait(
            {task},
            timeout=self._COMPONENT_SHUTDOWN_TIMEOUT if timeout is None else timeout,
        )
        if pending:
            logger.warning("%s shutdown timed out; cancelling it", name)
            task.cancel()
            done, pending = await asyncio.wait(pending, timeout=self._COMPONENT_CANCEL_TIMEOUT)
        if pending:
            logger.error("%s shutdown resisted cancellation and was detached", name)
            task.add_done_callback(self._observe_detached_task)
            return False
        if task.cancelled():
            logger.warning("%s shutdown was cancelled", name)
            return False
        error = task.exception()
        if error is not None:
            logger.error("%s failed during shutdown (%s)", name, type(error).__name__)
            return False
        return True

    async def stop(self) -> None:
        """Idempotently await a cancellation-safe, shared shutdown operation."""

        shutdown_task = getattr(self, "_shutdown_task", None)
        if shutdown_task is None:
            self._stopping = True
            shutdown_task = asyncio.create_task(self._shutdown(), name="chatbot-shutdown")
            self._shutdown_task = shutdown_task
        await asyncio.shield(shutdown_task)

    async def _shutdown(self) -> None:
        self._stop_event.set()
        logger.info("Stopping...")

        # Phase 1: quiesce every ingress source before waiting on any one source.
        if self.learning_scheduler:
            try:
                self.learning_scheduler.stop()
            except Exception as exc:
                logger.error("Learning scheduler failed to quiesce (%s)", type(exc).__name__)
        web_server = getattr(self, "_web_server", None)
        if web_server is not None:
            web_server.should_exit = True
        quiesce_platforms = getattr(self.platform_manager, "quiesce_all", None)
        if quiesce_platforms is not None:
            await self._run_cleanup(
                "platform ingress",
                quiesce_platforms,
                timeout=min(5.0, self._COMPONENT_SHUTDOWN_TIMEOUT),
            )

        # Phase 2: drain accepted work before closing the engine and dependencies.
        if self.learning_scheduler and hasattr(self.learning_scheduler, "drain"):
            await self._run_cleanup("learning scheduler", self.learning_scheduler.drain)
        for name, cleanup in (
            ("web", self._stop_web),
            ("platforms", self.platform_manager.stop_all),
            ("engine", lambda: self.engine.stop(timeout=15.0)),
            ("plugins", self.plugin_manager.stop_all),
            ("database", lambda: dispose_db(self.config.persistence.database_target)),
        ):
            await self._run_cleanup(name, cleanup)

        with contextlib.suppress(Exception):
            from web.app import set_engine

            set_engine(None)
        logger.info("Stopped")


def main() -> None:
    args = _parse_args()
    bot = ChatBot(console_mode=args.console)
    asyncio.run(bot.start())


if __name__ == "__main__":
    main()
