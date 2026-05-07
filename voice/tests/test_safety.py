"""
Safety triggers — whisper, silence-after-distress, third-voice.

Pure-Python detectors; we drive them with synthetic feeds and assert
each fires correctly. No audio fixtures needed at this stage.
"""

import time

from voice.processors.safety_triggers import (
    SafetyTriggerProcessor,
    WhisperDetector,
    SilentAfterDistressDetector,
    ThirdVoiceDetector,
    DISTRESS_KEYWORDS,
    WHISPER_DURATION_S,
    SILENCE_AFTER_DISTRESS_S,
)


# ── Whisper ────────────────────────────────────────────────────────────

def test_whisper_fires_after_duration():
    det = WhisperDetector()
    t = 1000.0
    # First sample below threshold but not yet long enough.
    assert det.feed(rms_dbfs=-40.0, vad_active=True, now=t) is None
    # Cross the duration threshold.
    evt = det.feed(rms_dbfs=-45.0, vad_active=True, now=t + WHISPER_DURATION_S + 0.01)
    assert evt is not None
    assert evt.trigger == "whisper_detected"
    assert 0.0 <= evt.confidence <= 1.0


def test_whisper_does_not_fire_without_vad():
    det = WhisperDetector()
    assert det.feed(rms_dbfs=-50.0, vad_active=False, now=1.0) is None
    assert det.feed(rms_dbfs=-50.0, vad_active=False, now=10.0) is None


def test_whisper_resets_when_volume_returns():
    det = WhisperDetector()
    det.feed(rms_dbfs=-40.0, vad_active=True, now=1.0)
    det.feed(rms_dbfs=-10.0, vad_active=True, now=2.0)  # back to normal
    # Another low sample — should not fire immediately.
    assert det.feed(rms_dbfs=-40.0, vad_active=True, now=2.5) is None


# ── Silence after distress ─────────────────────────────────────────────

def test_silence_after_distress_fires():
    det = SilentAfterDistressDetector()
    kw = next(iter(DISTRESS_KEYWORDS))
    det.feed_transcript(f"please {kw} now", now=0.0)
    assert det.feed_silence(now=SILENCE_AFTER_DISTRESS_S - 0.1) is None
    evt = det.feed_silence(now=SILENCE_AFTER_DISTRESS_S + 0.5)
    assert evt is not None
    assert evt.trigger == "silent_after_distress"
    assert evt.silence_seconds is not None


def test_silence_does_not_fire_without_distress_keyword():
    det = SilentAfterDistressDetector()
    det.feed_transcript("hello there how are you", now=0.0)
    assert det.feed_silence(now=120.0) is None


def test_silence_only_fires_once_per_distress():
    det = SilentAfterDistressDetector()
    kw = next(iter(DISTRESS_KEYWORDS))
    det.feed_transcript(kw, now=0.0)
    first = det.feed_silence(now=SILENCE_AFTER_DISTRESS_S + 1)
    second = det.feed_silence(now=SILENCE_AFTER_DISTRESS_S + 2)
    assert first is not None
    assert second is None


# ── Third voice ────────────────────────────────────────────────────────

def test_third_voice_fires_on_drift():
    det = ThirdVoiceDetector(history_size=4)
    base = [1.0, 0.5, 0.3, 0.2, 0.1]
    for _ in range(4):
        det.feed(base)
    # Big shift in spectral energy → another speaker.
    evt = det.feed([0.1, 0.2, 0.3, 0.5, 1.0])
    assert evt is not None
    assert evt.trigger == "third_voice"


def test_third_voice_silent_for_consistent_speaker():
    det = ThirdVoiceDetector(history_size=4)
    base = [1.0, 0.5, 0.3, 0.2, 0.1]
    for _ in range(8):
        evt = det.feed(base)
    assert evt is None


# ── Composite ──────────────────────────────────────────────────────────

def test_composite_processor_routes_audio_and_transcript():
    proc = SafetyTriggerProcessor()
    # No fire on quiet, non-VAD frame.
    assert proc.feed_audio(rms_dbfs=-50.0, vad_active=False) is None
    # Distress + silence.
    kw = next(iter(DISTRESS_KEYWORDS))
    proc.feed_transcript(f"{kw} please")
    # Manually push enough time forward via the underlying detector.
    proc.silence._last_distress_at = time.time() - SILENCE_AFTER_DISTRESS_S - 1
    evt = proc.feed_audio(rms_dbfs=-10.0, vad_active=True)
    assert evt is not None
    assert evt.trigger in {"silent_after_distress", "whisper_detected", "third_voice"}
