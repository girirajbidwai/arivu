"""
Pydantic input/output models for the Brain service.

These schemas are the contract between the voice pipeline (Pipecat
BrainProcessor) and the Brain microservice. Every field is documented
because mismatched fields between caller and callee are the most common
source of pipeline bugs.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Language = Literal["kn-IN", "hi-IN", "en-IN"]
Dialect = Literal["dharwad", "mangaluru", "mysuru", "bengaluru", "unknown"]
Sentiment = Literal["neutral", "confused", "anxious", "fearful", "distressed", "calm"]
SafetyTrigger = Literal["whisper_detected", "silent_after_distress", "third_voice"]
Action = Literal["speak", "verify", "handoff", "interpret"]
FSMState = Literal["pending", "verifying", "verified", "handoff"]


class BrainInput(BaseModel):
    """One Brain invocation per `turn_ended` from the voice pipeline."""

    call_id: str
    turn_index: int = 0
    transcript: str
    language: Optional[Language] = None
    dialect: Optional[Dialect] = None
    safety_flags: list[SafetyTrigger] = Field(default_factory=list)
    history: list[str] = Field(default_factory=list)
    audio_features: Optional[dict] = None  # rms, prosody, etc.


class IntentResult(BaseModel):
    """Output of the Sarvam-M intent extractor."""

    issue_summary: str
    urgency: int = Field(ge=1, le=5)
    sentiment: Sentiment = "neutral"
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)


class DialectResult(BaseModel):
    """Output of the dialect classifier."""

    dialect: Dialect
    confidence: float = Field(ge=0.0, le=1.0)
    markers_matched: list[str] = Field(default_factory=list)


class BrainOutput(BaseModel):
    """What the BrainProcessor receives and turns into TTS / handoff frames."""

    action: Action
    language_out: Language = "kn-IN"
    dialect_out: Dialect = "unknown"
    text: str = ""
    issue_summary: str = ""
    urgency_1_to_5: int = Field(ge=1, le=5, default=3)
    sentiment: Sentiment = "neutral"
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    fsm_state: FSMState = "pending"
    handoff_reason: Optional[str] = None
    reasoning_trace: str = ""
    # Latency telemetry — measured by the Brain so the dashboard can show it.
    latency_ms: dict = Field(default_factory=dict)


class CorrectionInput(BaseModel):
    """Posted by the dashboard backend whenever an agent overrides a field."""

    call_id: str
    turn_index: int = 0
    agent_id: str
    field: Literal["issue_summary", "urgency", "dialect", "sentiment"]
    old_value: str
    new_value: str
    timestamp: Optional[str] = None
