"""QQ emoji CDN fallbacks."""
from __future__ import annotations

import re

_EMOJI_ID = re.compile(r"^[0-9a-fA-F]{16,64}$")
_EMOJI_FILE = re.compile(r"(?:^|[-_/])f9[-_]([0-9a-fA-F]{32})", re.IGNORECASE)

QQ_EMOJI_CDN = "https://gxh.vip.qq.com/club/item/parcel/item/{bucket}/{emoji_id}/raw300.gif"


def emoji_cdn_url(emoji_id: str) -> str:
    value = (emoji_id or "").strip()
    if not _EMOJI_ID.fullmatch(value):
        return ""
    return QQ_EMOJI_CDN.format(bucket=value[:2].lower(), emoji_id=value.lower())


def emoji_id_from_file(file_name: str) -> str:
    match = _EMOJI_FILE.search(file_name or "")
    return match.group(1) if match else ""
