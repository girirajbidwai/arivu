"""
Google Cloud Speech-to-Text v2 (Chirp 2) — streaming.
Google Cloud Text-to-Speech (Chirp 3 HD) — streaming via long-poll.

Both run in `asia-south1` (Mumbai) for sub-50ms RTT inside India. Auth
flows through standard ADC (`GOOGLE_APPLICATION_CREDENTIALS` JSON path
or in-cluster metadata server on Cloud Run).

This module imports the SDKs lazily so the voice service still boots
without `google-cloud-speech` / `google-cloud-texttospeech` installed.
The factory falls back to Sarvam when imports or creds fail.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import AsyncIterator, Optional

from .base import AudioChunk, STTFinal, STTPartial

logger = logging.getLogger("arivu.speech.google")

DEFAULT_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
DEFAULT_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "asia-south1")


def _has_creds() -> bool:
    return bool(DEFAULT_PROJECT) and (
        bool(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
        or bool(os.getenv("K_SERVICE"))  # Cloud Run
    )


# ── STT — Google Cloud Speech v2 streaming ─────────────────────────────
class GoogleSTT:
    """
    Streaming STT via the v2 RecognizeStream API. Yields partials every
    100–200 ms, finals on EOU. Indian English / Kannada / Hindi natively.
    """

    name = "google"

    def __init__(self) -> None:
        if not _has_creds():
            raise RuntimeError("Google ADC missing — Google STT not available")
        try:
            from google.cloud.speech_v2 import SpeechAsyncClient  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "google-cloud-speech not installed; "
                "run `uv pip install google-cloud-speech`"
            ) from e
        self._client = None
        self._stream = None
        self._language_codes: list[str] = ["kn-IN", "hi-IN", "en-IN"]
        self._sample_rate = 16000
        self._queue: asyncio.Queue = asyncio.Queue()
        self._recv_task: Optional[asyncio.Task] = None
        self._send_queue: asyncio.Queue = asyncio.Queue()

    async def open(self, *, language_code: str = "auto", sample_rate: int = 16000) -> None:
        if not _has_creds():
            raise RuntimeError("Google ADC missing — cannot open Google STT")
        try:
            from google.cloud.speech_v2 import SpeechAsyncClient
        except ImportError as e:
            raise RuntimeError(
                "google-cloud-speech not installed; "
                "run `uv pip install google-cloud-speech`"
            ) from e
        self._client = SpeechAsyncClient()
        self._sample_rate = sample_rate
        if language_code and language_code not in ("auto", "unknown"):
            self._language_codes = [language_code]
        # The bidi stream is opened lazily on the first feed() — Google
        # requires the first request to carry the StreamingRecognitionConfig.
        logger.info("google STT ready (region=%s, langs=%s)",
                    DEFAULT_LOCATION, self._language_codes)

    async def feed(self, pcm: bytes) -> None:
        # Producer: enqueue the PCM frame; the bidi sender consumes it.
        await self._send_queue.put(pcm)
        if self._stream is None and self._client is not None:
            self._stream = asyncio.create_task(self._run_stream())

    async def _run_stream(self) -> None:
        """One-shot: open the bidi stream, send config, then forward audio."""
        from google.cloud.speech_v2 import types  # type: ignore

        recognizer = (
            f"projects/{DEFAULT_PROJECT}/locations/{DEFAULT_LOCATION}/"
            f"recognizers/_"  # `_` = inline config
        )
        config = types.RecognitionConfig(
            explicit_decoding_config=types.ExplicitDecodingConfig(
                encoding=types.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=self._sample_rate,
                audio_channel_count=1,
            ),
            language_codes=self._language_codes,
            model="chirp_2",
        )
        streaming_config = types.StreamingRecognitionConfig(
            config=config,
            streaming_features=types.StreamingRecognitionFeatures(
                interim_results=True,
            ),
        )

        async def _requests():
            yield types.StreamingRecognizeRequest(
                recognizer=recognizer,
                streaming_config=streaming_config,
            )
            while True:
                pcm = await self._send_queue.get()
                if pcm is None:
                    return
                yield types.StreamingRecognizeRequest(audio=pcm)

        t0 = time.perf_counter()
        async for resp in await self._client.streaming_recognize(requests=_requests()):
            for result in resp.results or []:
                alt = result.alternatives[0] if result.alternatives else None
                if not alt:
                    continue
                elapsed = (time.perf_counter() - t0) * 1000
                if result.is_final:
                    await self._queue.put(STTFinal(
                        transcript=alt.transcript,
                        language=getattr(result, "language_code", "") or "",
                        elapsed_ms=elapsed,
                        confidence=getattr(alt, "confidence", 0.0) or 0.0,
                    ))
                else:
                    await self._queue.put(STTPartial(
                        transcript=alt.transcript,
                        language=getattr(result, "language_code", "") or "",
                        stable=getattr(result, "stability", 0.0) > 0.7,
                        elapsed_ms=elapsed,
                    ))

    async def events(self) -> AsyncIterator[STTPartial | STTFinal]:
        while True:
            evt = await self._queue.get()
            yield evt

    async def close(self) -> None:
        await self._send_queue.put(None)
        if self._stream:
            try:
                await asyncio.wait_for(self._stream, timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                self._stream.cancel()
        self._stream = None
        self._client = None


# ── TTS — Google Cloud Text-to-Speech (Chirp 3 HD) ─────────────────────
class GoogleTTS:
    """
    Streaming TTS via the v1 LongAudioSynthesize endpoint isn't truly
    chunked; for low first-byte latency we use the standard synthesize
    with `Chirp 3 HD` voices and yield the WAV in one chunk. ~250–350 ms
    first byte for short verify phrases.

    For real chunked streaming, prefer Sarvam Bulbul. This adapter is the
    default-fallback when Bulbul is unreachable.
    """

    name = "google"

    def __init__(self, voice_kn: str = "kn-IN-Chirp3-HD-Aoede") -> None:
        if not _has_creds():
            raise RuntimeError("Google ADC missing — Google TTS not available")
        try:
            from google.cloud import texttospeech_v1  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "google-cloud-texttospeech not installed; "
                "run `uv pip install google-cloud-texttospeech`"
            ) from e
        self._voice_kn = voice_kn

    async def synthesize_stream(
        self,
        text: str,
        *,
        target_language_code: str = "kn-IN",
        speaker: Optional[str] = None,
    ) -> AsyncIterator[AudioChunk]:
        if not _has_creds():
            raise RuntimeError("Google ADC missing — cannot synthesize")
        if not text.strip():
            return
        try:
            from google.cloud import texttospeech_v1 as tts
        except ImportError as e:
            raise RuntimeError(
                "google-cloud-texttospeech not installed; "
                "run `uv pip install google-cloud-texttospeech`"
            ) from e

        client = tts.TextToSpeechAsyncClient()
        voice = tts.VoiceSelectionParams(
            language_code=target_language_code,
            name=speaker or self._voice_kn,
        )
        audio_config = tts.AudioConfig(
            audio_encoding=tts.AudioEncoding.LINEAR16,
            sample_rate_hertz=22050,
        )
        t0 = time.perf_counter()
        resp = await client.synthesize_speech(
            request=tts.SynthesizeSpeechRequest(
                input=tts.SynthesisInput(text=text),
                voice=voice,
                audio_config=audio_config,
            )
        )
        elapsed = (time.perf_counter() - t0) * 1000
        logger.info("google TTS first byte in %.0fms (%d bytes)",
                    elapsed, len(resp.audio_content))
        # Single-chunk yield — caller can split for latency.
        yield AudioChunk(pcm=resp.audio_content, sample_rate=22050, elapsed_ms=elapsed)

    async def close(self) -> None:
        return None
