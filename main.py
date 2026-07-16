"""聊天机器人主入口"""
import asyncio
import signal
import sys

from utils.config import load_config
from utils.logger import setup_logger
from utils.delay import init_delay
from core.engine import Engine
from platforms.qq import QQPlatform
from platforms.console import ConsolePlatform
from learning.scheduler import LearningScheduler
from learning.analyzer import LearningAnalyzer
from learning.evolution import EvolutionManager
from plugins.manager import PluginManager
from plugins.web_search import WebSearchPlugin
from database.db import init_db

logger = setup_logger("main")


class ChatBot:
    def __init__(self, console_mode: bool = False):
        self.config = load_config()
        self.engine = Engine(self.config)
        self.platforms = []
        self.learning_scheduler = None
        self.plugin_manager = PluginManager()
        self.console_mode = console_mode

        init_delay(enabled=not console_mode)
        self.plugin_manager.register(WebSearchPlugin())

        if self.config.learning.enabled:
            self.learning_scheduler = LearningScheduler(
                trigger_interval=self.config.learning.trigger_interval,
                trigger_schedule=self.config.learning.trigger_schedule,
            )

    async def start(self):
        logger.info(f"启动 {self.config.bot.name}...")

        await init_db()
        await self.plugin_manager.start_all()

        if self.console_mode:
            console = ConsolePlatform()
            console.on_message(self.engine.process_message)
            await console.start()
            self.platforms.append(console)
            logger.info("控制台模式已启动")
            while console._running:
                await asyncio.sleep(0.5)
            await self.stop()
            return

        # QQ 平台
        if self.config.platforms.qq.enabled:
            try:
                qq = QQPlatform(self.config.platforms.qq.model_dump())
                qq.on_message(self.engine.process_message)
                await qq.start()
                self.platforms.append(qq)
                self.engine._platforms.append(qq)
                logger.info("QQ平台已启动")
            except Exception as e:
                logger.error(f"QQ平台启动失败: {e}")

        # Telegram 平台
        tg_cfg = self.config.platforms.telegram
        if tg_cfg.enabled and tg_cfg.token:
            try:
                from platforms.telegram import TelegramPlatform
                telegram = TelegramPlatform({
                    "token": tg_cfg.token,
                    "owner_id": tg_cfg.owner_id,
                    "allowed_users": tg_cfg.allowed_users,
                    "group_mode": tg_cfg.group_mode,
                })
                telegram.on_message(self.engine.process_message)
                await telegram.start()
                self.platforms.append(telegram)
                self.engine._platforms.append(telegram)
                logger.info("Telegram平台已启动")
            except Exception as e:
                logger.error(f"Telegram平台启动失败: {e}")

        # Web管理面板
        try:
            import uvicorn
            from web.app import app as web_app, set_engine
            set_engine(self.engine)

            web_config = uvicorn.Config(web_app, host=self.config.web.host, port=self.config.web.port, log_level="warning")
            web_server = uvicorn.Server(web_config)
            asyncio.create_task(web_server.serve())
            logger.info(f"Web管理面板已启动: http://localhost:{self.config.web.port}")
        except Exception as e:
            logger.error(f"Web面板启动失败: {e}")

        if self.learning_scheduler:
            self.learning_scheduler.set_learn_callback(self.engine.run_learning_all)
            self.engine._on_conversation_end = self.learning_scheduler.on_conversation_end
            self.learning_scheduler.start()

        logger.info(f"{self.config.bot.name} 已就绪")

        stop_event = asyncio.Event()

        def _signal_handler():
            logger.info("收到停止信号...")
            stop_event.set()

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass

        await stop_event.wait()
        await self.stop()

    async def stop(self):
        logger.info("正在停止...")
        # 等待进行中的 LLM 调用
        await self.engine.wait_for_pending_calls(timeout=15.0)
        await self.plugin_manager.stop_all()
        for platform in self.platforms:
            await platform.stop()
        if self.learning_scheduler:
            self.learning_scheduler.stop()
        logger.info("已停止")


def main():
    console_mode = "--console" in sys.argv or "-c" in sys.argv
    bot = ChatBot(console_mode=console_mode)
    asyncio.run(bot.start())


if __name__ == "__main__":
    main()
