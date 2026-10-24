"""Turn a model response into a human-like ReplyPlan."""
from __future__ import annotations

import random
import re

from platforms.models import MessageType, ReplyPlan, UnifiedMessage
from utils.config import HumanizeConfig, VoiceConfig

_STICKER_MARKER = re.compile(r"\[sticker:\s*([^\]\n]{1,50})\]", re.IGNORECASE)
_VOICE_MARKER = re.compile(r"\[voice\]", re.IGNORECASE)
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?…])\s*|\n+")
_FENCE = re.compile(r"(```.*?```)", re.S)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_LIST_MARK = re.compile(r"^\s{0,3}(?:[-*•]|\d{1,2}[.)])\s+", re.M)
_FORMAT_REQUEST = re.compile(r"markdown|表格|列表|分点|代码块|排版|格式", re.I)
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_RULE = re.compile(r"^\s*(?:---+|\*\*\*+)\s*$", re.M)


class Humanizer:
    def __init__(self, config: HumanizeConfig, voice: VoiceConfig | None = None):
        self.config = config
        self.voice = voice or VoiceConfig()

    def _split(self, text: str) -> list[str]:
        text = text.strip()
        if len(text) <= self.config.split_threshold:
            return [text] if text else []
        pieces = [piece.strip() for piece in _SENTENCE_SPLIT.split(text) if piece.strip()]
        if not pieces:
            return [text]

        merged: list[str] = []
        for piece in pieces:
            if merged and (len(piece) < 3 or len(merged[-1]) < 3):
                separator = "" if merged[-1][-1] in "。！？!?…" else " "
                merged[-1] = f"{merged[-1]}{separator}{piece}"
            else:
                merged.append(piece)

        while len(merged) > self.config.max_bubbles:
            merged[-2] = f"{merged[-2]}{merged[-1]}"
            merged.pop()
        return merged

    @staticmethod
    def _extract_markers(raw: str) -> tuple[str, str, bool]:
        """Remove sticker/voice markers outside fenced code blocks."""
        parts = _FENCE.split(raw)
        sticker_tag = ""
        voice_requested = False
        for index, part in enumerate(parts):
            if part.startswith("```"):
                continue
            if not voice_requested and _VOICE_MARKER.search(part):
                voice_requested = True
            match = _STICKER_MARKER.search(part)
            if match and not sticker_tag:
                sticker_tag = match.group(1).strip()
            parts[index] = _VOICE_MARKER.sub("", _STICKER_MARKER.sub("", part))
        return "".join(parts).strip(), sticker_tag, voice_requested

    @staticmethod
    def _polish(text: str) -> str:
        """Strip chat-unfriendly Markdown outside fenced code blocks."""
        parts = _FENCE.split(text)
        for index, part in enumerate(parts):
            if part.startswith("```"):
                continue
            part = _HEADING.sub("", part)
            part = _LIST_MARK.sub("", part)
            part = _BOLD.sub(r"\1", part)
            part = _INLINE_CODE.sub(r"\1", part)
            part = _RULE.sub("", part)
            parts[index] = re.sub(r"\n{3,}", "\n\n", part)
        return "".join(parts).strip()

    def _delay(self, text: str, rng: random.Random) -> float:
        seconds = len(text) / self.config.typing_chars_per_second
        seconds = max(self.config.min_delay, min(self.config.max_delay, seconds))
        return round(seconds * rng.uniform(0.7, 1.3), 2)

    async def plan(
        self,
        text: str,
        message: UnifiedMessage,
        *,
        stickers=None,
        rng: random.Random | None = None,
    ) -> ReplyPlan:
        rng = rng or random.Random()
        raw = text or ""
        cleaned, sticker_tag, voice_requested = self._extract_markers(raw)
        if self.config.polish and not _FORMAT_REQUEST.search(message.content or ""):
            cleaned = self._polish(cleaned)

        plan = ReplyPlan(humanized=message.group_id is None, fallback_text=cleaned)
        if not cleaned and not sticker_tag:
            plan.humanized = False
            return plan

        bubbles = self._split(cleaned) if cleaned else []
        if any(len(bubble) > self.config.max_bubble_chars for bubble in bubbles):
            plan.humanized = False
            plan.bubbles = [cleaned] if cleaned else []
            return plan

        if message.group_id is None and sticker_tag and stickers is not None:
            try:
                sticker = await stickers.pick(sticker_tag, message.session_key)
            except Exception:
                sticker = None
            if sticker is not None:
                plan.sticker_path = sticker.path
                plan.sticker_tag = sticker_tag
                plan.sticker_hash = sticker.hash
                plan.sticker_delay = 0.6

        wants_voice = (
            message.group_id is None
            and self.voice.enabled
            and bubbles
            and len(bubbles[0]) <= self.voice.voice_max_chars
            and (voice_requested or rng.random() < self.voice.voice_probability)
        )
        if wants_voice:
            plan.voice_text = bubbles[0]
            plan.voice_delay = 0.4
            bubbles = bubbles[1:]
            plan.fallback_text = plan.voice_text

        plan.bubbles = bubbles
        plan.delays = [self._delay(bubble, rng) for bubble in bubbles[1:]]

        if message.group_id is None and message.message_id and _SAFE_ID.fullmatch(message.message_id):
            mandatory = bool(message.attachments) or message.message_type in {
                MessageType.IMAGE,
                MessageType.VOICE,
            }
            if mandatory or rng.random() < self.config.quote_probability:
                plan.reply_to = message.message_id
        return plan
