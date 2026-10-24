"""Interactive console adapter for local development."""
from __future__ import annotations

import asyncio
import sys
from typing import Any

from platforms.base import BasePlatform, PlatformState
from platforms.models import DeliveryReceipt, MessageType, Platform, UnifiedMessage
from utils.logger import setup_logger

logger = setup_logger("console")


def safe_print(*args: Any, **kwargs: Any) -> None:
    """Print with a replacement fallback for narrow Windows terminals."""

    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        text = " ".join(str(arg) for arg in args)
        text = text.encode("utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace")
        print(text, **kwargs)


BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
DIM = "\033[2m"
RESET = "\033[0m"
BOLD = "\033[1m"


class ConsolePlatform(BasePlatform):
    name = "console"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._running = False

    @property
    def _transport_ready(self) -> bool:
        return self._running or self.state is PlatformState.STOPPING

    async def _start(self) -> None:
        self._running = True
        safe_print(f"\n{CYAN}{'=' * 40}{RESET}")
        safe_print(f"{CYAN}{BOLD}  聊天机器人 - 控制台模式{RESET}")
        safe_print(f"{CYAN}{'=' * 40}{RESET}")
        safe_print(f"{DIM}  输入消息开始对话{RESET}")
        safe_print(f"{DIM}  /help 查看命令  /quit 退出{RESET}")
        safe_print(f"{CYAN}{'=' * 40}{RESET}\n")
        self._create_task(self._input_loop(), name="input")

    async def _input_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                user_input = await loop.run_in_executor(None, self._read_input)
                if user_input is None:
                    self._running = False
                    break
                if not user_input:
                    continue

                command = user_input.strip().lower()
                if command == "/quit":
                    self._running = False
                    safe_print(f"\n{YELLOW}再见！{RESET}\n")
                    break
                if command == "/clear":
                    safe_print("\033[2J\033[H", end="")
                    continue

                await self.handle_callback(
                    UnifiedMessage(
                        platform=Platform.CONSOLE,
                        user_id="console_user",
                        content=user_input,
                        message_type=MessageType.TEXT,
                    )
                )
            except asyncio.CancelledError:
                raise
            except (EOFError, KeyboardInterrupt):
                self._running = False
                safe_print(f"\n{YELLOW}再见！{RESET}\n")
                break
            except Exception as exc:
                self._record_transport_error(exc)
                logger.error("Console input failed (%s)", type(exc).__name__)

    def _read_input(self) -> str | None:
        try:
            return input(f"{BLUE}你: {RESET}")
        except EOFError:
            return None

    async def _quiesce(self) -> None:
        self._running = False

    async def _stop(self) -> None:
        self._running = False
        logger.info("Console adapter stopped")

    async def _send_message(
        self,
        user_id: str,
        content: str,
        group_id: str | None = None,
    ) -> DeliveryReceipt:
        lines = content.split("\n")
        safe_print(f"\n{GREEN}🤖 {lines[0]}{RESET}")
        for line in lines[1:]:
            safe_print(f"{GREEN}   {line}{RESET}")
        safe_print()
        return DeliveryReceipt(platform=Platform.CONSOLE, target_id=group_id or user_id)
