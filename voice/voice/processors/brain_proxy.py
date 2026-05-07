"""
BrainProxy — HTTP client to the Brain microservice.

Lives in the voice pipeline. On every `turn_ended` it builds a BrainInput,
POSTs it, and returns the BrainOutput. One persistent httpx client; tight
timeouts (the brain is on localhost or same VPC).

Decoupled from Pipecat frame plumbing so we can unit-test it in isolation
and swap the transport (HTTP → gRPC → in-process) without touching the
rest of the pipeline.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger("arivu.processors.brain_proxy")


class BrainProxy:
    def __init__(self, brain_url: Optional[str] = None) -> None:
        resolved = brain_url or os.getenv("BRAIN_SERVICE_URL", "http://localhost:8002")
        self.brain_url = resolved.rstrip("/")
        # Sarvam-M's <think> mode adds 3–5s per call; Brain's hot path
        # makes up to 4 sequential Sarvam calls (intent → verify_phrase
        # → optional retry → confirmation). 30s read timeout absorbs the
        # worst case while still failing fast on a real outage.
        self._client = httpx.AsyncClient(
            base_url=self.brain_url,
            timeout=httpx.Timeout(connect=1.0, read=30.0, write=1.0, pool=1.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def process(
        self,
        *,
        call_id: str,
        turn_index: int,
        transcript: str,
        language: Optional[str] = None,
        dialect: Optional[str] = None,
        safety_flags: Optional[list[str]] = None,
        history: Optional[list[str]] = None,
    ) -> dict:
        body = {
            "call_id": call_id,
            "turn_index": turn_index,
            "transcript": transcript,
            "language": language,
            "dialect": dialect,
            "safety_flags": safety_flags or [],
            "history": history or [],
        }
        t0 = time.perf_counter()
        try:
            r = await self._client.post("/process", json=body)
            r.raise_for_status()
            data = r.json()
            dt = (time.perf_counter() - t0) * 1000
            logger.info(
                "brain[%s/%d] %s in %.0fms — %s",
                call_id, turn_index, data.get("action"), dt,
                data.get("reasoning_trace", "")[:80],
            )
            return data
        except httpx.HTTPError as e:
            logger.error("brain call failed: %s", e)
            # Safe fallback — force handoff client-side if brain unreachable.
            return {
                "action": "handoff",
                "language_out": language or "kn-IN",
                "dialect_out": dialect or "unknown",
                "text": "",
                "issue_summary": "brain_unreachable",
                "urgency_1_to_5": 5,
                "confidence": 0.0,
                "fsm_state": "handoff",
                "handoff_reason": "brain_unreachable",
                "reasoning_trace": str(e),
                "latency_ms": {"total_ms": (time.perf_counter() - t0) * 1000},
            }

    async def correction(
        self, *, call_id: str, agent_id: str, field: str,
        old_value: str, new_value: str, turn_index: int = 0,
    ) -> None:
        try:
            await self._client.post("/correction_received", json={
                "call_id": call_id,
                "turn_index": turn_index,
                "agent_id": agent_id,
                "field": field,
                "old_value": old_value,
                "new_value": new_value,
            })
        except httpx.HTTPError as e:
            logger.warning("correction forward failed: %s", e)

    async def close(self) -> None:
        await self._client.aclose()
