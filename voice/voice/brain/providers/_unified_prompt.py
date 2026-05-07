"""
Single source of truth for the unified-turn prompt.

We reuse the same prompt across providers so a Sarvam→Gemini swap doesn't
silently change behaviour. Examples are intentionally Indian-language
heavy because that's where the model needs the most calibration.
"""

from __future__ import annotations

UNIFIED_SYSTEM = """You are Arivu, the AI listener on Karnataka's 1092 government helpline.
Your single job per turn is to UNDERSTAND the citizen — never to respond to their problem,
never to give advice. You produce a structured interpretation that a human officer will use.

Hard rules:
- Verify, don't assume. Your `verify_phrase` is a one-line *paraphrase* of the citizen's
  most recent utterance asking them to confirm. It is read back to the citizen verbatim.
- Match the citizen's register. If they used Dharwad Kannada (ರೀ, ಎನ್ನಾ, ಮಾಡ್ಬೇಕ),
  echo Dharwad Kannada. If Hindi, reply in Hindi. If English/Hinglish, English/Hinglish.
- Strip PII from `issue_summary`: no names, addresses, phone numbers.
- urgency is conservative: 1=casual, 3=routine complaint, 4=distress, 5=imminent danger.
- If you genuinely cannot understand the utterance, set confidence < 0.4 and use a
  reprompt as the verify_phrase ("ಕ್ಷಮಿಸಿ, ಇನ್ನೊಮ್ಮೆ ಹೇಳುವಿರಾ ರೀ?").
"""

UNIFIED_USER_TEMPLATE = """Citizen language code: {language}
Dialect hint (best guess): {dialect_hint}

Recent context (oldest → newest, citizen turns only):
{history}

Citizen's most recent utterance (verbatim):
\"\"\"
{transcript}
\"\"\"

Return JSON exactly matching this schema (no markdown fence, no prose):
{
  "verify_phrase": "<one-line confirmation question in citizen's language and register>",
  "issue_summary": "<English paraphrase, ≤14 words, no PII>",
  "urgency": <integer 1..5>,
  "sentiment": "<one of: neutral|confused|anxious|fearful|distressed|calm>",
  "dialect": "<one of: dharwad|mangaluru|mysuru|bengaluru|unknown>",
  "confidence": <float 0..1>
}"""


def build_messages(*, transcript: str, language: str, dialect_hint: str, history: list[str]) -> list[dict]:
    history_str = (
        "\n".join(f"- {h}" for h in (history or [])[-6:]) if history else "(none)"
    )
    return [
        {"role": "system", "content": UNIFIED_SYSTEM},
        {
            "role": "user",
            "content": UNIFIED_USER_TEMPLATE.format(
                language=language,
                dialect_hint=dialect_hint or "unknown",
                history=history_str,
                transcript=transcript,
            ),
        },
    ]


# Schema dict reused by Gemini's controlled-generation feature and as a
# Pydantic source-of-truth comment for the Sarvam path.
UNIFIED_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["verify_phrase", "issue_summary", "urgency", "sentiment", "dialect", "confidence"],
    "properties": {
        "verify_phrase": {"type": "string"},
        "issue_summary": {"type": "string"},
        "urgency": {"type": "integer", "minimum": 1, "maximum": 5},
        "sentiment": {
            "type": "string",
            "enum": ["neutral", "confused", "anxious", "fearful", "distressed", "calm"],
        },
        "dialect": {
            "type": "string",
            "enum": ["dharwad", "mangaluru", "mysuru", "bengaluru", "unknown"],
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
}
