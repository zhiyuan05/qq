"""Optional SILK decoding, local ASR and Edge TTS."""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from media.net import MediaFetchError, fetch_bytes
from platforms.models import Attachment, MessageType
from utils.config import MediaConfig, VoiceConfig
from utils.logger import setup_logger

logger = setup_logger("media.voice")

_VOICE_NAME = re.compile(r"^[A-Za-z]{2,3}-[A-Za-z0-9-]+Neural$")
_VOICE_RATE = re.compile(r"^[+-]\d{1,3}%$")
_VOICE_PITCH = re.compile(r"^[+-]\d{1,3}Hz$")


class VoiceService:
    """ASR/TTS facade; all heavy imports stay lazy so the base image can skip them."""

    def __init__(self, config: VoiceConfig, media: MediaConfig):
        self.config = config
        self.media = media
        self.available = False
        self._asr = None
        self._semaphore = asyncio.Semaphore(1)
        self._last_used = 0.0
        self.asr_provider = config.asr_provider.strip().lower().replace("-", "_")
        self.tts_provider = config.tts_provider.strip().lower().replace("-", "_")
        if not config.enabled:
            return
        tts_dir = Path(config.tts_dir).resolve()
        storage = Path(media.storage_dir).resolve()
        if tts_dir != storage and storage not in tts_dir.parents:
            logger.warning("voice.tts_dir 必须位于 media.storage_dir 内；语音功能已禁用")
            return
        if self.asr_provider != "faster_whisper":
            logger.warning("不支持的 ASR provider: %s；语音功能已禁用", config.asr_provider)
            return
        if self.tts_provider != "edge_tts":
            logger.warning("不支持的 TTS provider: %s；语音功能已禁用", config.tts_provider)
            return
        try:
            import edge_tts  # noqa: F401
            import faster_whisper  # noqa: F401

            self.available = True
            logger.info("语音服务可用（ASR: faster_whisper/%s，TTS: edge_tts）", config.asr_model)
        except ImportError:
            logger.warning("语音依赖未安装；语音功能已禁用")

    async def transcribe_url(self, url: str) -> tuple[Attachment | None, str]:
        if not self.available or not url:
            return None, ""
        try:
            result = await fetch_bytes(
                url,
                max_bytes=max(self.media.max_fetch_bytes, 20 * 1024 * 1024),
                total_timeout=self.media.fetch_total_timeout,
                attempt_timeout=self.media.fetch_attempt_timeout,
                max_redirects=self.media.max_redirects,
            )
        except MediaFetchError as exc:
            logger.info("语音下载失败: %s", exc)
            return None, ""
        return await self.transcribe_bytes(result.content)

    async def transcribe_bytes(self, data: bytes) -> tuple[Attachment | None, str]:
        """Transcribe already-downloaded audio (WAV/MP3/AMR; SILK needs conversion)."""
        if not self.available or not data:
            return None, ""
        wav_path = await asyncio.to_thread(self._decode_to_wav, data)
        if wav_path is None:
            return None, ""
        duration = _wav_duration(wav_path)
        if duration > self.config.max_audio_seconds:
            logger.info("语音超过时长上限: %.1fs", duration)
            try:
                wav_path.unlink()
            except OSError:
                pass
            return None, ""
        async with self._semaphore:
            transcript = await self._transcribe(wav_path)
        try:
            stored = wav_path.read_bytes()
        except OSError:
            stored = b""
        try:
            wav_path.unlink()
        except OSError:
            pass
        attachment = None
        if stored:
            attachment = await asyncio.to_thread(
                self._store_audio,
                stored,
                duration,
                transcript,
            )
        return attachment, transcript

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        rate: str = "+0%",
        pitch: str = "+0Hz",
    ) -> str | None:
        if not self.available or not text or self.tts_provider != "edge_tts":
            return None
        from edge_tts import Communicate

        voice_name = voice if _VOICE_NAME.fullmatch(voice or "") else self.config.tts_voice
        rate_value = rate if _VOICE_RATE.fullmatch(rate or "") else "+0%"
        pitch_value = pitch if _VOICE_PITCH.fullmatch(pitch or "") else "+0Hz"

        target_dir = Path(self.config.tts_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"tts-{uuid.uuid4().hex}.mp3"
        try:
            await asyncio.wait_for(
                Communicate(
                    text,
                    voice_name,
                    rate=rate_value,
                    pitch=pitch_value,
                ).save(str(target)),
                timeout=self.config.tts_timeout_seconds,
            )
        except Exception as exc:
            logger.info("TTS 合成失败 (%s)", type(exc).__name__)
            return None

        # NapCat estimates mp3 duration from bitrate before silk conversion,
        # which can produce a voice QQ clients refuse to play. Converting to
        # WAV first gives the encoder an exact duration header.
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            wav_path = target.with_suffix(".wav")
            try:
                subprocess.run(
                    [
                        ffmpeg,
                        "-y",
                        "-loglevel",
                        "error",
                        "-i",
                        str(target),
                        "-ar",
                        "24000",
                        "-ac",
                        "1",
                        str(wav_path),
                    ],
                    check=True,
                    timeout=20,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if wav_path.exists() and wav_path.stat().st_size > 0:
                    try:
                        target.unlink()
                    except OSError:
                        pass
                    return str(wav_path)
            except (subprocess.SubprocessError, OSError):
                pass
        return str(target)

    def _store_audio(self, data: bytes, duration: float, transcript: str) -> Attachment | None:
        digest = hashlib.sha256(data).hexdigest()
        target_dir = Path(self.media.storage_dir) / "voice"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{digest[:32]}.wav"
        if not target.exists():
            temp = target.with_suffix(".wav.tmp")
            temp.write_bytes(data)
            os.replace(temp, target)
        return Attachment(
            kind=MessageType.VOICE,
            path=str(target),
            mime="audio/wav",
            size=len(data),
            sha256=digest,
            duration=duration,
            transcript=transcript,
        )

    def _decode_to_wav(self, data: bytes) -> Path | None:
        if data.startswith(b"RIFF"):
            target = Path(self.media.storage_dir) / "voice" / f"{uuid.uuid4().hex}.wav"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return target
        source = Path(self.media.storage_dir) / "voice" / f"{uuid.uuid4().hex}.silk"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(data)
        target = source.with_suffix(".wav")
        try:
            import pilk

            pilk.decode(str(source), str(target))
            if target.exists():
                try:
                    source.unlink()
                except OSError:
                    pass
                return target
        except Exception:
            pass
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            try:
                subprocess.run(
                    [ffmpeg, "-y", "-loglevel", "error", "-i", str(source), "-ar", "16000", "-ac", "1", str(target)],
                    check=True,
                    timeout=30,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if target.exists():
                    try:
                        source.unlink()
                    except OSError:
                        pass
                    return target
            except (subprocess.SubprocessError, OSError):
                pass
        try:
            source.unlink()
        except OSError:
            pass
        return None

    async def _transcribe(self, wav_path: Path) -> str:
        if self.asr_provider != "faster_whisper":
            return ""
        self.unload_if_idle()
        if self._asr is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                return ""
            try:
                self._asr = await asyncio.to_thread(
                    WhisperModel,
                    self.config.asr_model,
                    device="cpu",
                    compute_type="int8",
                    download_root=os.getenv("WHISPER_CACHE_DIR") or None,
                )
                self._last_used = time.monotonic()
            except Exception as exc:
                logger.warning("ASR 模型加载失败 (%s)", type(exc).__name__)
                return ""
        try:
            def _run() -> str:
                segments, _info = self._asr.transcribe(
                    str(wav_path),
                    language="zh",
                    vad_filter=True,
                    initial_prompt="以下是简体中文普通话的句子。",
                )
                return "".join(segment.text for segment in segments).strip()

            text = await asyncio.to_thread(_run)
        except Exception as exc:
            logger.info("ASR 转写失败 (%s)", type(exc).__name__)
            return ""
        self._last_used = time.monotonic()
        self.unload_if_idle()
        return text[: self.config.max_audio_seconds * 10]

    def unload_if_idle(self) -> None:
        """Release the ASR model after the configured idle period."""

        if self._asr is None or not self.config.unload_after_seconds:
            return
        if time.monotonic() - self._last_used >= self.config.unload_after_seconds:
            self._asr = None
            logger.info("ASR 模型空闲超时，已释放")


def _wav_duration(path: Path) -> float:
    try:
        import wave

        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate() or 16000
            return round(frames / rate, 2)
    except Exception:
        return 0.0
