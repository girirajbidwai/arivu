"""
Provider factory — picks the active LLM at process start, with graceful
fallback. One env var (`BRAIN_LLM_PROVIDER`) overrides automatic detection.

Auto-detection rules:
  * If `GOOGLE_APPLICATION_CREDENTIALS` (or running on Cloud Run) AND
    `GOOGLE_CLOUD_PROJECT` are set → Gemini.
  * Else if `SARVAM_API_KEY` is set → Sarvam.
  * Else stub provider that raises on every call (tests use this).

A "shadow" provider can be configured via `BRAIN_SHADOW_PROVIDER` to run
in parallel for quality validation; results never block the hot path.
The shadow result lands in event publisher as a `quality_check` event.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from dotenv import load_dotenv

# Bug #4 fix: ensure .env is loaded before any provider reads os.getenv.
# Without this, direct imports of GeminiProvider (e.g. test scripts) fail
# because load_dotenv() only runs inside voice.main's module init.
load_dotenv()

from .base import LLMProvider, ProviderUnavailable, UnifiedTurn

logger = logging.getLogger("arivu.brain.provider.factory")

_primary: Optional[LLMProvider] = None
_shadow: Optional[LLMProvider] = None


def _build(name: str) -> LLMProvider:
    name = (name or "").strip().lower()
    if name in ("gemini", "google", "vertex"):
        from .gemini import GeminiProvider
        return GeminiProvider()
    if name in ("sarvam", "sarvam-m", "sarvam-30b"):
        from .sarvam import SarvamProvider
        return SarvamProvider()
    if name in ("heuristic", "fast", "local"):
        from .heuristic import HeuristicProvider
        return HeuristicProvider()
    if name == "stub":
        return _StubProvider()
    raise ProviderUnavailable(f"unknown provider {name!r}")


def _autodetect() -> str:
    explicit = os.getenv("BRAIN_LLM_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    # Priority 1: Google AI API key (fastest — no IAM overhead)
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    # Priority 2: Vertex AI with GCP creds
    has_gcp = bool(os.getenv("GOOGLE_CLOUD_PROJECT")) and (
        bool(os.getenv("GOOGLE_APPLICATION_CREDENTIALS")) or bool(os.getenv("K_SERVICE"))
    )
    if has_gcp:
        return "gemini"
    # Heuristic > Sarvam by default: Sarvam-30b reasoning blows the
    # latency budget on Indic prompts.
    return "heuristic"


def get_llm_provider() -> LLMProvider:
    """Return the cached primary provider, building it on first call."""
    global _primary
    if _primary is None:
        choice = _autodetect()
        try:
            _primary = _build(choice)
            logger.info("brain LLM provider: %s", _primary.name)
        except ProviderUnavailable as e:
            logger.warning("preferred provider %r unavailable (%s); falling back", choice, e)
            # Try the other one; always end with at least a stub.
            for fallback in ("sarvam", "gemini", "stub"):
                if fallback == choice:
                    continue
                try:
                    _primary = _build(fallback)
                    logger.info("brain LLM provider (fallback): %s", _primary.name)
                    break
                except ProviderUnavailable:
                    continue
            else:
                _primary = _StubProvider()
    return _primary


def get_shadow_provider() -> Optional[LLMProvider]:
    """
    Optional secondary provider for quality validation. Runs in parallel
    to the primary; never on the critical path. Returns None if no shadow
    is configured (the common case).
    """
    global _shadow
    name = os.getenv("BRAIN_SHADOW_PROVIDER", "").strip().lower()
    if not name:
        return None
    if _shadow is None:
        try:
            _shadow = _build(name)
            logger.info("brain shadow provider: %s", _shadow.name)
        except ProviderUnavailable as e:
            logger.warning("shadow provider %r unavailable: %s", name, e)
            _shadow = None
    return _shadow


# ── stub for tests + degraded mode ────────────────────────────────────
class _StubProvider:
    """Returns a deterministic 'I cannot interpret' response. Safe default."""

    name = "stub"

    async def unified_turn(self, *, transcript, language, dialect_hint, history) -> UnifiedTurn:
        return UnifiedTurn(
            issue_summary="parse_failed",
            urgency=5,
            sentiment="confused",
            dialect="unknown",
            verify_phrase="ಕ್ಷಮಿಸಿ, ನಿಮ್ಮನ್ನು ಅಧಿಕಾರಿಯೊಂದಿಗೆ ಸಂಪರ್ಕಿಸುತ್ತೇನೆ.",
            confidence=0.0,
            raw_meta={"provider": "stub"},
        )

    async def close(self) -> None:
        return None
