"""Telegram Bot API adapter."""
import asyncio
from typing import Optional

import httpx

from platforms.base import BasePlatform
from platforms.models import UnifiedMessage, Platform, MessageType
from utils.logger import setup_logger

logger = setup_logger("telegram")


class TelegramPlatform(BasePlatform):
    name = "telegram"

    def __init__(self, config: dict):
        super().__init__(config)
        self._token = config.get("token", "")
        self._owner_id = str(config.get("owner_id", ""))
        self._allowed_users = [str(u) for u in config.get("allowed_users", [])]
        self._group_mode = config.get("group_mode", "mention")
        self._api_base = f"https://api.telegram.org/bot{self._token}"
        self._running = False
        self._bot_username = ""
        self._offset = 0

    async def start(self):
        if not self._token:
            logger.error("Telegram token 未配置")
            return

        self._running = True
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self._api_base}/getMe", timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    self._bot_username = data.get("result", {}).get("username", "")
                    logger.info(f"Telegram bot 已连接: @{self._bot_username}")
        except Exception as e:
            logger.error(f"Telegram 连接失败: {e}")
            return

        loop = asyncio.get_event_loop()
        loop.create_task(self._poll_loop())

    async def _poll_loop(self):
        async with httpx.AsyncClient() as client:
            while self._running:
                try:
                    resp = await client.get(
                        f"{self._api_base}/getUpdates",
                        params={"offset": self._offset, "timeout": 30},
                        timeout=35,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        for update in data.get("result", []):
                            self._offset = update["update_id"] + 1
                            await self._process_update(update)
                except httpx.ConnectError:
                    logger.warning("Telegram 连接失败，重试中...")
                    await asyncio.sleep(5)
                except Exception as e:
                    logger.error(f"Telegram 轮询错误: {e}")
                    await asyncio.sleep(1)

    async def _process_update(self, update: dict):
        message = update.get("message")
        if not message:
            return

        text = message.get("text", "")
        if not text:
            return

        user = message.get("from", {})
        user_id = str(user.get("id", ""))
        nickname = user.get("first_name", "")

        if not self._is_allowed(user_id):
            return

        chat = message.get("chat", {})
        chat_type = chat.get("type", "private")
        group_id = str(chat.get("id", "")) if chat_type != "private" else None

        if group_id and not self._should_respond_group(text):
            return

        if group_id and self._bot_username:
            text = text.replace(f"@{self._bot_username}", "").strip()

        unified = UnifiedMessage(
            platform=Platform.TELEGRAM,
            user_id=user_id,
            content=text,
            message_type=MessageType.TEXT,
            group_id=group_id,
            nickname=nickname,
            message_id=str(message.get("message_id", "")),
        )

        await self.handle_callback(unified)

    async def stop(self):
        self._running = False
        logger.info("Telegram 已停止")

    async def send_message(self, user_id: str, content: str, group_id: Optional[str] = None):
        target = group_id or user_id
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self._api_base}/sendMessage",
                    json={"chat_id": target, "text": content, "parse_mode": "Markdown"},
                    timeout=10,
                )
                if resp.status_code != 200:
                    resp = await client.post(
                        f"{self._api_base}/sendMessage",
                        json={"chat_id": target, "text": content},
                        timeout=10,
                    )
                logger.debug(f"Telegram 消息已发送: {target}")
        except Exception as e:
            logger.error(f"Telegram 发送失败: {e}")

    async def send_alert(self, message: str):
        if self._owner_id:
            await self.send_message(self._owner_id, f"⚠️ {message}")

    def _is_allowed(self, user_id: str) -> bool:
        if not self._allowed_users:
            return True
        return user_id in self._allowed_users

    def _should_respond_group(self, text: str) -> bool:
        if self._group_mode == "always":
            return True
        return self._bot_username and f"@{self._bot_username}" in text
