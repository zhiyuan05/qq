"""Outbound audit for paths, credentials and configured secrets."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from utils.logger import setup_logger

logger = setup_logger("outbound_guard")

_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"\\\\[^\\\s]+\\"),
    re.compile(r"file://", re.IGNORECASE),
    re.compile(r"(?<![\w:/])/(?:app|data|home|root|etc|var|opt|srv|tmp)/"),
)
_CREDENTIAL_PATTERNS = (
    re.compile(
        r"(?:token|密码|密钥|口令|password|secret|api[_ -]?key|authorization|bearer|access[_ -]?key)"
        r"\s*[:=：是为]\s*\S{3,}",
        re.IGNORECASE,
    ),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
)


class OutboundGuard:
    def __init__(self, secrets: Iterable[str] = (), on_block=None):
        self._secrets = {secret for secret in secrets if secret and len(secret) >= 8}
        self._on_block = on_block

    def blocked(self, text: str) -> bool:
        if not text:
            return False
        if any(secret in text for secret in self._secrets):
            return True
        if any(pattern.search(text) for pattern in _CREDENTIAL_PATTERNS):
            return True
        # URLs frequently contain path segments such as /data/; only audit local
        # paths, not public links the bot legitimately shares.
        scrubbed = _URL_PATTERN.sub(" ", text)
        return any(pattern.search(scrubbed) for pattern in _PATH_PATTERNS)

    def filter(self, text: str) -> str:
        if not self.blocked(text):
            return text
        digest = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:8]
        logger.warning("出站内容被安全策略拦截 category=outbound digest=%s", digest)
        if self._on_block is not None:
            try:
                self._on_block()
            except Exception:
                pass
        return ""
