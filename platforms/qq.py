"""QQ平台适配器"""
import asyncio
from typing import Optional

import httpx

from platforms.base import BasePlatform
from platforms.models import UnifiedMessage, Platform, MessageType
from utils.logger import setup_logger

logger = setup_logger("qq")


class QQPlatform(BasePlatform):
    name = "qq"

    def __init__(self, config: dict):
        super().__init__(config)
        self.onebot_url = config.get("onebot_url", "ws://127.0.0.1:3001")
        self._http_base = self.onebot_url.replace("ws://", "http://").replace("wss://", "https://")
        self._running = False

    async def start(self):
        self._running = True
        loop = asyncio.get_event_loop()
        loop.create_task(self._poll_loop())
        logger.info(f"QQ适配器启动，连接: {self.onebot_url}")

    async def _poll_loop(self):
        retry_delay = 1
        max_retry = 60
        async with httpx.AsyncClient() as client:
            while self._running:
                try:
                    resp = await client.get(
                        f"{self._http_base}/get_new_msg",
                        params={"count": 10},
                        timeout=5,
                    )
                    if resp.status_code == 200:
                        retry_delay = 1  # 成功后重置
                        data = resp.json()
                        for msg in data.get("data", []):
                            await self._process_raw_message(msg)
                except httpx.ConnectError:
                    logger.warning(f"无法连接到NapCat({retry_delay}s后重试)...")
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, max_retry)
                except Exception as e:
                    logger.error(f"QQ轮询错误: {e}")
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, max_retry)

    async def _process_raw_message(self, raw: dict):
        try:
            post_type = raw.get("post_type")
            if post_type != "message":
                return

            message = raw.get("message", "")
            if isinstance(message, list):
                text_parts = [seg.get("data", {}).get("text", "") for seg in message if seg.get("type") == "text"]
                message = "".join(text_parts)

            if not message.strip():
                return

            user_id = str(raw.get("user_id", ""))
            group_id = str(raw.get("group_id", "")) if raw.get("message_type") == "group" else None

            unified = UnifiedMessage(
                platform=Platform.QQ,
                user_id=user_id,
                content=message,
                message_type=MessageType.TEXT,
                group_id=group_id,
                nickname=raw.get("sender", {}).get("nickname", ""),
                message_id=str(raw.get("message_id", "")),
            )

            await self.handle_callback(unified)
        except Exception as e:
            logger.error(f"处理QQ消息失败: {e}")

    async def stop(self):
        self._running = False
        logger.info("QQ适配器已停止")

    async def send_message(self, user_id: str, content: str, group_id: Optional[str] = None):
        async with httpx.AsyncClient() as client:
            try:
                if group_id:
                    endpoint = "/send_group_msg"
                    payload = {"group_id": int(group_id), "message": content}
                else:
                    endpoint = "/send_private_msg"
                    payload = {"user_id": int(user_id), "message": content}

                resp = await client.post(
                    f"{self._http_base}{endpoint}",
                    json=payload,
                    timeout=10,
                )
                if resp.status_code != 200:
                    logger.error(f"发送QQ消息失败: {resp.text}")
                else:
                    logger.debug(f"QQ消息已发送: {group_id or user_id}")
            except Exception as e:
                logger.error(f"发送QQ消息异常: {e}")
