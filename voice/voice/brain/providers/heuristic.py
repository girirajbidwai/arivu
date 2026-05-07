"""
Heuristic provider — sub-10 ms unified turn, no LLM.

We need a working brain *now* (before GCP creds land) so the demo, the
streaming pipeline, and the dashboard wiring can all be exercised with
realistic-looking output. This module turns transcripts into structured
turns using nothing but the existing dialect classifier and a small
keyword/template table.

Quality is honest about its limits — we surface `confidence ≤ 0.6` so
the FSM treats every heuristic turn as needing eventual human review.
The moment a real provider (Gemini) comes online, this one quietly
moves to the bench.
"""

from __future__ import annotations

import logging
import re
import time

from ..dialect import DialectClassifier
from .base import LLMProvider, UnifiedTurn

logger = logging.getLogger("arivu.brain.provider.heuristic")

_classifier = DialectClassifier()


# ── Distress + urgency keywords (multilingual) ─────────────────────────
URGENCY_5 = (
    "save me", "save us", "kill", "killing", "danger", "police",
    # Kannada
    "ಕೊಲ್", "ಬಚಾವ್", "ಪ್ರಾಣ", "ಸಾಯ್",
    # Hindi
    "बचाओ", "बचाइए", "जान", "मार", "खतरा", "अभी", "मरने", "जलाने",
)
URGENCY_4 = (
    "help", "scared", "fear", "abuse", "threat", "harass", "missing",
    "lost", "lapata", "lapatha",
    # Kannada
    "ಭಯ", "ತೊಂದರ", "ಹಿಂಸ", "ಹೆದರಿಕೆ", "ಲಾಪತ್ತಾ", "ಕಾಣೆ",
    # Hindi
    "तंग", "धमकी", "उत्पीड़", "मदद", "लापता", "गुम", "डर",
)
URGENCY_3 = (
    "complaint", "issue", "problem", "ration", "card", "office",
    # Kannada
    "ಸಮಸ್ಯೆ", "ಆಫೀಸ್",
    # Hindi
    "समस्या", "शिकायत", "परेशानी",
)


# Dialect-matched reprompt templates. We deliberately keep them short
# and conservative — quality over flair.
VERIFY_TEMPLATES = {
    "dharwad":   "ನಿಮ್ಮ ಸಮಸ್ಯೆ — {topic} — ಅಂತ ಹೇಳ್ತಿದೀರಾ ರೀ?",
    "mangaluru": "ನಿಮ್ಮ ಸಮಸ್ಯೆ {topic} ಆಯಿತಾ?",
    "mysuru":    "ನಿಮ್ಮ ಸಮಸ್ಯೆ {topic} ಎಂದು ಹೇಳುತ್ತಿದ್ದೀರಾ?",
    "bengaluru": "ನಿಮ್ಮ ಸಮಸ್ಯೆ — {topic} — ಸರಿಯಾ ಸಾರ್?",
    "unknown":   "ನಿಮ್ಮ ಸಮಸ್ಯೆ {topic} ಎಂದು ಅರ್ಥವಾಗಿದೆಯೇ?",
}
HINDI_TEMPLATE = "क्या आपकी समस्या {topic} है?"
ENGLISH_TEMPLATE = "Are you saying your issue is — {topic}?"


# Sentiment heuristics — same multilingual scan. Order matters: we
# return the first match, so distress/fear must come before milder
# states.
SENTIMENT_KEYS = {
    "distressed": ("kill", "save me", "save us", "ಪ್ರಾಣ", "ಕೊಲ್", "ಬಚಾವ್",
                   "जान", "मार", "बचाओ", "बचाइए", "मरने", "जलाने", "अभी"),
    "fearful":    ("scared", "fear", "afraid", "threat",
                   "ಭಯ", "ಹೆದರಿಕೆ", "ಹೆದರ",
                   "डर", "धमकी", "खतरा"),
    "anxious":    ("worried", "tense", "missing", "lost", "lapata",
                   "ಚಿಂತೆ", "ತೊಂದರ", "ಕಾಣೆ", "ಲಾಪತ್ತಾ",
                   "परेशान", "चिंता", "लापता", "गुम", "तंग"),
    "confused":   ("don't know", "what to do", "ಗೊತ್ತಿಲ್", "नहीं पता", "समझ नहीं"),
    "calm":       ("ok", "fine", "ಸರಿ", "ಚೆನ್ನಾಗಿ", "ठीक"),
}


def _urgency(text: str) -> int:
    lo = text.lower()
    if any(k in lo for k in URGENCY_5):
        return 5
    if any(k in lo for k in URGENCY_4):
        return 4
    if any(k in lo for k in URGENCY_3):
        return 3
    return 2


def _sentiment(text: str) -> str:
    lo = text.lower()
    for sentiment, keys in SENTIMENT_KEYS.items():
        if any(k in lo for k in keys):
            return sentiment
    return "neutral"


def _topic(text: str, language: str) -> str:
    """Strip leading honorifics + take the first 8–10 words as topic."""
    t = re.sub(r"^(sir|ಸಾರ್|ರೀ|भईया|जी|please)\s+", "", text.strip(), flags=re.I)
    parts = t.split()
    if not parts:
        return text[:60]
    head = " ".join(parts[:10])
    return head[:80].rstrip(",.;!?")


class HeuristicProvider:
    name = "heuristic"

    async def unified_turn(
        self,
        *,
        transcript: str,
        language: str,
        dialect_hint: str,
        history: list[str],
    ) -> UnifiedTurn:
        t0 = time.perf_counter()
        text = (transcript or "").strip()
        if not text:
            return UnifiedTurn(
                issue_summary="(no speech detected)",
                urgency=3, sentiment="confused", dialect="unknown",
                verify_phrase="ಕ್ಷಮಿಸಿ, ನಿಮ್ಮ ಮಾತು ಸ್ಪಷ್ಟವಾಗಿ ಕೇಳಿಸಲಿಲ್ಲ. ಮತ್ತೊಮ್ಮೆ ಹೇಳುವಿರಾ?",
                confidence=0.1,
                raw_meta={"provider": "heuristic", "latency_ms": 0.5},
            )

        dialect_result = _classifier.classify(text)
        dialect = dialect_result.dialect if dialect_result.dialect != "unknown" else (
            dialect_hint if dialect_hint in VERIFY_TEMPLATES else "unknown"
        )
        urgency = _urgency(text)
        sentiment = _sentiment(text)
        topic = _topic(text, language)

        if language.startswith("hi"):
            verify = HINDI_TEMPLATE.format(topic=topic)
        elif language.startswith("en"):
            verify = ENGLISH_TEMPLATE.format(topic=topic)
        else:
            verify = VERIFY_TEMPLATES.get(dialect, VERIFY_TEMPLATES["unknown"]).format(topic=topic)

        dt = (time.perf_counter() - t0) * 1000
        return UnifiedTurn(
            issue_summary=topic,  # rough English paraphrase fallback
            urgency=urgency,
            sentiment=sentiment,  # type: ignore[arg-type]
            dialect=dialect,  # type: ignore[arg-type]
            verify_phrase=verify,
            # Cap heuristic confidence — we honestly don't *understand*,
            # we just template. The FSM treats this as "good enough to
            # ask but always loops in a human eventually".
            confidence=min(0.6, 0.3 + dialect_result.confidence * 0.5),
            raw_meta={
                "provider": "heuristic",
                "latency_ms": round(dt, 2),
                "dialect_markers": dialect_result.markers_matched,
            },
        )

    async def close(self) -> None:
        return None
