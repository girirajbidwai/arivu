"""
Provider protocol for the Brain's LLM layer.

The contract is deliberately minimal — one method, one structured output.
That's enough to express the verification turn, and small enough that we
can mock providers in tests without elaborate fixtures.

Why a single `unified_turn` instead of separate intent / verify / dialect
calls? Latency. One Gemini Flash call returns all four fields in 250–400 ms.
Three sequential calls cost 1.2–2.5 s. The 1092 helpline budget is sub-1s.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from ..schemas import Dialect, Sentiment


class UnifiedTurn(BaseModel):
    """
    Everything the Brain needs from one LLM call to drive the FSM forward.

    `verify_phrase` is the *exact* string the citizen will hear next, in
    their own dialect register. The model writes it; we don't post-process.
    """

    issue_summary: str = Field(
        description="One short sentence in English paraphrasing the citizen's need. Stripped of names/addresses/phone numbers."
    )
    urgency: int = Field(ge=1, le=5, description="1=casual enquiry, 5=imminent danger.")
    sentiment: Sentiment = Field(description="One of the six PS-named states.")
    dialect: Dialect = Field(description="Detected/confirmed Kannada dialect, or 'unknown'.")
    verify_phrase: str = Field(
        description="Single-line confirmation question to read back to the citizen, in their dialect. <20 words, no quotes, no JSON."
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Model's confidence in the interpretation.")
    # Optional metadata — providers attach raw token counts, finish_reason
    # etc. for telemetry. Never load-bearing.
    raw_meta: dict = Field(default_factory=dict)


class ProviderUnavailable(RuntimeError):
    """Raised when the active provider's credentials / network are missing."""


@runtime_checkable
class LLMProvider(Protocol):
    """The single seam between brain logic and the LLM vendor."""

    name: str  # "sarvam" | "gemini" | "stub"

    async def unified_turn(
        self,
        *,
        transcript: str,
        language: str,
        dialect_hint: str,
        history: list[str],
    ) -> UnifiedTurn:
        """One LLM call. Must return a fully-validated UnifiedTurn or raise."""
        ...

    async def close(self) -> None:
        """Release any sockets / clients. Optional for tests."""
        ...
