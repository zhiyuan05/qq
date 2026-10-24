"""QQ adapter for the OneBot v11 forward WebSocket protocol."""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import re
import shutil
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect as websocket_connect

from media.images import sniff_image_mime
from media.qq_cdn import emoji_cdn_url
from media.store import MediaStore, validate_local_file
from media.video import analyze_video
from platforms.delivery import OutboundGate
from platforms.base import (
    BasePlatform,
    PlatformConfigurationError,
    PlatformDeliveryError,
    PlatformNotReadyError,
)
from platforms.models import (
    Attachment,
    DeliveryReceipt,
    MessageType,
    Platform,
    QuotedMessage,
    ReplyPlan,
    UnifiedMessage,
)
from platforms.qq_segments import MediaRef, parse_message
from security.outbound_guard import OutboundGuard
from utils.config import MediaConfig
from utils.logger import setup_logger

logger = setup_logger("qq")


class QQPlatform(BasePlatform):
    """Bidirectional OneBot v11 client with a single WebSocket receive owner."""

    name = "qq"

    def __init__(
        self,
        config: dict[str, Any],
        *,
        connector: Callable[..., Any] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        media_store: MediaStore | None = None,
        sticker_library: Any | None = None,
        outbound_guard: OutboundGuard | None = None,
        delivery_gate: OutboundGate | None = None,
        voice_service: Any | None = None,
        metrics: Any | None = None,
    ):
        super().__init__(config)
        self.media_store = media_store
        self.sticker_library = sticker_library
        self.outbound_guard = outbound_guard
        self.delivery_gate = delivery_gate
        self.voice_service = voice_service
        self.metrics = metrics
        self._delivery_lock = asyncio.Lock()
        self._media_config = media_store.config if media_store else MediaConfig(enabled=False)
        self._self_id = ""
        self._quote_cache: OrderedDict[str, QuotedMessage] = OrderedDict()
        self._quote_cache_size = 512
        self._quote_misses: set[str] = set()
        self.onebot_url = str(config.get("onebot_url", "ws://127.0.0.1:3001"))
        self._access_token = str(config.get("access_token", ""))
        self._allowed_users = {str(value) for value in config.get("allowed_users", [])}
        self._allowed_groups = {str(value) for value in config.get("allowed_groups", [])}
        self._connect_timeout = float(config.get("connect_timeout", 10.0))
        self._api_timeout = float(config.get("api_timeout", 15.0))
        self._retry_min = float(config.get("reconnect_min_seconds", 1.0))
        self._retry_max = float(config.get("reconnect_max_seconds", 30.0))
        self._connector = connector or websocket_connect
        self._sleep = sleep
        self._active = False
        self._connected = asyncio.Event()
        self._websocket: Any | None = None
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=int(config.get("event_queue_size", 1000))
        )
        self._event_semaphore = asyncio.Semaphore(int(config.get("event_concurrency", 2)))
        self._event_tasks: set[asyncio.Task[Any]] = set()
        self._pending_actions: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._send_lock = asyncio.Lock()

    def _metric(self, name: str, value: int = 1) -> None:
        if self.metrics is None:
            return
        try:
            self.metrics.increment(name, value)
        except Exception:
            pass

    @property
    def _transport_ready(self) -> bool:
        return self._connected.is_set() and self._websocket is not None

    async def _start(self) -> None:
        if not self.onebot_url.startswith(("ws://", "wss://")):
            raise PlatformConfigurationError("platforms.qq.onebot_url must use ws:// or wss://")
        self._active = True
        self._connected.clear()
        self._create_task(self._event_worker(), name="events")
        self._create_task(self._connection_loop(), name="connection")
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=self._connect_timeout)
        except TimeoutError as exc:
            raise PlatformNotReadyError("OneBot WebSocket did not become ready before startup timeout") from exc
        logger.info("QQ OneBot WebSocket adapter started")

    async def _quiesce(self) -> None:
        self._active = False

    async def _stop(self) -> None:
        self._active = False
        self._connected.clear()
        websocket = self._websocket
        self._websocket = None
        self._fail_pending_actions(PlatformNotReadyError("OneBot WebSocket stopped"))
        if websocket is not None:
            await websocket.close()
        if self._event_tasks:
            for task in list(self._event_tasks):
                task.cancel()
            await asyncio.gather(*self._event_tasks, return_exceptions=True)
            self._event_tasks.clear()
        logger.info("QQ OneBot WebSocket adapter stopped")

    async def _connection_loop(self) -> None:
        delay = self._retry_min
        connected_once = False
        while self._active:
            headers = {"Authorization": f"Bearer {self._access_token}"} if self._access_token else None
            try:
                async with self._connector(
                    self.onebot_url,
                    additional_headers=headers,
                    open_timeout=self._connect_timeout,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=4 * 1024 * 1024,
                    proxy=None,
                ) as websocket:
                    self._websocket = websocket
                    self._connected.set()
                    if connected_once:
                        self._stats["reconnects"] += 1
                    connected_once = True
                    delay = self._retry_min
                    logger.info("QQ OneBot WebSocket connected")
                    async for frame in websocket:
                        await self._route_frame(frame)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_transport_error(exc)
                logger.warning("QQ OneBot WebSocket disconnected (%s)", type(exc).__name__)
            finally:
                self._connected.clear()
                self._websocket = None
                self._fail_pending_actions(PlatformNotReadyError("OneBot WebSocket disconnected"))

            if self._active:
                await self._sleep(delay)
                delay = min(max(self._retry_min, delay * 2), self._retry_max)

    async def _route_frame(self, frame: str | bytes) -> None:
        try:
            if isinstance(frame, bytes):
                frame = frame.decode("utf-8")
            payload = json.loads(frame)
            if not isinstance(payload, dict):
                raise TypeError("OneBot frame must be a JSON object")
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            self._record_transport_error(exc)
            return

        echo = payload.get("echo")
        if echo is not None:
            future = self._pending_actions.get(str(echo))
            if future is not None and not future.done():
                future.set_result(payload)
            return

        if not self._accepting_messages:
            self._record_ignored()
            return

        try:
            self._event_queue.put_nowait(payload)
        except asyncio.QueueFull:
            self._stats["dropped"] += 1
            logger.error("QQ inbound event queue is full; event dropped")

    async def _event_worker(self) -> None:
        while self._active:
            raw = await self._event_queue.get()
            if not self._active:
                self._event_queue.task_done()
                return
            task = asyncio.create_task(self._process_event(raw), name="qq-event")
            self._event_tasks.add(task)
            task.add_done_callback(self._event_tasks.discard)

    async def _process_event(self, raw: dict[str, Any]) -> None:
        try:
            async with self._event_semaphore:
                await self._process_raw_message(raw)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_transport_error(exc)
            logger.error("QQ event handling failed (%s)", type(exc).__name__)
        finally:
            self._event_queue.task_done()

    async def _process_raw_message(self, raw: dict[str, Any]) -> None:
        if raw.get("post_type") != "message":
            self._record_ignored()
            return

        user_id = str(raw.get("user_id", ""))
        if not user_id or user_id == str(raw.get("self_id", "")):
            self._record_ignored()
            return

        is_group = raw.get("message_type") == "group"
        group_id = str(raw.get("group_id", "")) if is_group else None
        if self._allowed_users and user_id not in self._allowed_users:
            self._record_ignored()
            return
        if group_id and self._allowed_groups and group_id not in self._allowed_groups:
            self._record_ignored()
            return

        if self.voice_service is not None:
            self.voice_service.unload_if_idle()
        self_id = str(raw.get("self_id", "") or self._self_id or "")
        if self_id:
            self._self_id = self_id
        parsed = parse_message(raw, self_id=self_id)
        model_text = parsed.model_text
        attachments: list[Attachment] = []
        should_fetch_media = not is_group or (
            self._media_config.group_images and (parsed.mentioned_bot or bool(parsed.text))
        )
        if should_fetch_media and self.media_store:
            attachments = await self._download_media(parsed.media)
            expected = min(
                sum(1 for ref in parsed.media if ref.kind is MessageType.IMAGE),
                self._media_config.max_images_per_message,
            )
            received = sum(1 for attachment in attachments if attachment.kind is MessageType.IMAGE)
            self._metric("media_attachments", received)
            if expected > received:
                self._metric("media_fetch_failed", expected - received)
                model_text = f"{model_text} [图片未能识别]".strip()
            if self.sticker_library is not None and not is_group:
                for attachment in attachments:
                    if attachment.kind is MessageType.IMAGE:
                        try:
                            await self.sticker_library.collect(attachment.path)
                        except Exception as exc:
                            logger.debug("表情包自动收集失败 (%s)", type(exc).__name__)
            videos = [ref for ref in parsed.media if ref.kind is MessageType.VIDEO and ref.url]
            if videos:
                note = await self._process_video(videos[0], attachments)
                if note:
                    model_text = model_text.replace("[视频]", note, 1)

        transcript = ""
        if not is_group and self.voice_service is not None and self.voice_service.available:
            voices = [
                ref
                for ref in parsed.media
                if ref.kind is MessageType.VOICE and (ref.url or ref.file)
            ]
            if voices:
                attachment, transcript = await self._transcribe_voice(voices[0])
                if attachment is not None:
                    attachments.append(attachment)
                if transcript:
                    self._metric("voice_transcribed")
                    parsed.text = f"{transcript} {parsed.text}".strip()
                    model_text = model_text.replace("[语音]", f"[语音] {transcript}", 1)
        if not is_group and not transcript and any(
            ref.kind is MessageType.VOICE for ref in parsed.media
        ):
            self._metric("voice_transcribe_failed")
            model_text = model_text.replace("[语音]", "[语音消息，未能识别，请发文字]", 1)

        quoted: QuotedMessage | None = None
        quoted_bot = False
        if parsed.quote_id:
            quoted = await self._resolve_quote(parsed.quote_id)
            if quoted is not None:
                quoted_bot = bool(self_id and quoted.sender_id == self_id)
                label = quoted.sender_name or quoted.sender_id or "对方"
                model_text = f"[引用 {label}：{quoted.text or '消息'}] {model_text}".strip()
            else:
                model_text = f"[引用消息] {model_text}".strip()

        if is_group and not parsed.text and not parsed.mentioned_bot:
            self._record_ignored()
            return

        content = parsed.text
        if not content and any(ref.kind is MessageType.VOICE for ref in parsed.media):
            content = "[语音消息，未能识别]"
        if not content and parsed.quote_id:
            content = model_text
        if not content and not attachments and not parsed.media:
            self._record_ignored()
            return

        message_id = str(raw.get("message_id", ""))
        nickname = str(raw.get("sender", {}).get("nickname", ""))
        self._remember_message(
            message_id,
            QuotedMessage(message_id, user_id, nickname, parsed.text or model_text, True),
        )
        message_type = (
            MessageType.IMAGE
            if any(attachment.kind is MessageType.IMAGE for attachment in attachments)
            else MessageType.TEXT
        )
        await self.handle_callback(
            UnifiedMessage(
                platform=Platform.QQ,
                user_id=user_id,
                content=content,
                message_type=message_type,
                group_id=group_id,
                nickname=nickname,
                message_id=message_id,
                raw_data=raw,
                attachments=attachments,
                model_content=model_text,
                quoted=quoted,
                quoted_bot=quoted_bot,
            )
        )

    async def _transcribe_voice(self, ref: MediaRef) -> tuple[Attachment | None, str]:
        """Ask NapCat to convert SILK to WAV, then fall back to the raw URL."""
        if self.voice_service is None:
            return None, ""
        if ref.file:
            try:
                response = await self._call_action(
                    "get_record",
                    {"file": ref.file, "out_format": "wav"},
                )
                data = response.get("data") or {}
                encoded = str(data.get("base64") or "")
                if encoded.startswith("base64://"):
                    encoded = encoded[len("base64://") :]
                if encoded and len(encoded) <= 30 * 1024 * 1024:
                    try:
                        result = await self.voice_service.transcribe_bytes(
                            base64.b64decode(encoded)
                        )
                        if result[1] or result[0] is not None:
                            return result
                    except (ValueError, TypeError, binascii.Error):
                        pass
                url = str(data.get("url") or "")
                if url.startswith(("http://", "https://")):
                    result = await self.voice_service.transcribe_url(url)
                    if result[1] or result[0] is not None:
                        return result
            except Exception as exc:
                logger.debug("get_record 失败 (%s)", type(exc).__name__)
        if ref.url:
            return await self.voice_service.transcribe_url(ref.url)
        return None, ""

    async def _process_video(self, ref: MediaRef, attachments: list[Attachment]) -> str:
        if self.media_store is None:
            return "[视频，无法解析]"
        stored = await self.media_store.fetch_video(ref.url)
        if stored is None:
            return "[视频，下载失败]"
        attachments.append(stored)
        self._metric("media_video_received")
        analysis = await analyze_video(
            Path(stored.path),
            frames=self._media_config.video_frames,
            max_seconds=self._media_config.video_max_seconds,
        )
        if analysis is None:
            return "[视频，未能解析画面]"
        montage = await self.media_store.store_image(analysis.montage)
        if montage is not None:
            attachments.append(montage)
        duration = (
            f"{analysis.duration:.0f}秒"
            if analysis.duration < 60
            else f"{analysis.duration / 60:.1f}分钟"
        )
        return f"[视频 {duration}，已抽取 {analysis.frame_count} 张关键帧]"

    async def _download_media(self, refs: list[MediaRef]) -> list[Attachment]:
        if not self.media_store or not self._media_config.enabled:
            return []
        images = [
            ref
            for ref in refs
            if ref.kind is MessageType.IMAGE and (ref.url or ref.emoji_id or ref.file)
        ][: self._media_config.max_images_per_message]
        if not images:
            return []
        semaphore = asyncio.Semaphore(3)

        async def fetch(ref: MediaRef) -> Attachment | None:
            async with semaphore:
                is_sticker = bool(ref.emoji_id or ref.summary)
                attachment = None
                # qq-bridge prefers get_image for ordinary images and URLs for
                # stickers (CDN links are the reliable path for emoji).
                if not is_sticker and ref.file:
                    attachment = await self._fetch_image_via_action(ref.file)
                url = ref.url or emoji_cdn_url(ref.emoji_id)
                if attachment is None and url:
                    attachment = await self.media_store.fetch_image(url)
                if attachment is None and ref.emoji_id:
                    fallback = emoji_cdn_url(ref.emoji_id)
                    if fallback and fallback != url:
                        attachment = await self.media_store.fetch_image(fallback)
                if attachment is None and ref.file:
                    attachment = await self._fetch_image_via_action(ref.file)
                if attachment is not None and ref.file:
                    self._metric("media_get_image_fallback")
                return attachment

        results = await asyncio.gather(*(fetch(ref) for ref in images), return_exceptions=True)
        return [result for result in results if isinstance(result, Attachment)]

    async def _fetch_image_via_action(self, file_ref: str) -> Attachment | None:
        if not self.media_store or not file_ref:
            return None
        try:
            response = await self._call_action("get_image", {"file": file_ref})
        except Exception as exc:
            logger.debug("get_image 失败 (%s)", type(exc).__name__)
            return None
        data = response.get("data") or {}
        if not isinstance(data, dict):
            return None
        url = str(data.get("url", "") or "")
        if url.startswith(("http://", "https://")):
            attachment = await self.media_store.fetch_image(url)
            if attachment is not None:
                return attachment
        payload = str(data.get("base64", "") or "")
        if payload.startswith("base64://"):
            payload = payload[len("base64://") :]
        if payload and len(payload) <= 12 * 1024 * 1024:
            try:
                attachment = await self.media_store.store_image(base64.b64decode(payload))
                if attachment is not None:
                    return attachment
            except (ValueError, TypeError, binascii.Error):
                pass
        local = str(data.get("file", "") or "")
        if local:
            # Only read paths inside the media store; never arbitrary host paths
            # returned by the protocol side.
            path = validate_local_file(self.media_store.config.storage_dir, local)
            if path is not None:
                try:
                    return await self.media_store.store_image(path.read_bytes())
                except OSError:
                    pass
        return None

    async def _resolve_quote(self, quote_id: str) -> QuotedMessage | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", quote_id):
            return None
        cached = self._quote_cache.get(quote_id)
        if cached is not None:
            self._quote_cache.move_to_end(quote_id)
            return cached
        if quote_id in self._quote_misses:
            return None
        try:
            response = await self._call_action(
                "get_msg",
                {"message_id": int(quote_id) if quote_id.isdigit() else quote_id},
            )
        except Exception:
            self._remember_quote_miss(quote_id)
            return None
        payload = response.get("data") or {}
        if not isinstance(payload, dict):
            self._remember_quote_miss(quote_id)
            return None
        sender = payload.get("sender") or {}
        text = parse_message(payload, self_id=self._self_id).model_text[:200]
        quoted = QuotedMessage(
            message_id=quote_id,
            sender_id=str(sender.get("user_id", "") or ""),
            sender_name=str(sender.get("card") or sender.get("nickname") or ""),
            text=text,
            resolved=bool(text),
        )
        self._remember_message(quote_id, quoted)
        return quoted

    def _remember_quote_miss(self, quote_id: str) -> None:
        if len(self._quote_misses) >= 256:
            self._quote_misses.clear()
        self._quote_misses.add(quote_id)

    def _remember_message(self, message_id: str, quoted: QuotedMessage) -> None:
        if not message_id:
            return
        self._quote_cache[message_id] = quoted
        self._quote_cache.move_to_end(message_id)
        while len(self._quote_cache) > self._quote_cache_size:
            self._quote_cache.popitem(last=False)

    async def deliver_reply(self, message: UnifiedMessage, plan: ReplyPlan) -> None:
        if message.group_id is not None or not plan.humanized:
            text = plan.text()
            if self.outbound_guard is not None:
                text = self.outbound_guard.filter(text)
            if text:
                await self.send_long_message(message.user_id, text, message.group_id)
            return
        async with self._delivery_lock:
            await self._deliver_humanized(message, plan)

    async def _deliver_humanized(self, message: UnifiedMessage, plan: ReplyPlan) -> None:
        principal = message.principal_key
        filtered = (self.outbound_guard.filter(bubble) for bubble in plan.bubbles) if self.outbound_guard else plan.bubbles
        bubbles = [bubble for bubble in filtered if bubble]
        voice_text = plan.voice_text
        if voice_text and self.outbound_guard is not None:
            voice_text = self.outbound_guard.filter(voice_text)
        voice_path = plan.voice_path
        if voice_text and not voice_path and self.voice_service is not None:
            voice_path = await self.voice_service.synthesize(voice_text) or ""
        if not bubbles and not plan.sticker_path and not voice_text:
            return

        threshold = self.delivery_gate.config.failure_stop_threshold if self.delivery_gate else 2
        sent_any = plan.delivered_bubbles > 0 or plan.sticker_sent or plan.voice_sent
        failures = 0
        last_error: Exception | None = None

        for index, bubble in enumerate(bubbles):
            if index < plan.delivered_bubbles:
                continue
            first = plan.delivered_bubbles == 0
            if self.delivery_gate and not self.delivery_gate.reserve(principal):
                remaining = "".join(bubbles[index:]).strip()
                if remaining:
                    try:
                        await self.send_message(message.user_id, remaining)
                        self._metric("outbound_text")
                        sent_any = True
                        plan.delivered_bubbles = len(bubbles)
                    except Exception as exc:
                        last_error = exc
                break
            segments: list[dict[str, Any]] = []
            if first and plan.reply_to:
                segments.append({"type": "reply", "data": {"id": plan.reply_to}})
            segments.append({"type": "text", "data": {"text": bubble}})
            try:
                await self._send_segments(message.user_id, segments)
                self._metric("outbound_text")
                sent_any = True
                plan.delivered_bubbles = index + 1
            except PlatformDeliveryError as exc:
                last_error = exc
                failures += 1
                if self.delivery_gate:
                    self.delivery_gate.rollback(principal)
                recovered = False
                if first and plan.reply_to and len(segments) > 1:
                    try:
                        await self._send_segments(message.user_id, segments[1:])
                        self._metric("outbound_text")
                        sent_any = True
                        plan.delivered_bubbles = index + 1
                        recovered = True
                    except PlatformDeliveryError as retry_exc:
                        last_error = retry_exc
                        failures += 1
                if not recovered or failures >= threshold:
                    break
            if index < len(bubbles) - 1:
                delay = plan.delays[index] if index < len(plan.delays) else 0.0
                if delay > 0:
                    await asyncio.sleep(delay)

        if plan.sticker_path and not plan.sticker_sent:
            if await self._deliver_sticker(
                message,
                plan,
                principal,
                reply_to=plan.reply_to if plan.delivered_bubbles == 0 else "",
            ):
                plan.sticker_sent = True
                sent_any = True

        if voice_text and not plan.voice_sent:
            if voice_path:
                if await self._deliver_voice(
                    message,
                    plan,
                    voice_text,
                    voice_path,
                    principal,
                    reply_to=plan.reply_to if plan.delivered_bubbles == 0 else "",
                ):
                    plan.voice_sent = True
                    sent_any = True
            else:
                try:
                    fallback_segments: list[dict[str, Any]] = []
                    if plan.reply_to and plan.delivered_bubbles == 0:
                        fallback_segments.append({"type": "reply", "data": {"id": plan.reply_to}})
                    fallback_segments.append({"type": "text", "data": {"text": voice_text}})
                    await self._send_segments(message.user_id, fallback_segments)
                    self._metric("voice_fallback")
                    plan.voice_sent = True
                    sent_any = True
                except Exception as exc:
                    last_error = exc

        if not sent_any and (
            last_error is not None
            or plan.sticker_path
            or (voice_text and not plan.voice_sent)
        ):
            raise last_error or PlatformDeliveryError("表情包或语音发送失败")

    async def _deliver_sticker(
        self,
        message: UnifiedMessage,
        plan: ReplyPlan,
        principal: str,
        *,
        reply_to: str = "",
    ) -> bool:
        url = self._outbound_file(plan.sticker_path)
        if not url:
            return False
        if self.delivery_gate and not self.delivery_gate.reserve(principal):
            return False
        segments: list[dict[str, Any]] = []
        if reply_to:
            segments.append({"type": "reply", "data": {"id": reply_to}})
        segments.append({"type": "image", "data": {"file": url}})
        try:
            await self._send_segments(message.user_id, segments)
            self._metric("stickers_sent")
            if self.sticker_library is not None and plan.sticker_hash:
                await self.sticker_library.mark_used_hash(plan.sticker_hash)
            return True
        except PlatformDeliveryError:
            if self.delivery_gate:
                self.delivery_gate.rollback(principal)
            logger.debug("表情包发送失败")
            return False

    async def _deliver_voice(
        self,
        message: UnifiedMessage,
        plan: ReplyPlan,
        voice_text: str,
        voice_path: str,
        principal: str,
        *,
        reply_to: str = "",
    ) -> bool:
        url = self._outbound_file(voice_path)
        if not url or (self.delivery_gate and not self.delivery_gate.reserve(principal, voice=True)):
            try:
                await self.send_message(message.user_id, voice_text)
                return True
            except Exception:
                return False
        segments: list[dict[str, Any]] = []
        if reply_to:
            segments.append({"type": "reply", "data": {"id": reply_to}})
        segments.append({"type": "record", "data": {"file": url}})
        try:
            await self._send_segments(message.user_id, segments)
            self._metric("voice_sent")
            return True
        except PlatformDeliveryError:
            if self.delivery_gate:
                self.delivery_gate.rollback(principal, voice=True)
            try:
                await self.send_message(message.user_id, voice_text)
                self._metric("voice_fallback")
                return True
            except Exception:
                return False

    def _outbound_file(self, path: str) -> str:
        try:
            resolved = Path(path).resolve()
        except (OSError, RuntimeError, ValueError):
            return ""
        roots: list[Path] = []
        if self.sticker_library is not None:
            roots.append(Path(self.sticker_library.root).resolve())
        if self.media_store is not None:
            roots.append(Path(self.media_store.config.storage_dir).resolve())
        if not any(resolved == root or root in resolved.parents for root in roots):
            return ""
        if not resolved.is_file():
            return ""
        target_dir = (
            Path(self.media_store.config.outbound_dir) if self.media_store else resolved.parent
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
        target = target_dir / f"{digest}{resolved.suffix}"
        if not target.exists():
            shutil.copyfile(resolved, target)
        return target.resolve().as_uri()

    async def _send_segments(
        self,
        user_id: str,
        segments: list[dict[str, Any]],
        group_id: str | None = None,
    ) -> DeliveryReceipt:
        if group_id:
            action = "send_group_msg"
            params: dict[str, Any] = {
                "group_id": int(group_id) if group_id.isdigit() else group_id,
                "message": segments,
            }
            target_id = group_id
        else:
            action = "send_private_msg"
            params = {"user_id": int(user_id) if user_id.isdigit() else user_id, "message": segments}
            target_id = user_id
        response = await self._call_action(action, params)
        data = response.get("data") or {}
        receipt = DeliveryReceipt(
            platform=Platform.QQ,
            target_id=target_id,
            message_id=str(data.get("message_id", "")),
        )
        text = "".join(
            str(segment.get("data", {}).get("text", ""))
            for segment in segments
            if segment.get("type") == "text"
        )
        self._remember_message(
            receipt.message_id,
            QuotedMessage(receipt.message_id, self._self_id, "机器人", text[:200], True),
        )
        return receipt

    async def _send_message(
        self,
        user_id: str,
        content: str,
        group_id: str | None = None,
    ) -> DeliveryReceipt:
        if group_id:
            action = "send_group_msg"
            params: dict[str, Any] = {"group_id": int(group_id) if group_id.isdigit() else group_id, "message": content}
            target_id = group_id
        else:
            action = "send_private_msg"
            params = {"user_id": int(user_id) if user_id.isdigit() else user_id, "message": content}
            target_id = user_id

        response = await self._call_action(action, params)
        data = response.get("data") or {}
        receipt = DeliveryReceipt(
            platform=Platform.QQ,
            target_id=target_id,
            message_id=str(data.get("message_id", "")),
        )
        self._remember_message(
            receipt.message_id,
            QuotedMessage(receipt.message_id, self._self_id, "机器人", content[:200], True),
        )
        return receipt

    @property
    def self_id(self) -> str:
        return self._self_id

    async def call_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self._call_action(action, params)

    async def set_avatar(self, path: str) -> None:
        """Set the bot's QQ avatar from a file inside the shared media directory."""
        resolved = validate_local_file(self.media_store.config.storage_dir, path) if self.media_store else None
        if resolved is None:
            raise PlatformDeliveryError("头像文件不在媒体目录内", code="invalid_path")
        data = await asyncio.to_thread(resolved.read_bytes)
        if len(data) > 5 * 1024 * 1024:
            raise PlatformDeliveryError("头像图片超过 5MB", code="too_large")
        if not sniff_image_mime(data):
            raise PlatformDeliveryError("文件不是支持的图片格式", code="invalid_format")
        await self._call_action("set_qq_avatar", {"file": str(resolved)})

    async def _call_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        websocket = self._websocket
        if websocket is None or not self._connected.is_set():
            raise PlatformNotReadyError("OneBot WebSocket is disconnected")

        echo = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending_actions[echo] = future
        try:
            payload = json.dumps({"action": action, "params": params, "echo": echo}, ensure_ascii=False)
            async with self._send_lock:
                await websocket.send(payload)
            response = await asyncio.wait_for(future, timeout=self._api_timeout)
        except TimeoutError as exc:
            raise PlatformDeliveryError("OneBot action timed out", code="timeout") from exc
        except PlatformDeliveryError:
            raise
        except Exception as exc:
            raise PlatformDeliveryError("OneBot action transport failed", code=type(exc).__name__) from exc
        finally:
            self._pending_actions.pop(echo, None)

        retcode = response.get("retcode")
        if response.get("status") != "ok" or retcode not in {0, None}:
            raise PlatformDeliveryError("OneBot action was rejected", code=retcode)
        return response

    def _fail_pending_actions(self, error: Exception) -> None:
        pending = list(self._pending_actions.values())
        self._pending_actions.clear()
        for future in pending:
            if not future.done():
                future.set_exception(error)

    def status_snapshot(self) -> dict[str, Any]:
        snapshot = super().status_snapshot()
        snapshot.update({"connected": self._connected.is_set(), "queue_depth": self._event_queue.qsize()})
        return snapshot
