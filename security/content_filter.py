"""Sensitive content filtering with configurable word list."""
import time
from pathlib import Path
from utils.logger import setup_logger

logger = setup_logger("content_filter")


class ContentFilter:
    def __init__(self, words_file: str = "config/sensitive_words.txt"):
        self._words: list[str] = []
        self._filter_log: list[dict] = []
        self._load_words(words_file)

    def _load_words(self, path: str):
        p = Path(path)
        if not p.exists():
            logger.warning(f"敏感词文件不存在: {path}")
            return

        words = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                words.append(line)
        self._words = words
        logger.info(f"已加载 {len(words)} 个敏感词")

    def filter(self, text: str) -> str:
        if not self._words:
            return text

        result = text
        found = []
        for word in self._words:
            if word in result:
                result = result.replace(word, "***")
                found.append(word)

        if found:
            self._filter_log.append({
                "time": time.time(),
                "words_found": found,
                "original": text,
                "filtered": result,
            })
            logger.info(f"内容过滤: 发现敏感词 {found}")

        return result

    def has_sensitive(self, text: str) -> bool:
        return any(word in text for word in self._words)

    def get_filter_log(self, limit: int = 100) -> list[dict]:
        return self._filter_log[-limit:]

    def reload(self, path: str):
        self._load_words(path)
