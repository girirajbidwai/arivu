"""
Brain LLM provider plug-in surface.

Active provider is chosen by env:

    BRAIN_LLM_PROVIDER = "gemini" | "sarvam"   (default: gemini if GOOGLE_APPLICATION_CREDENTIALS, else sarvam)

Every provider exposes the same `LLMProvider` protocol — one method,
`unified_turn`, returns a `UnifiedTurn` Pydantic model. That's the only
contract callers depend on. Swap providers without touching brain logic.
"""

from .base import LLMProvider, UnifiedTurn, ProviderUnavailable
from .factory import get_llm_provider, get_shadow_provider

__all__ = [
    "LLMProvider", "UnifiedTurn", "ProviderUnavailable",
    "get_llm_provider", "get_shadow_provider",
]
