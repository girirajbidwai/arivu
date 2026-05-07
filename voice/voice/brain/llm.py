"""
Sarvam-M LLM client.

Uses Sarvam's OpenAI-compatible chat-completions endpoint. One persistent
httpx.AsyncClient is shared across all calls to keep TLS + HTTP/2 sessions
warm — that single change knocks 80–150 ms off p50 latency vs. spawning
a fresh client per request.

Modes:
  * `non-think` — fast, no chain-of-thought, JSON-mode output. Default.
  * `think`     — reserved for offline batch eval, not the live path.

Every method validates JSON output against a Pydantic model and falls
back deterministically on parse failure. The FSM treats parse-failed
responses as `safety_override`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

import httpx
from pydantic import BaseModel, ValidationError

from .schemas import IntentResult, Sentiment

logger = logging.getLogger("arivu.brain.llm")

SARVAM_BASE_URL = os.getenv("SARVAM_BASE_URL", "https://api.sarvam.ai")
SARVAM_CHAT_PATH = "/v1/chat/completions"
# `sarvam-30b` returns reasoning and answer in separate fields
# (`reasoning_content` vs `content`), runs ~2s per call, and is what
# Sarvam currently exposes in their model catalogue. The legacy
# `sarvam-m` returned inline `<think>` blocks that we still strip
# defensively for backwards compatibility.
SARVAM_MODEL = os.getenv("SARVAM_MODEL", "sarvam-30b")

PROMPTS_DIR = Path(__file__).parent / "prompts"


# ── Singleton async client ──────────────────────────────────────────────
# httpx clients are expensive to construct (TLS handshake, connection
# pool init, HTTP/2 settings frame). Share one across the process.
_client_lock = asyncio.Lock()
_shared_client: Optional[httpx.AsyncClient] = None


async def get_shared_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None:
        async with _client_lock:
            if _shared_client is None:
                _shared_client = httpx.AsyncClient(
                    base_url=SARVAM_BASE_URL,
                    # Sarvam-30b's reasoning step can run 8–15 s on long
                    # Indic prompts; we keep an aggressive read timeout
                    # but still bound it so a hung server can't stall.
                    timeout=httpx.Timeout(connect=2.0, read=30.0, write=5.0, pool=2.0),
                    limits=httpx.Limits(
                        max_connections=20,
                        max_keepalive_connections=10,
                        keepalive_expiry=30.0,
                    ),
                    http2=True,
                )
                logger.info("sarvam shared client created — base=%s model=%s",
                            SARVAM_BASE_URL, SARVAM_MODEL)
    return _shared_client


async def close_shared_client() -> None:
    global _shared_client
    if _shared_client is not None:
        await _shared_client.aclose()
        _shared_client = None


# ── Prompt loader (cached) ──────────────────────────────────────────────
def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / name
    return path.read_text(encoding="utf-8")


# Pre-load at import time. Tiny files, prompts almost never change at runtime.
_PROMPTS = {
    "intent_extract": _load_prompt("intent_extract.txt"),
    "verify_phrase": _load_prompt("verify_phrase.txt"),
    "confirmation_check": _load_prompt("confirmation_check.txt"),
    "handoff_summary": _load_prompt("handoff_summary.txt"),
}


# ── Sarvam-M output cleanup ─────────────────────────────────────────────
# Sarvam-M emits an internal reasoning block as <think>...</think> before
# the actual answer. Strip it everywhere — both the JSON parsers and the
# free-text generators must see clean output.
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _strip_think(text: str) -> str:
    if not text:
        return ""
    cleaned = _THINK_RE.sub("", text)
    # Defensive: if the close tag is missing (truncation), drop everything
    # up to the last newline that might belong to think.
    if "<think>" in cleaned and "</think>" not in cleaned:
        # Truncated think block — content after a final closing tag is gone.
        return ""
    return cleaned.strip()


def _extract_json(text: str) -> Optional[str]:
    """Strip think tags + code fences + leading prose; return first {..} object."""
    text = _strip_think(text or "")
    if not text:
        return None
    fenced = _JSON_FENCE.search(text)
    if fenced:
        return fenced.group(1)
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                return text[start:i + 1]
    return None


# ── ConfirmationResult tiny model ───────────────────────────────────────
class _ConfirmationResult(BaseModel):
    confirmed: bool
    confidence: float = 0.7


# ── The client ──────────────────────────────────────────────────────────
class SarvamLLM:
    """
    Stateless wrapper around Sarvam-M. Constructed once per process.
    All public methods are async, idempotent, and return validated objects.
    """

    def __init__(self, api_key: str, model: str = SARVAM_MODEL) -> None:
        self.api_key = api_key
        self.model = model
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._enabled = bool(api_key)
        if not self._enabled:
            logger.warning("SARVAM_API_KEY not set — LLM client running in stub mode.")

    # ── Core call ───────────────────────────────────────────────────────
    async def _chat(
        self,
        prompt: str,
        *,
        max_tokens: int = 2000,
        temperature: float = 0.2,
        json_mode: bool = True,
    ) -> str:
        """
        Single chat-completion call. Returns the assistant's `content`
        field (already clean on sarvam-30b/105b — the reasoning lives in
        a separate `reasoning_content` field). Falls back to stripping
        `<think>...</think>` for backwards compatibility with older
        sarvam-m responses.

        max_tokens has to cover Sarvam-30b's reasoning + the final answer.
        Empirically: ~600 tokens of reasoning for short prompts, 1200+
        for prompts with examples. 2000 is the safe floor.
        """
        if not self._enabled:
            return ""

        client = await get_shared_client()
        body: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        # Don't set response_format on sarvam-30b — it triggers an output
        # path that drops content when reasoning hits the budget. We
        # parse JSON downstream from the clean `content` field.

        t0 = time.perf_counter()
        try:
            r = await client.post(SARVAM_CHAT_PATH, json=body, headers=self._headers)
            r.raise_for_status()
            data = r.json()
            msg = data["choices"][0]["message"]
            # Prefer the new clean content field; fall back to stripping
            # legacy <think> blocks if a model still returns inline thought.
            raw = msg.get("content") or ""
            if raw and ("<think>" in raw):
                content = _strip_think(raw)
            else:
                content = (raw or "").strip()
            usage = data.get("usage", {})
            dt = (time.perf_counter() - t0) * 1000
            logger.info(
                "sarvam ok in %.0fms (model=%s · prompt=%d · think=%d · answer=%d)",
                dt, self.model,
                usage.get("prompt_tokens", 0),
                len(msg.get("reasoning_content") or ""),
                len(content),
            )
            return content
        except (httpx.HTTPError, KeyError, IndexError) as e:
            dt = (time.perf_counter() - t0) * 1000
            logger.error("sarvam call failed after %.0fms: %s", dt, e)
            return ""

    # ── High-level methods ──────────────────────────────────────────────

    async def extract_intent(
        self,
        transcript: str,
        language: str = "kn-IN",
        history: Optional[list[str]] = None,
    ) -> IntentResult:
        """
        One Sarvam-M call → structured IntentResult.
        Two retries on parse failure; fallback returns urgency=5 so the
        FSM forces a safe handoff.
        """
        if not transcript.strip():
            return IntentResult(
                issue_summary="unintelligible", urgency=3,
                sentiment="confused", confidence=0.1,
            )

        prompt = _PROMPTS["intent_extract"].format(
            transcript=transcript,
            language=language,
            history="\n".join(f"- {h}" for h in (history or [])[-5:]) or "(none)",
        )

        for attempt in range(2):
            raw = await self._chat(prompt, max_tokens=700, temperature=0.1)
            parsed = _extract_json(raw)
            if parsed is None:
                continue
            try:
                obj = json.loads(parsed)
                return IntentResult(**obj)
            except (json.JSONDecodeError, ValidationError) as e:
                logger.warning("intent parse failed (attempt %d): %s — raw=%r",
                               attempt + 1, e, raw[:200])

        # Fallback — "needs human" path. This is the safe default.
        return IntentResult(
            issue_summary="parse_failed",
            urgency=5,
            sentiment="confused",
            confidence=0.0,
        )

    async def generate_verify_phrase(
        self,
        issue: str,
        dialect: str,
        original_utterance: str,
        language: str = "kn-IN",
    ) -> str:
        """Free-text generation; one retry; fallback to a fixed handoff line."""
        prompt = _PROMPTS["verify_phrase"].format(
            language=language,
            dialect=dialect,
            issue=issue,
            utterance=original_utterance,
        )
        for _ in range(2):
            raw = await self._chat(prompt, max_tokens=600, temperature=0.4, json_mode=False)
            # _chat already strips <think> blocks; just clean up quotes.
            cleaned = (raw or "").strip().strip('"').strip("'")
            if cleaned:
                # First non-empty line only — Sarvam-M sometimes adds an
                # explanation after the answer. Keep just the first line.
                first = next((ln.strip() for ln in cleaned.splitlines() if ln.strip()), "")
                if first:
                    return first.strip('"').strip("'")

        # Fallback — generic confirmation phrase in the caller's language.
        _VERIFY_FALLBACK = {
            "kn-IN": "ನಾನು ನಿಮ್ಮ ಸಮಸ್ಯೆಯನ್ನು ಸರಿಯಾಗಿ ಅರ್ಥಮಾಡಿಕೊಂಡಿದ್ದೇನೆಯೇ?",
            "hi-IN": "क्या मैंने आपकी समस्या सही समझी है?",
            "en-IN": "Did I understand your issue correctly?",
        }
        return _VERIFY_FALLBACK.get(language, _VERIFY_FALLBACK["en-IN"])

    async def check_confirmation(
        self,
        transcript: str,
        language: str = "kn-IN",
    ) -> _ConfirmationResult:
        """Yes/no classifier on the citizen's reply to a verify phrase."""
        # Fast path — match obvious tokens before paying for an LLM call.
        lo = (transcript or "").strip().lower()
        if any(tok in lo for tok in ("houdu", "ಹೌದು", "haan", "हाँ", "yes", "yeah", "ಸರಿ", "ಸರೀ", "right")):
            return _ConfirmationResult(confirmed=True, confidence=0.95)
        if any(tok in lo for tok in ("illa", "ಇಲ್ಲ", "nahin", "नहीं", " no ", "wrong", "ಅಲ್ಲ", "ಬೇರೆ")):
            return _ConfirmationResult(confirmed=False, confidence=0.9)

        prompt = _PROMPTS["confirmation_check"].format(transcript=transcript)
        raw = await self._chat(prompt, max_tokens=500, temperature=0.0)
        parsed = _extract_json(raw or "")
        if parsed:
            try:
                return _ConfirmationResult(**json.loads(parsed))
            except (json.JSONDecodeError, ValidationError):
                pass
        # Default: not confirmed. Safer.
        return _ConfirmationResult(confirmed=False, confidence=0.2)

    async def summarise_handoff(
        self,
        *,
        call_id: str,
        dialect: str,
        language: str,
        sentiment: Sentiment,
        urgency: int,
        issue_summary: str,
        reason: str,
        history: list[str],
    ) -> dict:
        """Generate the briefing card the agent sees on handoff."""
        prompt = _PROMPTS["handoff_summary"].format(
            call_id=call_id,
            dialect=dialect,
            language=language,
            sentiment=sentiment,
            urgency=urgency,
            issue_summary=issue_summary,
            reason=reason,
            history="\n".join(f"- {h}" for h in history[-8:]) or "(none)",
        )
        raw = await self._chat(prompt, max_tokens=800, temperature=0.2)
        parsed = _extract_json(raw or "")
        if parsed:
            try:
                return json.loads(parsed)
            except json.JSONDecodeError:
                pass
        # Fallback — boring but safe.
        return {
            "headline": issue_summary or "Citizen requesting help",
            "key_facts": [f"Urgency {urgency}", f"Sentiment {sentiment}", f"Dialect {dialect}"],
            "recommended_next_step": "Greet the citizen and ask them to repeat the issue.",
        }
