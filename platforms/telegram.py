"""Telegram Bot API adapter with ordered long-poll acknowledgement."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from platforms.base import (
    BasePlatform,
    PlatformConfigurationError,
    PlatformDeliveryError,
    PlatformNotReadyError,
    PlatformRateLimitedError,
)
from platforms.models import DeliveryReceipt, MessageType, Platform, UnifiedMessage
from utils.logger import setup_logger

logger = setup_logger("telegram")


class TelegramPlatform(BasePlatform):
    """A small Bot API client whose polling task is owned by the adapter lifecycle."""

    name = "telegram"

    def __init__(
        self,
        config: dict[str, Any],
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        super().__init__(config)
        self._token = str(config.get("token", ""))
        self._owner_id = str(config.get("owner_id", ""))
        self._allowed_users = {str(value) for value in config.get("allowed_users", [])}
        self._allowed_groups = {str(value) for value in config.get("allowed_groups", [])}
        self._group_mode = str(config.get("group_mode", "mention"))
        self._poll_timeout = int(config.get("poll_timeout", 30))
        self._retry_min = float(config.get("retry_min_seconds", 1.0))
        self._retry_max = float(config.get("retry_max_seconds", 30.0))
        self._api_base = f"https://api.telegram.org/bot{self._token}"
        self._active = False
        self._connected = False
        self._bot_username = ""
        self._offset = 0
        self._client = client
        self._owns_client = client is None
        self._sleep = sleep
        self._client_timeout = float(config.get("timeout", 40.0))

    @property
    def _transport_ready(self) -> bool:
        return self._connected and self._client is not None

    async def _start(self) -> None:
        if not self._token:
            raise PlatformConfigurationError("platforms.telegram.token is required")
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._client_timeout, connect=min(10.0, self._client_timeout))
            )
            self._owns_client = True

        self._active = True
        try:
            result = await self._call_api("getMe")
            if not isinstance(result, dict) or not result.get("id"):
                raise PlatformDeliveryError("Telegram getMe returned an invalid result")
            self._bot_username = str(result.get("username", ""))
            self._connected = True
        except BaseException:
            self._active = False
            raise

        self._create_task(self._poll_loop(), name="poll")
        logger.info("Telegram bot connected as @%s", self._bot_username)

    async def _quiesce(self) -> None:
        self._active = False

    async def _stop(self) -> None:
        self._active = False
        self._connected = False
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
        logger.info("Telegram adapter stopped")

    async def _poll_loop(self) -> None:
        delay = self._retry_min
        had_failure = False
        while self._active:
            try:
                updates = await self._call_api(
                    "getUpdates",
                    params={
                        "offset": self._offset,
                        "timeout": self._poll_timeout,
                        "allowed_updates": '["message"]',
                    },
                )
                if not isinstance(updates, list):
                    raise PlatformDeliveryError("Telegram getUpdates returned an invalid result")
                self._connected = True
                update_failed = False
                for update in updates:
                    if not self._active:
                        break
                    update_id = self._update_id(update)
                    if update_id < self._offset:
                        continue
                    try:
                        await self._process_update(update)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        # Don't acknowledge this update. The dispatch cache prevents
                        # running the chatbot twice if only response delivery failed.
                        self._record_transport_error(exc)
                        logger.error("Telegram update handling failed (%s)", type(exc).__name__)
                        update_failed = True
                        break
                    self._offset = update_id + 1
                if update_failed:
                    had_failure = True
                    if self._active:
                        await self._sleep(delay)
                        delay = min(max(self._retry_min, delay * 2), self._retry_max)
                    continue
                if had_failure:
                    self._stats["reconnects"] += 1
                had_failure = False
                delay = self._retry_min
            except asyncio.CancelledError:
                raise
            except PlatformRateLimitedError as exc:
                had_failure = True
                self._connected = False
                self._record_transport_error(exc)
                if self._active:
                    await self._sleep(max(delay, exc.retry_after))
                    delay = min(max(self._retry_min, delay * 2), self._retry_max)
            except Exception as exc:
                had_failure = True
                self._connected = False
                self._record_transport_error(exc)
                logger.warning("Telegram polling failed (%s)", type(exc).__name__)
                if self._active:
                    await self._sleep(delay)
                    delay = min(max(self._retry_min, delay * 2), self._retry_max)

    @staticmethod
    def _update_id(update: Any) -> int:
        if not isinstance(update, dict) or isinstance(update.get("update_id"), bool):
            raise PlatformDeliveryError("Telegram update has no valid update_id")
        try:
            return int(update["update_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PlatformDeliveryError("Telegram update has no valid update_id") from exc

    async def _process_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            self._record_ignored()
            return

        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            self._record_ignored()
            return

        user = message.get("from") or {}
        user_id = str(user.get("id", ""))
        if not user_id or not self._is_allowed(user_id):
            self._record_ignored()
            return

        chat = message.get("chat") or {}
        chat_type = str(chat.get("type", "private"))
        group_id = str(chat.get("id", "")) if chat_type != "private" else None
        if group_id and self._allowed_groups and group_id not in self._allowed_groups:
            self._record_ignored()
            return
        if group_id and not self._should_respond_group(text):
            self._record_ignored()
            return
        if group_id:
            text = self._remove_mention(text)

        await self.handle_callback(
            UnifiedMessage(
                platform=Platform.TELEGRAM,
                user_id=user_id,
                content=text,
                message_type=MessageType.TEXT,
                group_id=group_id,
                nickname=str(user.get("first_name", "")),
                message_id=str(message.get("message_id", "")),
                raw_data=update,
            )
        )

    async def _send_message(
        self,
        user_id: str,
        content: str,
        group_id: str | None = None,
    ) -> DeliveryReceipt:
        target_id = group_id or user_id
        result = await self._call_api("sendMessage", json={"chat_id": target_id, "text": content})
        if not isinstance(result, dict):
            raise PlatformDeliveryError("Telegram sendMessage returned an invalid result")
        return DeliveryReceipt(
            platform=Platform.TELEGRAM,
            target_id=target_id,
            message_id=str(result.get("message_id", "")),
        )

    async def _call_api(
        self,
        method: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        client = self._client
        if client is None:
            raise PlatformNotReadyError("Telegram HTTP client is not ready")
        try:
            response = await client.post(f"{self._api_base}/{method}", params=params, json=json)
        except httpx.HTTPError as exc:
            raise PlatformDeliveryError("Telegram HTTP request failed", code=type(exc).__name__) from exc

        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise PlatformDeliveryError("Telegram returned non-JSON data", code=response.status_code) from exc
        if not isinstance(payload, dict):
            raise PlatformDeliveryError("Telegram returned an invalid envelope", code=response.status_code)

        error_code = payload.get("error_code", response.status_code)
        if response.status_code == 429 or error_code == 429:
            parameters = payload.get("parameters") or {}
            retry_after = float(parameters.get("retry_after", self._retry_min))
            raise PlatformRateLimitedError("Telegram rate limit reached", retry_after=retry_after, code=429)
        if response.is_error or payload.get("ok") is not True:
            raise PlatformDeliveryError("Telegram API rejected the request", code=error_code)
        return payload.get("result")

    async def send_alert(self, message: str) -> DeliveryReceipt | None:
        if not self._owner_id:
            return None
        return await self.send_message(self._owner_id, f"⚠️ {message}")

    def _is_allowed(self, user_id: str) -> bool:
        return not self._allowed_users or user_id in self._allowed_users

    def _should_respond_group(self, text: str) -> bool:
        if self._group_mode == "always":
            return True
        if not self._bot_username:
            return False
        return re.search(rf"(?<!\w)@{re.escape(self._bot_username)}\b", text, flags=re.IGNORECASE) is not None

    def _remove_mention(self, text: str) -> str:
        if not self._bot_username:
            return text.strip()
        return re.sub(
            rf"(?<!\w)@{re.escape(self._bot_username)}\b",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()

    def status_snapshot(self) -> dict[str, Any]:
        snapshot = super().status_snapshot()
        snapshot.update({"connected": self._connected, "offset": self._offset})
        return snapshot
