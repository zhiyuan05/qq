"""平台抽象基类"""
from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Optional

from platforms.models import UnifiedMessage
from utils.message_splitter import split_message
from utils.delay import get_delay
from utils.logger import setup_logger

logger = setup_logger("platform")

MessageCallback = Callable[[UnifiedMessage], Awaitable[Optional[str]]]


class BasePlatform(ABC):
    name: str = "base"

    def __init__(self, config: dict):
        self.config = config
        self._callback: Optional[MessageCallback] = None
        self._max_msg_length: int = config.get("max_msg_length", 2000)

    def on_message(self, callback: MessageCallback):
        self._callback = callback

    @abstractmethod
    async def start(self):
        ...

    @abstractmethod
    async def stop(self):
        ...

    @abstractmethod
    async def send_message(self, user_id: str, content: str, group_id: Optional[str] = None):
        ...

    async def send_image(self, user_id: str, image_path: str, group_id: Optional[str] = None):
        pass

    async def send_long_message(self, user_id: str, content: str, group_id: Optional[str] = None):
        """拆分长消息 + 模拟人类延迟后发送"""
        chunks = split_message(content, max_length=self._max_msg_length)
        delay = get_delay()
        for i, chunk in enumerate(chunks):
            if i > 0:
                await delay.wait(len(chunk))
            await self.send_message(user_id, chunk, group_id)

    async def handle_callback(self, unified: UnifiedMessage):
        """统一处理：回调 → 拆分发送"""
        if not self._callback:
            return
        response = await self._callback(unified)
        if response:
            target = unified.group_id or unified.user_id
            await self.send_long_message(target, response, unified.group_id)
