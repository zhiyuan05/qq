"""指令解析器"""
from dataclasses import dataclass, field
from typing import List

from utils.logger import setup_logger

logger = setup_logger("command_parser")


@dataclass
class ParsedCommand:
    is_command: bool
    command: str = ""
    subcommand: str = ""
    args: List[str] = field(default_factory=list)
    raw: str = ""


class CommandParser:
    def __init__(self, prefix: str = "/"):
        self.prefix = prefix

    def parse(self, text: str) -> ParsedCommand:
        text = text.strip()

        if not text.startswith(self.prefix):
            return ParsedCommand(is_command=False, raw=text)

        parts = text[len(self.prefix):].strip().split()
        if not parts:
            return ParsedCommand(is_command=False, raw=text)

        command = parts[0].lower()
        subcommand = parts[1].lower() if len(parts) > 1 else ""
        args = parts[2:] if len(parts) > 2 else []

        return ParsedCommand(
            is_command=True,
            command=command,
            subcommand=subcommand,
            args=args,
            raw=text,
        )
