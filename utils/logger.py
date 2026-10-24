from __future__ import annotations

"""日志配置 — 支持 contextvars 请求追踪"""
import contextvars
import logging
import logging.handlers
import sys
from pathlib import Path

# 请求追踪 ID：在每个请求开始时设置，所有日志自动携带
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
_file_handlers: dict[str, logging.Handler] = {}


def set_request_id(request_id: str) -> contextvars.Token[str]:
    return _request_id.set(request_id)


def reset_request_id(token: contextvars.Token[str]) -> None:
    _request_id.reset(token)


def get_request_id() -> str:
    return _request_id.get()


class _ContextLogger(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
        rid = get_request_id()
        if rid:
            msg = f"[{rid}] {msg}"
        return msg, kwargs


def get_logger(name: str = "chatbot") -> _ContextLogger:
    """获取带请求追踪上下文的日志器。"""
    return _ContextLogger(setup_logger(name), {})


def setup_logger(
    name: str = "chatbot",
    level: str = "INFO",
    log_file: str = "data/logs/chatbot.log",
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    file_handler = _file_handlers.get(log_file)
    if file_handler is None:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        _file_handlers[log_file] = file_handler
    logger.addHandler(file_handler)

    return logger
