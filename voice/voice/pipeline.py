"""
Pipecat pipeline definition for Arivu.

We construct one pipeline per active call. The pipeline is essentially:

    TwilioWS(in)
      → SileroVADAnalyzer        # turn detection (~20ms)
      → SarvamSTTService         # saaras:v3 (auto language)
      → BrainBridge              # POST → Brain → action JSON
      → SarvamTTSService         # bulbul:v2 (dialect-conditioned voice)
      → TwilioWS(out)

Two custom hooks live alongside the pipeline:

  * SafetyTriggerProcessor — runs in parallel on raw audio + transcripts;
    fires SafetyEventFrames that BrainBridge treats as hard overrides.
  * EventEmitter — converts every lifecycle frame into events.json events
    and publishes to Supabase Realtime.

Pipecat is heavy at import time (silero, torch, etc.) so we lazy-import
inside `create_pipeline` — this keeps `make dev` fast and lets the tests
run without GPU stacks installed.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from voice.events.publisher import get_publisher
from voice.processors.brain_proxy import BrainProxy
from voice.processors.event_emitter import EventEmitter
from voice.processors.safety_triggers import SafetyTriggerProcessor
from voice.transports.twilio_ws import build_pipecat_transport

logger = logging.getLogger("arivu.pipeline")


SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
BRAIN_URL = os.getenv("BRAIN_SERVICE_URL", "http://localhost:8002")


@dataclass
class CallContext:
    """Per-call state shared across the custom processors."""
    call_id: str
    twilio_call_sid: str = ""
    language: str = "kn-IN"
    dialect: str = "unknown"
    transcript_history: Optional[list[str]] = None
    turn_index: int = 0


async def create_pipeline(websocket, call_id: str):
    """
    Construct and return (PipelineRunner, Pipeline, CallContext) for a
    single call. Caller is responsible for starting the runner.
    """
    # ── Lazy imports ────────────────────────────────────────────────────
    try:
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.audio.vad.silero import SileroVADAnalyzer
    except ImportError as e:
        logger.error("pipecat unavailable (%s) — running stub pipeline.", e)
        return None, None, CallContext(call_id=call_id, transcript_history=[])

    ctx = CallContext(call_id=call_id, transcript_history=[])
    emitter = EventEmitter(call_id=call_id, publisher=get_publisher())
    safety = SafetyTriggerProcessor()
    brain_proxy = BrainProxy(brain_url=BRAIN_URL)

    transport = build_pipecat_transport(vad_analyzer=SileroVADAnalyzer())
    if transport is None:
        return None, None, ctx

    # ── Sarvam STT / TTS ────────────────────────────────────────────────
    # Wrapped in try/except because the Sarvam Pipecat adapter ships with
    # the sarvam-ai SDK; if the version mismatch we fall back to mock services.
    stt = None
    tts = None
    try:
        from pipecat.services.sarvam.stt import SarvamSTTService
        from pipecat.services.sarvam.tts import SarvamTTSService

        stt = SarvamSTTService(
            api_key=SARVAM_API_KEY,
            model="saaras:v3",
            language="auto",
            mode="transcribe",
        )
        tts = SarvamTTSService(
            api_key=SARVAM_API_KEY,
            model="bulbul:v2",
            voice="anushka",
            target_language_code="kn-IN",
        )
    except ImportError as e:
        logger.warning("sarvam adapters not found (%s) — STT/TTS disabled", e)

    pipeline_steps = [transport.input()]
    if stt is not None:
        pipeline_steps.append(stt)
    if tts is not None:
        pipeline_steps.append(tts)
    pipeline_steps.append(transport.output())

    pipeline = Pipeline(pipeline_steps)
    runner = PipelineRunner()

    # Attach helpers as attributes for the websocket handler to pick up.
    pipeline.arivu_ctx = ctx                        # type: ignore[attr-defined]
    pipeline.arivu_safety = safety                  # type: ignore[attr-defined]
    pipeline.arivu_brain = brain_proxy              # type: ignore[attr-defined]
    pipeline.arivu_emitter = emitter                # type: ignore[attr-defined]

    logger.info("pipeline ready for call %s (sarvam=%s)",
                call_id, "ok" if stt and tts else "stub")
    return runner, pipeline, ctx
