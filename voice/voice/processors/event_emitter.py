"""
EventEmitter — converts Pipecat lifecycle frames into events.json
shapes and publishes them to Supabase / dashboard fallback.

Decoupled from any specific frame class so we can also fire events
from non-Pipecat code paths (REST handlers, tests, demo scripts).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from voice.events.publisher import EventPublisher, get_publisher

logger = logging.getLogger("arivu.processors.event_emitter")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventEmitter:
    """
    Constructed once per call. Holds the call_id so callers don't have
    to repeat it on every emit.
    """

    def __init__(
        self,
        *,
        call_id: str,
        publisher: Optional[EventPublisher] = None,
    ) -> None:
        self.call_id = call_id
        self._publisher = publisher or get_publisher()
        self._turn_index = 0
        self._started_at = time.time()

    # ── Lifecycle ───────────────────────────────────────────────────────
    async def call_started(self, *, caller_number: str = "", language: str = "kn-IN") -> None:
        await self._emit("call_started", {
            "speaker": "system",
            "language": language,
        })

    async def call_ended(self) -> None:
        await self._emit("call_ended", {
            "speaker": "system",
        })

    async def turn_started(self, *, speaker: str = "citizen") -> None:
        self._turn_index += 1
        await self._emit("turn_started", {"speaker": speaker})

    async def transcript_partial(self, *, transcript: str, speaker: str = "citizen", language: str = "kn-IN") -> None:
        await self._emit("transcript_partial", {
            "speaker": speaker,
            "transcript": transcript,
            "language": language,
        })

    async def transcript_final(self, *, transcript: str, speaker: str = "citizen", language: str = "kn-IN") -> None:
        await self._emit("transcript_final", {
            "speaker": speaker,
            "transcript": transcript,
            "language": language,
        })

    async def dialect_detected(self, *, dialect: str, confidence: float) -> None:
        await self._emit("dialect_detected", {
            "dialect": dialect,
            "dialect_confidence": confidence,
        })

    async def sentiment_updated(self, *, sentiment: str, confidence: float = 0.7) -> None:
        await self._emit("sentiment_updated", {
            "sentiment": sentiment,
            "sentiment_confidence": confidence,
        })

    async def verification_pending(self, *, verify_phrase: str, issue_summary: str, urgency: int) -> None:
        await self._emit("verification_pending", {
            "verify_phrase": verify_phrase,
            "issue_summary": issue_summary,
            "urgency": urgency,
            "fsm_state": "verifying",
        })

    async def verification_confirmed(self, *, issue_summary: str, urgency: int, confidence: float) -> None:
        await self._emit("verification_confirmed", {
            "issue_summary": issue_summary,
            "urgency": urgency,
            "confidence": confidence,
            "fsm_state": "verified",
        })

    async def safety_alert(self, *, trigger: str, confidence: float = 0.9) -> None:
        await self._emit("safety_alert", {
            "safety_trigger": trigger,
            "confidence": confidence,
            "fsm_state": "handoff",
        })

    async def handoff_initiated(self, *, reason: str) -> None:
        await self._emit("handoff_initiated", {
            "handoff_reason": reason,
            "fsm_state": "handoff",
        })

    async def interpreter_mode_active(self, *, agent_lang: str, citizen_lang: str) -> None:
        await self._emit("interpreter_mode_active", {
            "language": citizen_lang,
            "reasoning_trace": f"interpreter: {agent_lang} ↔ {citizen_lang}",
        })

    # ── Internal ────────────────────────────────────────────────────────
    async def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        envelope = {
            "event_type": event_type,
            "call_id": self.call_id,
            "turn_index": self._turn_index,
            "timestamp": _now_iso(),
            "data": data,
        }
        try:
            await self._publisher.publish(envelope)
        except Exception as e:  # pragma: no cover
            logger.debug("emit %s failed: %s", event_type, e)
