"""
Sarvam-M / Sarvam-30b LLM provider.

Wraps the existing `voice.brain.llm.SarvamLLM` chat client and adds the
unified-turn prompt path. Used today (works without GCP creds) and stays
on as the shadow validator once Gemini becomes the primary.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Optional

from pydantic import ValidationError

from ..llm import SarvamLLM, _extract_json, _strip_think
from .base import LLMProvider, ProviderUnavailable, UnifiedTurn
from ._unified_prompt import build_messages

logger = logging.getLogger("arivu.brain.provider.sarvam")

# Sarvam-30b separates reasoning from content, but on long Indic prompts
# it can still spend most of the budget thinking. 2500 is the empirical
# floor that gives us a populated `content` field every time.
_DEFAULT_MAX_TOKENS = int(os.getenv("SARVAM_MAX_TOKENS", "2500"))


class SarvamProvider:
    name = "sarvam"

    def __init__(self, api_key: Optional[str] = None) -> None:
        key = api_key or os.getenv("SARVAM_API_KEY", "")
        if not key:
            raise ProviderUnavailable("SARVAM_API_KEY not set")
        self._llm = SarvamLLM(api_key=key)

    async def unified_turn(
        self,
        *,
        transcript: str,
        language: str,
        dialect_hint: str,
        history: list[str],
    ) -> UnifiedTurn:
        msgs = build_messages(
            transcript=transcript,
            language=language,
            dialect_hint=dialect_hint,
            history=history,
        )
        # Flatten messages into a single user prompt — Sarvam chat path
        # expects {role:user} with the system content prepended (the
        # _chat() helper adds its own system anchor too; that's fine).
        prompt = msgs[0]["content"] + "\n\n" + msgs[1]["content"]

        for attempt in range(2):
            t0 = time.perf_counter()
            raw = await self._llm._chat(
                prompt,
                max_tokens=_DEFAULT_MAX_TOKENS,
                temperature=0.1,
                json_mode=False,  # response_format conflicts with reasoning budget
            )
            dt = (time.perf_counter() - t0) * 1000
            parsed = _extract_json(raw or "")
            if not parsed:
                logger.warning("sarvam.unified attempt %d: no JSON in %.0fms (raw_len=%d)",
                               attempt + 1, dt, len(raw or ""))
                continue
            try:
                obj = json.loads(parsed)
                turn = UnifiedTurn(
                    raw_meta={"latency_ms": round(dt, 1), "provider": "sarvam", "attempt": attempt + 1},
                    **obj,
                )
                logger.info("sarvam.unified ok in %.0fms (urgency=%d dialect=%s conf=%.2f)",
                            dt, turn.urgency, turn.dialect, turn.confidence)
                return turn
            except (json.JSONDecodeError, ValidationError) as e:
                logger.warning("sarvam.unified parse fail attempt %d: %s", attempt + 1, e)

        # Both attempts failed — caller treats this as parse_failed and
        # the FSM forces handoff. Never silent.
        raise ProviderUnavailable("sarvam returned no parseable JSON twice")

    async def close(self) -> None:
        # SarvamLLM uses a module-level shared client; we don't own it.
        return None
