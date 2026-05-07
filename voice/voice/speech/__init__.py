"""
Streaming STT + TTS adapter surface.

Two protocols, two seams:

    STTStream — async iterator yielding partial + final transcripts.
    TTSStream — async iterator yielding raw PCM byte chunks.

Active provider chosen by env (`STT_PROVIDER`, `TTS_PROVIDER`). One env
flip swaps Sarvam → Google STT v2 / ElevenLabs / etc. — no caller changes.
"""

from .base import (
    STTProvider,
    TTSProvider,
    STTPartial,
    STTFinal,
    AudioChunk,
)
from .factory import get_stt_provider, get_tts_provider

__all__ = [
    "STTProvider",
    "TTSProvider",
    "STTPartial",
    "STTFinal",
    "AudioChunk",
    "get_stt_provider",
    "get_tts_provider",
]
