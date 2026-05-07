"""
InterpreterProcessor — silent translator that runs after handoff.

When the call enters interpreter mode:
  * Citizen speaks Kannada (any dialect) → Arivu translates → agent hears Hindi/English.
  * Agent speaks Hindi/English → Arivu translates → citizen hears their dialect.

We pipeline both directions concurrently. STT and TTS each sit on their
own asyncio task; translation is a single Mayura call per utterance.

Latency budget per direction: STT (~250ms TTFT) + Mayura (~300ms) +
TTS (~250ms first byte) ≈ 800ms — safely under the 2s human conversation
gap people tolerate.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from voice.brain.interpreter import Interpreter

logger = logging.getLogger("arivu.processors.interpreter")

Direction = Literal["citizen_to_agent", "agent_to_citizen"]


@dataclass
class InterpreterTurn:
    direction: Direction
    source_lang: str
    target_lang: str
    source_text: str
    target_text: str
    latency_ms: float


class InterpreterProcessor:
    """
    Glue between two STT streams (citizen + agent legs of the bridged
    call) and the agent / citizen TTS outputs. The two streams are wired
    through callback hooks so this class stays test-friendly.
    """

    def __init__(
        self,
        *,
        api_key: str,
        speak_to_agent: Callable[[str, str], Awaitable[None]],
        speak_to_citizen: Callable[[str, str], Awaitable[None]],
        agent_lang: str = "hi-IN",
        citizen_lang: str = "kn-IN",
        max_segment_chars: int = 240,
    ) -> None:
        self._mayura = Interpreter(api_key=api_key)
        self._speak_to_agent = speak_to_agent
        self._speak_to_citizen = speak_to_citizen
        self.agent_lang = agent_lang
        self.citizen_lang = citizen_lang
        self.max_segment_chars = max_segment_chars
        self._inflight: set[asyncio.Task] = set()

    async def on_citizen_utterance(self, text: str) -> InterpreterTurn:
        return await self._handle(
            text,
            direction="citizen_to_agent",
            source=self.citizen_lang,
            target=self.agent_lang,
            speak=self._speak_to_agent,
        )

    async def on_agent_utterance(self, text: str) -> InterpreterTurn:
        return await self._handle(
            text,
            direction="agent_to_citizen",
            source=self.agent_lang,
            target=self.citizen_lang,
            speak=self._speak_to_citizen,
        )

    async def _handle(
        self,
        text: str,
        *,
        direction: Direction,
        source: str,
        target: str,
        speak: Callable[[str, str], Awaitable[None]],
    ) -> InterpreterTurn:
        text = (text or "").strip()
        if not text:
            return InterpreterTurn(
                direction=direction, source_lang=source, target_lang=target,
                source_text="", target_text="", latency_ms=0.0,
            )

        # Cap segment length so the speaker doesn't get a 30s lag spike.
        if len(text) > self.max_segment_chars:
            text = text[: self.max_segment_chars]

        t0 = time.perf_counter()
        translated = await self._mayura.translate(
            text, source=source, target=target,  # type: ignore[arg-type]
            mode="modern-colloquial" if direction == "agent_to_citizen" else "formal",
        )
        # Speak in the background; we don't block the next utterance.
        task = asyncio.create_task(speak(translated, target))
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

        return InterpreterTurn(
            direction=direction,
            source_lang=source,
            target_lang=target,
            source_text=text,
            target_text=translated,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    async def drain(self) -> None:
        """Wait for outstanding TTS to flush — used at end-of-call."""
        if self._inflight:
            await asyncio.gather(*list(self._inflight), return_exceptions=True)
