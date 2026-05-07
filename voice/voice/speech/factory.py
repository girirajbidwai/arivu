"""
Factory: choose STT + TTS providers per-process.

Env knobs (all optional; auto-detect if unset):

    STT_PROVIDER = "google" | "sarvam"
    TTS_PROVIDER = "sarvam" | "google"

Auto-detection prefers Google STT if GCP creds are present (faster
partials in Mumbai region), Sarvam TTS for Indic dialect quality.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from .base import STTProvider, TTSProvider

logger = logging.getLogger("arivu.speech.factory")

_stt_singleton: Optional[STTProvider] = None
_tts_singleton: Optional[TTSProvider] = None


def _has_gcp() -> bool:
    return bool(os.getenv("GOOGLE_CLOUD_PROJECT")) and (
        bool(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
        or bool(os.getenv("K_SERVICE"))
    )


def _build_stt(name: str) -> STTProvider:
    name = name.strip().lower()
    if name == "google":
        from .google_streaming import GoogleSTT
        return GoogleSTT()
    if name == "sarvam":
        from .sarvam_streaming import SarvamSTT
        return SarvamSTT()
    raise ValueError(f"unknown STT provider: {name!r}")


def _build_tts(name: str) -> TTSProvider:
    name = name.strip().lower()
    if name == "sarvam":
        from .sarvam_streaming import SarvamTTS
        return SarvamTTS()
    if name == "google":
        from .google_streaming import GoogleTTS
        return GoogleTTS()
    raise ValueError(f"unknown TTS provider: {name!r}")


def get_stt_provider() -> STTProvider:
    """Process-singleton STT provider. Falls back to Sarvam if Google unavailable."""
    global _stt_singleton
    if _stt_singleton is not None:
        return _stt_singleton
    pref = os.getenv("STT_PROVIDER", "").strip().lower()
    if not pref:
        pref = "google" if _has_gcp() else "sarvam"
    try:
        _stt_singleton = _build_stt(pref)
        logger.info("STT provider: %s", _stt_singleton.name)
    except Exception as e:
        logger.warning("STT %r unavailable (%s) — fallback sarvam", pref, e)
        _stt_singleton = _build_stt("sarvam")
        logger.info("STT provider (fallback): %s", _stt_singleton.name)
    return _stt_singleton


def get_tts_provider() -> TTSProvider:
    """Process-singleton TTS provider. Sarvam Bulbul stays preferred for Indic quality."""
    global _tts_singleton
    if _tts_singleton is not None:
        return _tts_singleton
    pref = os.getenv("TTS_PROVIDER", "sarvam").strip().lower()
    try:
        _tts_singleton = _build_tts(pref)
        logger.info("TTS provider: %s", _tts_singleton.name)
    except Exception as e:
        logger.warning("TTS %r unavailable (%s) — fallback google", pref, e)
        _tts_singleton = _build_tts("google")
        logger.info("TTS provider (fallback): %s", _tts_singleton.name)
    return _tts_singleton
