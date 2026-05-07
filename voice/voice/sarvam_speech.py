"""
Sarvam Speech wrappers — Saaras STT + Bulbul TTS.

Used by the browser-mic path (`POST /web/turn`) since we're holding off
on Twilio. Same shared async client as the Brain LLM so TLS stays warm.

Sarvam API endpoints (all POST):
  /speech-to-text       — multipart audio in, JSON {transcript, language_code} out
  /text-to-speech       — JSON in, JSON with base64 WAV chunks out

If `SARVAM_API_KEY` isn't set, both methods return empty results — the
brain endpoint upstream still functions, the demo just stays text-only.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import time
from typing import Optional

import httpx

from voice.brain.llm import get_shared_client

logger = logging.getLogger("arivu.sarvam_speech")

SARVAM_STT_PATH = "/speech-to-text"
SARVAM_TTS_PATH = "/text-to-speech"

# Models — pinned per Sarvam's published versioning.
DEFAULT_STT_MODEL = os.getenv("SARVAM_STT_MODEL", "saarika:v2.5")
DEFAULT_TTS_MODEL = os.getenv("SARVAM_TTS_MODEL", "bulbul:v3")
DEFAULT_TTS_SPEAKER = os.getenv("SARVAM_TTS_SPEAKER", "pooja")


def _api_key() -> str:
    # Read on every call — main.py imports this module BEFORE load_dotenv()
    # runs, so we can't snapshot at module import.
    return os.getenv("SARVAM_API_KEY", "")


def _headers() -> dict:
    key = _api_key()
    return {
        "api-subscription-key": key,
        # Some Sarvam routes accept Bearer too; harmless to send both.
        "Authorization": f"Bearer {key}",
    }


# ── STT (Saaras) ────────────────────────────────────────────────────────
async def transcribe(
    audio_bytes: bytes,
    *,
    filename: str = "audio.webm",
    content_type: str = "audio/webm",
    language_code: str = "unknown",
    model: str = DEFAULT_STT_MODEL,
) -> dict:
    """
    Transcribe one utterance. `language_code='unknown'` lets Saaras auto-detect.

    Returns: {transcript, language_code, duration_s, latency_ms}
    On any error returns transcript="" — caller must handle empty strings.
    """
    if not _api_key():
        logger.warning("SARVAM_API_KEY missing — transcribe returning empty")
        return {"transcript": "", "language_code": "", "duration_s": 0.0, "latency_ms": 0}
    if not audio_bytes:
        return {"transcript": "", "language_code": "", "duration_s": 0.0, "latency_ms": 0}

    client = await get_shared_client()
    files = {
        "file": (filename, io.BytesIO(audio_bytes), content_type),
    }
    data = {
        "model": model,
        "language_code": language_code,
        # `with_diarization` and `with_timestamps` available; we keep this
        # path minimal for latency.
    }

    t0 = time.perf_counter()
    try:
        r = await client.post(
            SARVAM_STT_PATH,
            files=files,
            data=data,
            headers=_headers(),
            timeout=httpx.Timeout(connect=2.0, read=15.0, write=5.0, pool=2.0),
        )
        r.raise_for_status()
        body = r.json()
        dt_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "stt ok in %.0fms — '%s' (lang=%s)",
            dt_ms,
            (body.get("transcript", "") or "")[:60],
            body.get("language_code", "?"),
        )
        return {
            "transcript": body.get("transcript", "") or "",
            "language_code": body.get("language_code", "") or "",
            "duration_s": float(body.get("duration_s") or body.get("duration") or 0.0),
            "latency_ms": round(dt_ms, 1),
        }
    except httpx.HTTPError as e:
        dt_ms = (time.perf_counter() - t0) * 1000
        body_preview = ""
        if isinstance(e, httpx.HTTPStatusError):
            try:
                body_preview = e.response.text[:200]
            except Exception:
                pass
        logger.error("stt failed in %.0fms: %s — %s", dt_ms, e, body_preview)
        return {"transcript": "", "language_code": "", "duration_s": 0.0, "latency_ms": dt_ms}


# ── TTS (Bulbul) ────────────────────────────────────────────────────────
async def synthesize(
    text: str,
    *,
    target_language_code: str = "kn-IN",
    speaker: str = DEFAULT_TTS_SPEAKER,
    model: str = DEFAULT_TTS_MODEL,
    pitch: float = 0.0,
    pace: float = 1.0,
    loudness: float = 1.0,
) -> dict:
    """
    Synthesize speech. Returns dict:
      {audio_b64: <base64 WAV bytes>, mime: "audio/wav", latency_ms: ...}
    On any failure returns audio_b64="" so the caller can degrade gracefully.
    """
    if not _api_key():
        logger.warning("SARVAM_API_KEY missing — synthesize returning empty")
        return {"audio_b64": "", "mime": "audio/wav", "latency_ms": 0}
    if not text or not text.strip():
        return {"audio_b64": "", "mime": "audio/wav", "latency_ms": 0}

    client = await get_shared_client()
    body = {
        "inputs": [text],
        "target_language_code": target_language_code,
        "speaker": speaker,
        "model": model,
        "speech_sample_rate": 22050,
        "enable_preprocessing": True,
    }
    # Bulbul v3 does not support pitch/pace/loudness — only add for v2
    if "v2" in model:
        body["pitch"] = pitch
        body["pace"] = pace
        body["loudness"] = loudness

    t0 = time.perf_counter()
    try:
        r = await client.post(
            SARVAM_TTS_PATH,
            json=body,
            headers=_headers(),
            timeout=httpx.Timeout(connect=2.0, read=15.0, write=2.0, pool=2.0),
        )
        r.raise_for_status()
        data = r.json()
        # API returns `audios: [base64...]` (list), one entry per `inputs`.
        audios = data.get("audios") or []
        audio_b64 = audios[0] if audios else ""
        dt_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "tts ok in %.0fms — %d chars in / %d b64 out",
            dt_ms, len(text), len(audio_b64),
        )
        return {
            "audio_b64": audio_b64,
            "mime": "audio/wav",
            "latency_ms": round(dt_ms, 1),
        }
    except httpx.HTTPError as e:
        dt_ms = (time.perf_counter() - t0) * 1000
        body_preview = ""
        if isinstance(e, httpx.HTTPStatusError):
            try:
                body_preview = e.response.text[:200]
            except Exception:
                pass
        logger.error("tts failed in %.0fms: %s — %s", dt_ms, e, body_preview)
        return {"audio_b64": "", "mime": "audio/wav", "latency_ms": dt_ms}
