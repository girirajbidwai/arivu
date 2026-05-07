"""
Sarvam streaming STT (Saaras v3) + TTS (Bulbul v3) via the official
`sarvamai` async SDK.

Why Sarvam Bulbul stays primary for TTS even after we add Gemini for the
brain: Bulbul preserves Kannada dialect register and Indic phonemes better
than any non-Indic vendor today. Speed is competitive (~250 ms first byte
on the v3 streaming endpoint), so we don't sacrifice latency.

Saaras streaming is here too, but Google STT v2 in Mumbai is faster on
partial-result latency and has better punctuation. The factory prefers
Google when GCP creds are present; this module is the always-available
Indic fallback.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import AsyncIterator, Optional

from .base import AudioChunk, STTFinal, STTPartial

logger = logging.getLogger("arivu.speech.sarvam")


def _api_key() -> str:
    return os.getenv("SARVAM_API_KEY", "")


# ── STT ─────────────────────────────────────────────────────────────────
class SarvamSTT:
    name = "sarvam"

    def __init__(self) -> None:
        self._client = None
        self._session = None
        self._cm = None  # context-manager for the async with
        self._language_code = "unknown"
        self._sample_rate = 16000
        self._open_at = 0.0
        # Receive loop: events() yields from this queue.
        self._queue: asyncio.Queue = asyncio.Queue()
        self._recv_task: Optional[asyncio.Task] = None

    async def open(self, *, language_code: str = "auto", sample_rate: int = 16000) -> None:
        if not _api_key():
            raise RuntimeError("SARVAM_API_KEY missing — cannot open Sarvam STT")
        from sarvamai import AsyncSarvamAI  # lazy import

        self._client = AsyncSarvamAI(api_subscription_key=_api_key())
        # Sarvam treats "unknown" as auto-detect.
        self._language_code = (
            language_code if language_code not in ("auto", "") else "unknown"
        )
        self._sample_rate = sample_rate
        self._cm = self._client.speech_to_text_streaming.connect(
            model="saaras:v3",
            mode="transcribe",
            language_code=self._language_code,
        )
        self._session = await self._cm.__aenter__()
        self._open_at = time.perf_counter()
        # Start receive loop in the background.
        self._recv_task = asyncio.create_task(self._recv_loop())
        logger.info("sarvam STT open (lang=%s, sr=%d)", self._language_code, sample_rate)

    async def feed(self, pcm: bytes) -> None:
        if not self._session:
            return
        # Sarvam SDK expects raw PCM bytes; the wrapper handles base64+protocol.
        try:
            await self._session.transcribe(audio=pcm)
        except Exception as e:
            logger.warning("sarvam STT feed failed (non-fatal): %s", e)

    async def _recv_loop(self) -> None:
        try:
            while True:
                resp = await self._session.recv()
                elapsed = (time.perf_counter() - self._open_at) * 1000
                transcript = ""
                lang = self._language_code
                is_final = True

                # The SDK returns either dict or pydantic-ish objects.
                if hasattr(resp, "transcript"):
                    transcript = resp.transcript or ""
                    if hasattr(resp, "language_code") and resp.language_code:
                        lang = resp.language_code
                    if hasattr(resp, "is_final"):
                        is_final = bool(resp.is_final)
                elif isinstance(resp, dict):
                    transcript = resp.get("transcript", "") or ""
                    lang = resp.get("language_code", lang) or lang
                    is_final = bool(resp.get("is_final", True))

                if not transcript:
                    continue

                if is_final:
                    await self._queue.put(STTFinal(
                        transcript=transcript, language=lang,
                        elapsed_ms=elapsed, confidence=0.85,
                    ))
                else:
                    await self._queue.put(STTPartial(
                        transcript=transcript, language=lang,
                        stable=False, elapsed_ms=elapsed,
                    ))
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.debug("sarvam STT recv loop ended: %s", e)
            return

    async def events(self) -> AsyncIterator[STTPartial | STTFinal]:
        while True:
            evt = await self._queue.get()
            yield evt

    async def close(self) -> None:
        if self._recv_task:
            self._recv_task.cancel()
        if self._cm:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
        self._cm = None
        self._session = None
        self._client = None


# ── TTS ─────────────────────────────────────────────────────────────────
class SarvamTTS:
    name = "sarvam"

    # bulbul:v3 has its own voice catalogue; `anushka` is v2-only. We pick
    # `pooja` as the default (clear Indic prosody, compatible with v3).
    # Override via SARVAM_TTS_SPEAKER env if you want a different timbre.
    def __init__(
        self,
        speaker: str = os.getenv("SARVAM_TTS_SPEAKER", "pooja"),
        model: str = os.getenv("SARVAM_TTS_MODEL", "bulbul:v3"),
    ) -> None:
        self._speaker = speaker
        self._model = model

    async def synthesize_stream(
        self,
        text: str,
        *,
        target_language_code: str = "kn-IN",
        speaker: Optional[str] = None,
    ) -> AsyncIterator[AudioChunk]:
        if not _api_key():
            raise RuntimeError("SARVAM_API_KEY missing — cannot stream TTS")
        if not text or not text.strip():
            return

        from sarvamai import AsyncSarvamAI  # lazy import
        client = AsyncSarvamAI(api_subscription_key=_api_key())
        t0 = time.perf_counter()
        chunks_yielded = 0

        async with client.text_to_speech_streaming.connect(model=self._model) as tts_ws:
            await tts_ws.configure(
                target_language_code=target_language_code,
                speaker=speaker or self._speaker,
            )
            await tts_ws.convert(text)
            await tts_ws.flush()

            sample_rate = 24000 if self._model.endswith("v3") else 22050
            # Pull frames explicitly. We close on:
            #   * a completion / error event from the SDK
            #   * the websocket being closed by the server
            #   * a short idle gap after we've already seen audio.
            idle_timeout = 2.0  # generous before first byte
            while True:
                try:
                    evt = await asyncio.wait_for(tts_ws.recv(), timeout=idle_timeout)
                except asyncio.TimeoutError:
                    if chunks_yielded > 0:
                        logger.debug("sarvam TTS idle gap — closing cleanly")
                    else:
                        logger.warning("sarvam TTS idle %.1fs without audio — closing", idle_timeout)
                    break
                except Exception as e:
                    # Connection closed by server is normal end-of-stream.
                    logger.debug("sarvam TTS recv ended: %s", e)
                    break

                if evt is None or _is_completion_event(evt):
                    break

                pcm = _extract_pcm(evt)
                if not pcm:
                    continue
                elapsed = (time.perf_counter() - t0) * 1000
                if chunks_yielded == 0:
                    logger.info("sarvam TTS first byte in %.0fms", elapsed)
                    idle_timeout = 1.5  # Bug #6 fix: Indic synthesis has natural 300-800ms gaps
                chunks_yielded += 1
                yield AudioChunk(
                    pcm=pcm,
                    sample_rate=sample_rate,
                    mime=_evt_mime(evt, "audio/mpeg"),
                    elapsed_ms=elapsed,
                )

        logger.info(
            "sarvam TTS done — %d chunks, total %.0fms",
            chunks_yielded, (time.perf_counter() - t0) * 1000,
        )

    async def close(self) -> None:
        return None


# ── helpers ────────────────────────────────────────────────────────────
def _extract_pcm(evt) -> bytes:
    """
    Decode Sarvam-SDK TTS event payload to raw bytes.

    The SDK shapes audio as `AudioOutput(type='audio', data=AudioOutputData(
    content_type='audio/mpeg', audio='<base64>'))`. We also tolerate the
    legacy shapes where `audio` lives on the event directly or as a dict.
    """
    import base64

    if isinstance(evt, (bytes, bytearray)):
        return bytes(evt)

    # New shape: evt.data.audio
    data = getattr(evt, "data", None)
    if data is not None and hasattr(data, "audio"):
        a = data.audio
        if isinstance(a, (bytes, bytearray)):
            return bytes(a)
        if isinstance(a, str):
            try:
                return base64.b64decode(a)
            except Exception:
                return b""

    # Legacy: evt.audio
    if hasattr(evt, "audio"):
        a = evt.audio
        if isinstance(a, (bytes, bytearray)):
            return bytes(a)
        if isinstance(a, str):
            try:
                return base64.b64decode(a)
            except Exception:
                return b""

    if isinstance(evt, dict):
        a = (evt.get("data") or {}).get("audio") if isinstance(evt.get("data"), dict) else None
        a = a or evt.get("audio")
        if isinstance(a, (bytes, bytearray)):
            return bytes(a)
        if isinstance(a, str):
            try:
                return base64.b64decode(a)
            except Exception:
                return b""
    return b""


def _evt_mime(evt, default: str = "audio/mpeg") -> str:
    """Pull content-type off an AudioOutput event (default = MP3)."""
    data = getattr(evt, "data", None)
    if data is not None and hasattr(data, "content_type") and data.content_type:
        return str(data.content_type)
    if isinstance(evt, dict):
        d = evt.get("data") or {}
        if isinstance(d, dict) and d.get("content_type"):
            return str(d["content_type"])
    return default


def _is_completion_event(evt) -> bool:
    """Detect SDK-emitted end-of-stream events without coupling to types."""
    type_attr = getattr(evt, "type", None) or getattr(evt, "event_type", None)
    if isinstance(type_attr, str) and type_attr.lower() in ("completion", "end", "done", "stop"):
        return True
    # Error events terminate the stream too — caller already saw the error
    # via logs; we just stop reading.
    if isinstance(type_attr, str) and type_attr.lower() == "error":
        return True
    if isinstance(evt, dict):
        et = (evt.get("type") or evt.get("event_type") or "").lower()
        if et in ("completion", "end", "done", "stop", "error"):
            return True
    return False
