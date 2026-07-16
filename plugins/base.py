"""插件基类"""
from abc import ABC, abstractmethod
from typing import Callable, Dict, Optional

from platforms.models import UnifiedMessage


class BasePlugin(ABC):
    name: str = "base"
    version: str = "1.0.0"
    description: str = ""

    def __init__(self, config: dict = None):
        self.config = config or {}

    async def on_message(self, message: UnifiedMessage) -> Optional[UnifiedMessage]:
        """消息预处理，返回None继续处理，返回修改后的消息替换原消息"""
        return None

    async def on_response(self, response: str, message: UnifiedMessage) -> Optional[str]:
        """响应后处理，返回None使用原响应，返回修改后的响应替换原响应"""
        return None

    def get_commands(self) -> Dict[str, Callable]:
        """注册自定义指令"""
        return {}

    async def start(self):
        """插件启动"""
        pass

    async def stop(self):
        """插件停止"""
        pass
