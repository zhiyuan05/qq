"""插件管理器"""
from typing import Dict, List, Optional

from plugins.base import BasePlugin
from platforms.models import UnifiedMessage
from utils.logger import setup_logger

logger = setup_logger("plugins")


class PluginManager:
    def __init__(self):
        self._plugins: Dict[str, BasePlugin] = {}

    def register(self, plugin: BasePlugin):
        self._plugins[plugin.name] = plugin
        logger.info(f"注册插件: {plugin.name} v{plugin.version}")

    def get(self, name: str) -> Optional[BasePlugin]:
        return self._plugins.get(name)

    def list_plugins(self) -> List[str]:
        return list(self._plugins.keys())

    async def start_all(self):
        for plugin in self._plugins.values():
            try:
                await plugin.start()
            except Exception as e:
                logger.error(f"插件 {plugin.name} 启动失败: {e}")

    async def stop_all(self):
        for plugin in self._plugins.values():
            try:
                await plugin.stop()
            except Exception as e:
                logger.error(f"插件 {plugin.name} 停止失败: {e}")

    async def process_message(self, message: UnifiedMessage) -> Optional[UnifiedMessage]:
        """运行所有插件的消息预处理"""
        current = message
        for plugin in self._plugins.values():
            try:
                result = await plugin.on_message(current)
                if result is not None:
                    current = result
            except Exception as e:
                logger.error(f"插件 {plugin.name} 消息处理失败: {e}")
        return current

    async def process_response(self, response: str, message: UnifiedMessage) -> str:
        """运行所有插件的响应后处理"""
        current = response
        for plugin in self._plugins.values():
            try:
                result = await plugin.on_response(current, message)
                if result is not None:
                    current = result
            except Exception as e:
                logger.error(f"插件 {plugin.name} 响应处理失败: {e}")
        return current

    def get_all_commands(self) -> Dict[str, Callable]:
        """获取所有插件注册的指令"""
        commands = {}
        for plugin in self._plugins.values():
            try:
                commands.update(plugin.get_commands())
            except Exception as e:
                logger.error(f"获取插件 {plugin.name} 指令失败: {e}")
        return commands
