"""
Streaming speech protocols.

These are the only types the WebRTC pipeline depends on. Implementations
live in `sarvam_streaming.py`, `google_streaming.py`, etc. Each one is
selectable by an env var; each one returns the same yields. That means:

  * Hot-swap providers without touching the pipeline.
  * Mock the protocol for tests with a tiny in-memory generator.
  * Add Google / ElevenLabs / Deepgram as a 100-line file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Optional, Protocol, runtime_checkable


@dataclass
class STTPartial:
    """An interim transcript that may still change."""
    transcript: str
    language: str = ""
    stable: bool = False  # True when this prefix is unlikely to change
    elapsed_ms: float = 0.0


@dataclass
class STTFinal:
    """The final, locked-in transcript for an utterance."""
    transcript: str
    language: str = ""
    elapsed_ms: float = 0.0
    confidence: float = 0.0


@dataclass
class AudioChunk:
    """
    One audio frame headed for the speaker. `pcm` is the raw payload — name
    kept for backwards compatibility, but the actual codec is whatever
    `mime` says. Browsers play MP3 / WAV / Opus / PCM (decoded via Web
    Audio's decodeAudioData) regardless of source.
    """
    pcm: bytes
    sample_rate: int = 22050
    channels: int = 1
    sample_width: int = 2          # ignored for compressed codecs
    mime: str = "audio/mpeg"       # Sarvam Bulbul ws ships base64 MP3
    elapsed_ms: float = 0.0


@runtime_checkable
class STTProvider(Protocol):
    """
    Open one streaming STT session per call. Caller pushes raw PCM frames
    via `feed`, and reads STTPartial / STTFinal events from the async
    iterator returned by `events`. Closing the session flushes the final.
    """

    name: str

    async def open(
        self,
        *,
        language_code: str = "auto",
        sample_rate: int = 16000,
    ) -> None: ...

    async def feed(self, pcm: bytes) -> None: ...

    def events(self) -> AsyncIterator[STTPartial | STTFinal]: ...

    async def close(self) -> None: ...


@runtime_checkable
class TTSProvider(Protocol):
    """
    One-shot synthesize that yields PCM chunks as they're produced.
    Streaming first-byte latency is the metric that matters here.
    """

    name: str

    async def synthesize_stream(
        self,
        text: str,
        *,
        target_language_code: str = "kn-IN",
        speaker: Optional[str] = None,
    ) -> AsyncIterator[AudioChunk]: ...

    async def close(self) -> None: ...
