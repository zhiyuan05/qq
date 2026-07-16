"""Token 计数"""
from typing import List, Dict


def count_tokens(messages: List[Dict[str, str]], model: str = "deepseek-chat") -> int:
    try:
        import tiktoken
        encoding = tiktoken.encoding_for_model("gpt-4")
    except Exception:
        total = sum(len(m.get("content", "")) for m in messages)
        return total // 2

    total = 0
    for msg in messages:
        total += 4
        for value in msg.values():
            total += len(encoding.encode(str(value)))
    total += 2
    return total
