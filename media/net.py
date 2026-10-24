"""SSRF-hardened HTTP fetching for inbound media.

Every hop re-resolves the host and rejects the request if *any* resolved
address is non-global. Redirects are followed manually so each target is
validated. The resolver is injectable for tests.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from utils.logger import setup_logger

logger = setup_logger("media.net")

REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class MediaFetchError(RuntimeError):
    """Fetch rejected or failed; the caller should degrade to text."""


@dataclass(frozen=True)
class FetchResult:
    content: bytes
    content_type: str
    final_url: str


def _is_global_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def _resolve_global(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise MediaFetchError(f"DNS 解析失败: {host}") from exc
    addresses = [info[4][0] for info in infos]
    if not addresses:
        raise MediaFetchError(f"DNS 无结果: {host}")
    if any(not _is_global_address(address) for address in addresses):
        raise MediaFetchError(f"目标地址不是公网地址: {host}")
    # Prefer IPv4 to avoid waiting for unusable IPv6 routes.
    addresses.sort(key=lambda value: ":" in value)
    return addresses


def _validate_url(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise MediaFetchError("仅允许 http/https")
    if parts.username or parts.password:
        raise MediaFetchError("URL 不允许携带凭据")
    host = parts.hostname or ""
    if not host:
        raise MediaFetchError("URL 缺少主机名")
    if host.lower() in {"localhost", "localhost.localdomain"} or host.lower().endswith(".local"):
        raise MediaFetchError("拒绝本地主机")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return parts.scheme, host, port


async def fetch_bytes(
    url: str,
    *,
    max_bytes: int,
    total_timeout: float = 20.0,
    attempt_timeout: float = 8.0,
    max_redirects: int = 5,
    resolver: Callable[[str, int], list[str]] | None = None,
) -> FetchResult:
    """Fetch a URL with size, timeout and SSRF limits.

    ``resolver`` is injectable for tests; production uses getaddrinfo.
    """

    current = url
    deadline = asyncio.get_running_loop().time() + total_timeout
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/json;q=0.9,"
            "image/avif,image/webp,image/*,audio/*,*/*;q=0.5"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(attempt_timeout),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        for _hop in range(max_redirects + 1):
            _validate_url(current)
            parts = urlsplit(current)
            host = parts.hostname or ""
            port = parts.port or (443 if parts.scheme == "https" else 80)
            if resolver is not None:
                resolver(host, port)
            else:
                await _resolve_global(host, port)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise MediaFetchError("下载超时")
            try:
                async with asyncio.timeout(remaining):
                    async with client.stream("GET", current, headers=headers) as response:
                        if response.status_code in REDIRECT_STATUSES:
                            location = response.headers.get("location", "")
                            if not location:
                                raise MediaFetchError("重定向缺少 Location")
                            current = urljoin(current, location)
                            continue
                        response.raise_for_status()
                        declared = response.headers.get("content-length")
                        if declared and declared.isdigit() and int(declared) > max_bytes:
                            raise MediaFetchError("内容超过大小上限")
                        chunks: list[bytes] = []
                        total = 0
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > max_bytes:
                                raise MediaFetchError("内容超过大小上限")
                            chunks.append(chunk)
                        return FetchResult(
                            content=b"".join(chunks),
                            content_type=response.headers.get("content-type", ""),
                            final_url=current,
                        )
            except MediaFetchError:
                raise
            except (httpx.HTTPError, TimeoutError, OSError) as exc:
                raise MediaFetchError(f"下载失败: {type(exc).__name__}") from exc
    raise MediaFetchError("重定向次数过多")
