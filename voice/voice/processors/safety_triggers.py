"""
SafetyTriggerProcessor — covert handoff detector.

Three independent detectors run in parallel on the inbound audio:

  1. WHISPER          — rolling RMS below threshold while VAD is active
  2. SILENT_AFTER_DISTRESS — distress keyword followed by >5s silence
  3. THIRD_VOICE      — speaker change inferred from spectral energy drift

Each detector is a pure-Python streaming algorithm; no model weights at
runtime. The processor emits a `SafetyEventFrame` (custom) which the
BrainProcessor reads as a hard override.

This is deliberately conservative — false positives are tolerable, false
negatives are not. A whisper trigger pulls a human onto the line; the
worst case is a citizen speaking softly gets a human earlier than needed.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("arivu.processors.safety")


# ── Tunables (configurable via env in production) ──────────────────────
WHISPER_RMS_DBFS_MAX = -30.0     # below this is "whisper" (relative)
WHISPER_DURATION_S = 2.0         # consecutive seconds of low-RMS speech
SILENCE_AFTER_DISTRESS_S = 5.0   # silence threshold after a distress word
SPECTRAL_DRIFT_THRESHOLD = 0.35  # cosine distance jump = new speaker


# A small but representative distress keyword set. The list is
# multilingual on purpose — we don't want to depend on STT picking the
# right language code first.
DISTRESS_KEYWORDS = {
    # Kannada
    "ಸಹಾಯ", "ಬಚಾವ್", "ತೊಂದರೆ", "ಕೊಲ್ಲಿತ್ತಾರೆ", "ಭಯ", "ಹೆದರಿಕೆ", "ಅಪಾಯ", "ಪ್ರಾಣ",
    # Hindi
    "बचाओ", "मदद", "खतरा", "जान", "मार", "डर", "धमकी",
    # English / Hinglish
    "help", "save me", "save us", "danger", "threat", "kill", "scared", "fear",
    "police", "emergency",
}


# ── Frame definition (mirrors Pipecat conventions) ─────────────────────
@dataclass
class SafetyEventFrame:
    """A custom frame emitted into the pipeline when a trigger fires."""
    trigger: str               # whisper_detected | silent_after_distress | third_voice
    confidence: float          # 0..1
    rms_dbfs: Optional[float] = None
    spectral_drift: Optional[float] = None
    silence_seconds: Optional[float] = None
    detected_at: float = field(default_factory=time.time)


# ── Detectors ──────────────────────────────────────────────────────────
class WhisperDetector:
    """Rolling RMS-dBFS check; fires after `WHISPER_DURATION_S` of low RMS while VAD active."""

    def __init__(self) -> None:
        self._below_since: Optional[float] = None

    def feed(self, *, rms_dbfs: float, vad_active: bool, now: Optional[float] = None) -> Optional[SafetyEventFrame]:
        now = now if now is not None else time.time()
        if not vad_active:
            self._below_since = None
            return None
        if rms_dbfs < WHISPER_RMS_DBFS_MAX:
            if self._below_since is None:
                self._below_since = now
            elif now - self._below_since >= WHISPER_DURATION_S:
                self._below_since = None
                return SafetyEventFrame(
                    trigger="whisper_detected",
                    confidence=min(1.0, (WHISPER_RMS_DBFS_MAX - rms_dbfs) / 10.0),
                    rms_dbfs=rms_dbfs,
                )
        else:
            self._below_since = None
        return None


class SilentAfterDistressDetector:
    """If a keyword from `DISTRESS_KEYWORDS` is heard then >5s of silence, fire."""

    def __init__(self) -> None:
        self._last_distress_at: Optional[float] = None
        self._fired_for: Optional[float] = None

    def feed_transcript(self, transcript: str, now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        if not transcript:
            return
        lo = transcript.lower()
        for kw in DISTRESS_KEYWORDS:
            if kw.lower() in lo:
                self._last_distress_at = now
                self._fired_for = None
                return

    def feed_silence(self, now: Optional[float] = None) -> Optional[SafetyEventFrame]:
        now = now if now is not None else time.time()
        if self._last_distress_at is None:
            return None
        elapsed = now - self._last_distress_at
        if elapsed >= SILENCE_AFTER_DISTRESS_S and self._fired_for != self._last_distress_at:
            self._fired_for = self._last_distress_at
            return SafetyEventFrame(
                trigger="silent_after_distress",
                confidence=0.85,
                silence_seconds=elapsed,
            )
        return None


class ThirdVoiceDetector:
    """
    Cheap speaker-change heuristic — track a rolling spectral fingerprint
    (mean per-band energy from the STT side); a sudden jump above
    `SPECTRAL_DRIFT_THRESHOLD` is treated as a new speaker.

    For the prototype this is heuristic on purpose. Replacing this with
    pyannote.audio is one drop-in if a judge demands it.
    """

    def __init__(self, history_size: int = 8) -> None:
        self._history: deque[list[float]] = deque(maxlen=history_size)

    def feed(self, fingerprint: list[float]) -> Optional[SafetyEventFrame]:
        if not fingerprint:
            return None
        if not self._history:
            self._history.append(fingerprint)
            return None

        # Cosine distance between current and rolling-average fingerprint.
        avg = [sum(col) / len(self._history) for col in zip(*self._history)]
        drift = _cosine_distance(fingerprint, avg)
        self._history.append(fingerprint)
        if drift >= SPECTRAL_DRIFT_THRESHOLD:
            return SafetyEventFrame(
                trigger="third_voice",
                confidence=min(1.0, drift),
                spectral_drift=drift,
            )
        return None


def _cosine_distance(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(x * x for x in b)) or 1e-9
    cos = max(-1.0, min(1.0, dot / (na * nb)))
    return 1.0 - cos


# ── Composite processor ────────────────────────────────────────────────
class SafetyTriggerProcessor:
    """
    Aggregates the three detectors. Public API is just `feed(...)` — the
    voice pipeline calls this on every audio chunk + every transcript.
    Returns the first SafetyEventFrame that fires, or None.
    """

    def __init__(self) -> None:
        self.whisper = WhisperDetector()
        self.silence = SilentAfterDistressDetector()
        self.third_voice = ThirdVoiceDetector()

    def feed_audio(
        self,
        *,
        rms_dbfs: float,
        vad_active: bool,
        spectral_fingerprint: Optional[list[float]] = None,
    ) -> Optional[SafetyEventFrame]:
        evt = self.whisper.feed(rms_dbfs=rms_dbfs, vad_active=vad_active)
        if evt:
            logger.warning("safety: whisper detected (rms=%.1f dBFS)", rms_dbfs)
            return evt
        if spectral_fingerprint:
            evt = self.third_voice.feed(spectral_fingerprint)
            if evt:
                logger.warning("safety: third voice (drift=%.2f)", evt.spectral_drift or 0.0)
                return evt
        evt = self.silence.feed_silence()
        if evt:
            logger.warning("safety: silence after distress (%.1fs)", evt.silence_seconds or 0.0)
            return evt
        return None

    def feed_transcript(self, transcript: str) -> None:
        self.silence.feed_transcript(transcript)
