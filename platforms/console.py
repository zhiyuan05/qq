"""控制台调试平台 - 本地测试用"""
import asyncio
import sys
from typing import Optional

from platforms.base import BasePlatform
from platforms.models import UnifiedMessage, Platform, MessageType
from utils.logger import setup_logger

logger = setup_logger("console")

# Windows GBK 兼容输出
def safe_print(*args, **kwargs):
    """打印内容，遇到编码错误时自动降级"""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        # 替换无法编码的字符
        text = " ".join(str(a) for a in args)
        text = text.encode("utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace")
        print(text, **kwargs)

# ANSI 颜色
BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
DIM = "\033[2m"
RESET = "\033[0m"
BOLD = "\033[1m"


class ConsolePlatform(BasePlatform):
    name = "console"

    def __init__(self, config: dict = None):
        super().__init__(config or {})
        self._running = False

    async def start(self):
        self._running = True
        safe_print(f"\n{CYAN}{'='*40}{RESET}")
        safe_print(f"{CYAN}{BOLD}  聊天机器人 - 控制台模式{RESET}")
        safe_print(f"{CYAN}{'='*40}{RESET}")
        safe_print(f"{DIM}  输入消息开始对话{RESET}")
        safe_print(f"{DIM}  /help 查看指令  /quit 退出{RESET}")
        safe_print(f"{CYAN}{'='*40}{RESET}\n")

        loop = asyncio.get_event_loop()
        loop.create_task(self._input_loop())

    async def _input_loop(self):
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                user_input = await loop.run_in_executor(None, self._read_input)
                if not user_input:
                    continue

                if user_input.strip().lower() == "/quit":
                    self._running = False
                    safe_print(f"\n{YELLOW}再见！{RESET}\n")
                    break

                if user_input.strip().lower() == "/clear":
                    safe_print("\033[2J\033[H", end="")
                    continue

                unified = UnifiedMessage(
                    platform=Platform.CONSOLE,
                    user_id="console_user",
                    content=user_input,
                    message_type=MessageType.TEXT,
                )

                if self._callback:
                    response = await self._callback(unified)
                    if response:
                        # 格式化输出
                        lines = response.split("\n")
                        safe_print(f"\n{GREEN}🤖 {lines[0]}{RESET}")
                        for line in lines[1:]:
                            safe_print(f"{GREEN}   {line}{RESET}")
                        safe_print()

            except (EOFError, KeyboardInterrupt):
                self._running = False
                safe_print(f"\n{YELLOW}再见！{RESET}\n")
                break
            except Exception as e:
                logger.error(f"控制台输入错误: {e}")

    def _read_input(self) -> str:
        try:
            return input(f"{BLUE}你: {RESET}")
        except EOFError:
            return ""

    async def stop(self):
        self._running = False
        logger.info("控制台已停止")

    async def send_message(self, user_id: str, content: str, group_id: Optional[str] = None):
        lines = content.split("\n")
        safe_print(f"\n{GREEN}🤖 {lines[0]}{RESET}")
        for line in lines[1:]:
            safe_print(f"{GREEN}   {line}{RESET}")
        safe_print()
