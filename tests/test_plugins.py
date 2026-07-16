"""插件系统测试"""
import pytest
from unittest.mock import AsyncMock, patch

from plugins.manager import PluginManager
from plugins.base import BasePlugin
from platforms.models import UnifiedMessage, Platform


class MockPlugin(BasePlugin):
    name = "mock_plugin"
    version = "1.0.0"
    description = "测试插件"

    def __init__(self):
        self.started = False
        self.stopped = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def on_message(self, message: UnifiedMessage):
        if "test" in message.content:
            return UnifiedMessage(
                platform=message.platform,
                user_id=message.user_id,
                content="[插件处理] " + message.content,
            )
        return None

    async def on_response(self, response: str, message: UnifiedMessage):
        return f"[插件增强] {response}" if response else None

    def get_commands(self):
        return {"/test": self._cmd_test}

    async def _cmd_test(self, args, user_id=""):
        return "插件命令执行"


class TestPluginManager:
    @pytest.mark.asyncio
    async def test_register_and_list(self):
        mgr = PluginManager()
        plugin = MockPlugin()
        mgr.register(plugin)
        assert "mock_plugin" in mgr.list_plugins()

    @pytest.mark.asyncio
    async def test_get_plugin(self):
        mgr = PluginManager()
        plugin = MockPlugin()
        mgr.register(plugin)
        assert mgr.get("mock_plugin") is plugin

    @pytest.mark.asyncio
    async def test_start_stop_all(self):
        mgr = PluginManager()
        plugin = MockPlugin()
        mgr.register(plugin)
        await mgr.start_all()
        assert plugin.started is True
        await mgr.stop_all()
        assert plugin.stopped is True

    @pytest.mark.asyncio
    async def test_process_message_modifies(self):
        mgr = PluginManager()
        plugin = MockPlugin()
        mgr.register(plugin)
        msg = UnifiedMessage(platform=Platform.CONSOLE, user_id="u1", content="test hello")
        result = await mgr.process_message(msg)
        assert result is not None
        assert "插件处理" in result.content

    @pytest.mark.asyncio
    async def test_process_message_passthrough(self):
        mgr = PluginManager()
        plugin = MockPlugin()
        mgr.register(plugin)
        msg = UnifiedMessage(platform=Platform.CONSOLE, user_id="u1", content="normal message")
        result = await mgr.process_message(msg)
        # 不匹配插件条件时返回原始消息（不会返回None）
        assert result is not None
        assert result.content == "normal message"

    @pytest.mark.asyncio
    async def test_process_response_enhances(self):
        mgr = PluginManager()
        plugin = MockPlugin()
        mgr.register(plugin)
        msg = UnifiedMessage(platform=Platform.CONSOLE, user_id="u1", content="hello")
        result = await mgr.process_response("原始回复", msg)
        assert "插件增强" in result

    def test_get_all_commands(self):
        mgr = PluginManager()
        plugin = MockPlugin()
        mgr.register(plugin)
        cmds = mgr.get_all_commands()
        assert "/test" in cmds
