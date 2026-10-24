from __future__ import annotations

"""Token 计数"""
from typing import Any, Dict, List

_ENCODING: Any = None
_ENCODING_FAILED = False


def _get_encoding() -> Any:
    global _ENCODING, _ENCODING_FAILED
    if _ENCODING is not None or _ENCODING_FAILED:
        return _ENCODING
    try:
        import tiktoken

        _ENCODING = tiktoken.encoding_for_model("gpt-4")
    except Exception:
        _ENCODING_FAILED = True
    return _ENCODING


def _content_length(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    total += len(str(block.get("text", "")))
                elif block.get("type") in {"image_url", "input_image"}:
                    continue
                else:
                    total += len(str(block))
            else:
                total += len(str(block))
        return total
    return len(str(content))


def _image_blocks(content: Any) -> int:
    if not isinstance(content, list):
        return 0
    return sum(
        1
        for block in content
        if isinstance(block, dict) and block.get("type") in {"image_url", "input_image"}
    )


def _text_tokens(encoding, content: Any) -> int:
    if isinstance(content, str):
        return len(encoding.encode(content))
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                total += len(encoding.encode(str(block.get("text", ""))))
        return total
    return len(encoding.encode(str(content)))


def count_message_tokens(
    messages: List[Dict[str, Any]],
    model: str = "deepseek-chat",
    *,
    image_token_cost: int = 800,
) -> int:
    """Count a chat payload, including multimodal content blocks.

    Base64 image data is never tokenized; each image block costs a fixed amount.
    """

    encoding = _get_encoding()
    if encoding is None:
        text_total = sum(_content_length(message.get("content", "")) for message in messages)
        image_total = image_token_cost * sum(
            _image_blocks(message.get("content", "")) for message in messages
        )
        return text_total // 2 + image_total

    total = 0
    for message in messages:
        total += 4
        total += _text_tokens(encoding, message.get("content", ""))
        total += image_token_cost * _image_blocks(message.get("content", ""))
        for key, value in message.items():
            if key != "content":
                total += len(encoding.encode(str(value)))
    total += 2
    return total


def count_tokens(messages: List[Dict[str, str]], model: str = "deepseek-chat") -> int:
    return count_message_tokens(messages, model)
