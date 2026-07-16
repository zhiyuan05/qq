"""长消息智能拆分"""
import re
from typing import List


def split_message(text: str, max_length: int = 2000) -> List[str]:
    """按语义边界拆分长消息"""
    if len(text) <= max_length:
        return [text]

    chunks = []
    current = ""

    paragraphs = text.split("\n\n")
    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_length:
            current = current + "\n\n" + para if current else para
        else:
            if current:
                chunks.append(current.strip())
            if len(para) > max_length:
                sentences = _split_sentences(para)
                current = ""
                for sent in sentences:
                    if len(current) + len(sent) <= max_length:
                        current += sent
                    else:
                        if current:
                            chunks.append(current.strip())
                        current = sent
            else:
                current = para

    if current.strip():
        chunks.append(current.strip())

    return chunks


def _split_sentences(text: str) -> List[str]:
    sentences = re.split(r'(?<=[。！？.!?])\s*', text)
    return [s for s in sentences if s.strip()]
