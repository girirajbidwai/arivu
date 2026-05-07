"""
Arivu Brain Service — FastAPI entry point.

Stateless microservice with two endpoints:

    POST /process              one Brain decision per `turn_ended`
    POST /correction_received  agent-side override piped back from dashboard
    POST /handoff_brief        synthesise the handoff briefing card

The Brain is a deterministic state machine on top of Sarvam-M. The FSM is
the safety net — if Sarvam returns garbage or low confidence, the FSM
falls back to a hard handoff. An LLM never independently decides to keep
an at-risk citizen on the line.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from dotenv import load_dotenv

from .schemas import BrainInput, BrainOutput, CorrectionInput
from .fsm import VerificationFSM
from .llm import SarvamLLM, close_shared_client
from .dialect import DialectClassifier

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s │ %(name)s │ %(levelname)s │ %(message)s",
)
logger = logging.getLogger("arivu.brain")


# ── State: one FSM per active call ──────────────────────────────────────
active_calls: dict[str, VerificationFSM] = {}

# Lazy publisher import — voice/events/publisher.py emits to Supabase.
_publisher = None


def _get_publisher():
    global _publisher
    if _publisher is None:
        try:
            from voice.events.publisher import EventPublisher  # type: ignore
            _publisher = EventPublisher()
        except Exception as e:
            logger.warning("event publisher unavailable: %s", e)
            _publisher = False  # sentinel: tried and failed
    return _publisher or None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🧠 Arivu Brain starting — sarvam_key=%s",
                "***" if os.getenv("SARVAM_API_KEY") else "MISSING")
    yield
    await close_shared_client()
    logger.info("🧠 Arivu Brain shutting down — bye.")


app = FastAPI(title="Arivu Brain Service", version="0.1.0", lifespan=lifespan)

# ── Components (one per process) ────────────────────────────────────────
llm = SarvamLLM(api_key=os.getenv("SARVAM_API_KEY", ""))
dialect_classifier = DialectClassifier()


# ── Health ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "ok": True,
        "service": "arivu-brain",
        "version": "0.1.0",
        "active_calls": len(active_calls),
        "sarvam_configured": bool(os.getenv("SARVAM_API_KEY")),
    }


# ── Helpers ─────────────────────────────────────────────────────────────
def _get_or_create_fsm(call_id: str) -> VerificationFSM:
    fsm = active_calls.get(call_id)
    if fsm is None:
        fsm = VerificationFSM(call_id=call_id)
        active_calls[call_id] = fsm
        logger.info("call %s — new FSM created", call_id)
    return fsm


async def _publish(event: dict) -> None:
    """Fire-and-forget event publish so we never block the brain hot path."""
    pub = _get_publisher()
    if pub is None:
        return
    try:
        await pub.publish(event)
    except Exception as e:  # pragma: no cover — observability path
        logger.debug("event publish failed (non-fatal): %s", e)


_ALLOWED_DIALECTS = {"dharwad", "mangaluru", "mysuru", "bengaluru", "unknown"}
_ALLOWED_SENTIMENTS = {"neutral", "confused", "anxious", "fearful", "distressed", "calm"}

# ── Language-aware system phrases ───────────────────────────────────────
# Pre-written in all 3 supported languages so we never need an extra LLM
# call for system-level responses. Faster + more reliable than generating.
SYSTEM_PHRASES = {
    "handoff_confirmed": {
        "kn-IN": "ಧನ್ಯವಾದ. ನಿಮ್ಮನ್ನು ಈಗ ಅಧಿಕಾರಿಯೊಂದಿಗೆ ಸಂಪರ್ಕಿಸುತ್ತೇನೆ.",
        "hi-IN": "धन्यवाद। अब आपको एक अधिकारी से जोड़ रहा हूँ।",
        "en-IN": "Thank you. Connecting you with an officer now.",
    },
    "handoff_max_loops": {
        "kn-IN": "ಕ್ಷಮಿಸಿ, ನಿಮ್ಮನ್ನು ಅಧಿಕಾರಿಯೊಂದಿಗೆ ಸಂಪರ್ಕಿಸುತ್ತೇನೆ.",
        "hi-IN": "क्षमा करें, आपको अधिकारी से जोड़ रहा हूँ।",
        "en-IN": "Sorry, let me connect you with an officer.",
    },
    "denial_reprompt": {
        "kn-IN": "ಕ್ಷಮಿಸಿ. ದಯವಿಟ್ಟು ನಿಮ್ಮ ಸಮಸ್ಯೆಯನ್ನು ಇನ್ನೊಮ್ಮೆ ಸ್ವಲ್ಪ ವಿವರವಾಗಿ ಹೇಳಿ.",
        "hi-IN": "क्षमा करें। कृपया अपनी समस्या थोड़ा विस्तार से दोबारा बताइए।",
        "en-IN": "Sorry about that. Could you please describe your issue again in a bit more detail?",
    },
    "greeting": {
        "kn-IN": "ನಮಸ್ಕಾರ, ಇದು ಅರಿವು, 1092 ಸಹಾಯವಾಣಿ. ನಿಮ್ಮ ಸಮಸ್ಯೆಯನ್ನು ಹೇಳಿ.",
        "hi-IN": "नमस्ते, यह अरिवु है, 1092 हेल्पलाइन। अपनी समस्या बताइए।",
        "en-IN": "Hello, this is Arivu from the 1092 helpline. Please describe your issue.",
    },
}


def _system_phrase(key: str, language: str) -> str:
    """Look up a pre-written phrase in the caller's language."""
    phrases = SYSTEM_PHRASES.get(key, {})
    return phrases.get(language, phrases.get("en-IN", ""))


def _clamp_urgency(value: object, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if 1 <= parsed <= 5 else fallback


def _snapshot_from_intent(intent, *, dialect: str) -> dict[str, object]:
    return {
        "issue_summary": intent.issue_summary,
        "urgency": intent.urgency,
        "sentiment": intent.sentiment,
        "confidence": intent.confidence,
        "dialect": dialect,
    }


def _apply_corrections(fsm: VerificationFSM, snapshot: dict[str, object]) -> dict[str, object]:
    corrected = dict(snapshot)
    if "issue_summary" in fsm.corrections:
        corrected["issue_summary"] = fsm.corrections["issue_summary"]
    if "urgency" in fsm.corrections:
        corrected["urgency"] = _clamp_urgency(fsm.corrections["urgency"], int(snapshot["urgency"]))
    if "dialect" in fsm.corrections and fsm.corrections["dialect"] in _ALLOWED_DIALECTS:
        corrected["dialect"] = fsm.corrections["dialect"]
    if "sentiment" in fsm.corrections and fsm.corrections["sentiment"] in _ALLOWED_SENTIMENTS:
        corrected["sentiment"] = fsm.corrections["sentiment"]
    return corrected


def _brief_denial(transcript: str) -> bool:
    lo = (transcript or "").strip().lower()
    if len(lo) > 18:
        return False
    return any(tok in lo for tok in ("illa", "ಇಲ್ಲ", "nahin", "नहीं", "no", "wrong", "ಅಲ್ಲ"))


# ── /process ────────────────────────────────────────────────────────────
@app.post("/process", response_model=BrainOutput)
async def process(input: BrainInput) -> BrainOutput:
    """
    Single Brain decision. Hot path — measure every stage.

    Order of operations (latency-conscious):
      1. Pull / construct FSM           (sync, ~µs)
      2. If safety flag → hard handoff  (sync, ~µs)
      3. Concurrently: dialect + intent (async, dominates latency)
      4. FSM transition + branch
      5. If branch needs verify_phrase → 1 more Sarvam call
    """
    t_start = time.perf_counter()
    fsm = _get_or_create_fsm(input.call_id)
    latency: dict[str, float] = {}

    # ── Step 0: safety override ────────────────────────────────────────
    if input.safety_flags:
        trigger = input.safety_flags[0]
        fsm.force_handoff(reason=f"safety_{trigger}")
        logger.warning("call %s — safety trigger %s → forced handoff",
                       input.call_id, trigger)
        latency["total_ms"] = (time.perf_counter() - t_start) * 1000
        out = BrainOutput(
            action="handoff",
            language_out=input.language or "kn-IN",
            dialect_out=input.dialect or "unknown",
            text="",  # silent covert handoff
            issue_summary=input.history[-1] if input.history else "",
            urgency_1_to_5=5,
            sentiment="distressed",
            confidence=1.0,
            fsm_state="handoff",
            handoff_reason=f"safety_{trigger}",
            reasoning_trace=f"Safety override: {trigger}",
            latency_ms=latency,
        )
        await _publish({
            "event_type": "safety_alert",
            "call_id": input.call_id,
            "turn_index": input.turn_index,
            "data": {"safety_trigger": trigger, "fsm_state": "handoff"},
        })
        return out

    # ── Step 1: ONE call returns dialect + intent + verify_phrase ──────
    if _USE_UNIFIED:
        dialect_result, intent, unified_verify_phrase, sub_latency = await _unified_pieces(input)
        latency.update(sub_latency)
    else:
        # Legacy fallback path — kept for one release as a safety net.
        t1 = time.perf_counter()
        dialect_task = asyncio.to_thread(dialect_classifier.classify, input.transcript)
        intent_task = llm.extract_intent(
            transcript=input.transcript,
            language=input.language or "kn-IN",
            history=input.history,
        )
        dialect_result, intent = await asyncio.gather(dialect_task, intent_task)
        unified_verify_phrase = ""
        latency["dialect_intent_ms"] = (time.perf_counter() - t1) * 1000
    fresh_snapshot = _apply_corrections(
        fsm,
        _snapshot_from_intent(intent, dialect=dialect_result.dialect),
    )

    # If LLM hard-failed (parse_failed urgency=5 confidence=0), force handoff.
    if intent.issue_summary == "parse_failed" and intent.confidence < 0.05:
        fsm.force_handoff(reason="parse_failed")
        latency["total_ms"] = (time.perf_counter() - t_start) * 1000
        return BrainOutput(
            action="handoff",
            language_out=input.language or "kn-IN",
            dialect_out=dialect_result.dialect,
            text="",
            issue_summary="parse_failed",
            urgency_1_to_5=5,
            sentiment="confused",
            confidence=0.0,
            fsm_state="handoff",
            handoff_reason="parse_failed",
            reasoning_trace="LLM returned malformed JSON twice — handing off.",
            latency_ms=latency,
        )

    # ── Step 2: branch on FSM state ────────────────────────────────────
    state_before = fsm.state

    if state_before == "pending":
        fsm.set_pending_interpretation(
            issue_summary=str(fresh_snapshot["issue_summary"]),
            urgency=int(fresh_snapshot["urgency"]),
            sentiment=str(fresh_snapshot["sentiment"]),
            confidence=float(fresh_snapshot["confidence"]),
            dialect=str(fresh_snapshot["dialect"]),
        )
        # Use the unified verify_phrase if we have one (sub-1s path);
        # otherwise legacy generator (kept for the BRAIN_USE_UNIFIED=0 escape).
        verify_phrase = unified_verify_phrase or await _verify_phrase(
            input,
            issue=str(fresh_snapshot["issue_summary"]),
            dialect=str(fresh_snapshot["dialect"]),
        )
        fsm.transition("verify_sent")
        latency["total_ms"] = (time.perf_counter() - t_start) * 1000
        out = BrainOutput(
            action="verify",
            language_out=input.language or "kn-IN",
            dialect_out=str(fresh_snapshot["dialect"]),
            text=verify_phrase,
            issue_summary=str(fresh_snapshot["issue_summary"]),
            urgency_1_to_5=int(fresh_snapshot["urgency"]),
            sentiment=str(fresh_snapshot["sentiment"]),
            confidence=float(fresh_snapshot["confidence"]),
            fsm_state=fsm.state,
            reasoning_trace=(
                f"pending→verifying  dialect={fresh_snapshot['dialect']} "
                f"({dialect_result.confidence:.0%}) urgency={fresh_snapshot['urgency']}"
            ),
            latency_ms=latency,
        )
        await _publish({
            "event_type": "verification_pending",
            "call_id": input.call_id,
            "turn_index": input.turn_index,
            "data": {
                "verify_phrase": verify_phrase,
                "dialect": fresh_snapshot["dialect"],
                "dialect_confidence": dialect_result.confidence,
                "issue_summary": fresh_snapshot["issue_summary"],
                "urgency": fresh_snapshot["urgency"],
                "sentiment": fresh_snapshot["sentiment"],
                "fsm_state": fsm.state,
            },
        })
        return out

    if state_before == "verifying":
        active_snapshot = _apply_corrections(fsm, fsm.pending_interpretation or fresh_snapshot)
        # Step: did the citizen say houdu / illa?
        t2 = time.perf_counter()
        confirmation = await llm.check_confirmation(
            transcript=input.transcript,
            language=input.language or "kn-IN",
        )
        latency["confirmation_ms"] = (time.perf_counter() - t2) * 1000

        if confirmation.confirmed:
            fsm.set_pending_interpretation(
                issue_summary=str(active_snapshot["issue_summary"]),
                urgency=int(active_snapshot["urgency"]),
                sentiment=str(active_snapshot["sentiment"]),
                confidence=float(active_snapshot["confidence"]),
                dialect=str(active_snapshot["dialect"]),
            )
            fsm.mark_verified()
            fsm.transition("confirmed")
            verified_snapshot = _apply_corrections(
                fsm,
                fsm.verified_interpretation or active_snapshot,
            )
            lang_out = input.language or "kn-IN"
            latency["total_ms"] = (time.perf_counter() - t_start) * 1000
            out = BrainOutput(
                action="handoff",
                language_out=lang_out,
                dialect_out=str(verified_snapshot["dialect"]),
                text=_system_phrase("handoff_confirmed", lang_out),
                issue_summary=str(verified_snapshot["issue_summary"]),
                urgency_1_to_5=int(verified_snapshot["urgency"]),
                sentiment=str(verified_snapshot["sentiment"]),
                confidence=max(float(verified_snapshot["confidence"]), confirmation.confidence),
                fsm_state=fsm.state,
                handoff_reason="verification_complete",
                reasoning_trace="verifying→verified→handoff (citizen confirmed).",
                latency_ms=latency,
            )
            await _publish({
                "event_type": "verification_confirmed",
                "call_id": input.call_id,
                "turn_index": input.turn_index,
                "data": {
                    "issue_summary": verified_snapshot["issue_summary"],
                    "urgency": verified_snapshot["urgency"],
                    "dialect": verified_snapshot["dialect"],
                    "sentiment": verified_snapshot["sentiment"],
                    "confidence": verified_snapshot["confidence"],
                    "fsm_state": "verified",
                },
            })
            await _publish({
                "event_type": "handoff_initiated",
                "call_id": input.call_id,
                "turn_index": input.turn_index,
                "data": {"handoff_reason": "verification_complete"},
            })
            return out

        # Not confirmed — re-verify if we have loops left, else handoff.
        new_state = fsm.transition("re_verify")
        if new_state == "handoff":
            failed_snapshot = _apply_corrections(fsm, active_snapshot)
            lang_out = input.language or "kn-IN"
            latency["total_ms"] = (time.perf_counter() - t_start) * 1000
            out = BrainOutput(
                action="handoff",
                language_out=lang_out,
                dialect_out=str(failed_snapshot["dialect"]),
                text=_system_phrase("handoff_max_loops", lang_out),
                issue_summary=str(failed_snapshot["issue_summary"]),
                urgency_1_to_5=int(failed_snapshot["urgency"]),
                sentiment=str(failed_snapshot["sentiment"]),
                confidence=float(failed_snapshot["confidence"]),
                fsm_state="handoff",
                handoff_reason="max_verify_loops",
                reasoning_trace=f"verifying→handoff (loops={fsm.verify_count}).",
                latency_ms=latency,
            )
            await _publish({
                "event_type": "verification_failed",
                "call_id": input.call_id,
                "turn_index": input.turn_index,
                "data": {"handoff_reason": "max_verify_loops"},
            })
            return out

        # Re-verify with the citizen's *latest* utterance — they may have
        # corrected us with new information.
        if _brief_denial(input.transcript) and fsm.pending_interpretation:
            next_snapshot = _apply_corrections(fsm, fsm.pending_interpretation)
            verify_phrase = _system_phrase("denial_reprompt", input.language or "kn-IN")
        else:
            next_snapshot = fresh_snapshot
            verify_phrase = unified_verify_phrase or await _verify_phrase(
                input,
                issue=str(next_snapshot["issue_summary"]),
                dialect=str(next_snapshot["dialect"]),
            )
        fsm.set_pending_interpretation(
            issue_summary=str(next_snapshot["issue_summary"]),
            urgency=int(next_snapshot["urgency"]),
            sentiment=str(next_snapshot["sentiment"]),
            confidence=float(next_snapshot["confidence"]),
            dialect=str(next_snapshot["dialect"]),
        )
        latency["total_ms"] = (time.perf_counter() - t_start) * 1000
        out = BrainOutput(
            action="verify",
            language_out=input.language or "kn-IN",
            dialect_out=str(next_snapshot["dialect"]),
            text=verify_phrase,
            issue_summary=str(next_snapshot["issue_summary"]),
            urgency_1_to_5=int(next_snapshot["urgency"]),
            sentiment=str(next_snapshot["sentiment"]),
            confidence=float(next_snapshot["confidence"]),
            fsm_state=fsm.state,
            reasoning_trace=f"re-verify attempt {fsm.verify_count}/2.",
            latency_ms=latency,
        )
        return out

    if state_before == "verified":
        verified_snapshot = _apply_corrections(
            fsm,
            fsm.verified_interpretation or fsm.pending_interpretation or fresh_snapshot,
        )
        latency["total_ms"] = (time.perf_counter() - t_start) * 1000
        return BrainOutput(
            action="interpret",
            language_out=input.language or "kn-IN",
            dialect_out=str(verified_snapshot["dialect"]),
            text="",
            issue_summary=str(verified_snapshot["issue_summary"]),
            urgency_1_to_5=int(verified_snapshot["urgency"]),
            sentiment=str(verified_snapshot["sentiment"]),
            confidence=float(verified_snapshot["confidence"]),
            fsm_state="verified",
            reasoning_trace="In interpreter mode.",
            latency_ms=latency,
        )

    # Terminal handoff — keep returning the same shape.
    terminal_snapshot = _apply_corrections(
        fsm,
        fsm.verified_interpretation or fsm.pending_interpretation or fresh_snapshot,
    )
    latency["total_ms"] = (time.perf_counter() - t_start) * 1000
    return BrainOutput(
        action="handoff",
        language_out=input.language or "kn-IN",
        dialect_out=str(terminal_snapshot["dialect"]),
        text="",
        issue_summary=str(terminal_snapshot["issue_summary"]),
        urgency_1_to_5=int(terminal_snapshot["urgency"]),
        sentiment=str(terminal_snapshot["sentiment"]),
        confidence=1.0,
        fsm_state="handoff",
        handoff_reason=fsm.handoff_reason or "terminal",
        reasoning_trace="Already in handoff.",
        latency_ms=latency,
    )


# ── /correction_received ───────────────────────────────────────────────
@app.post("/correction_received")
async def correction_received(correction: CorrectionInput):
    fsm = active_calls.get(correction.call_id)
    if fsm is None:
        fsm = _get_or_create_fsm(correction.call_id)
    fsm.add_correction(correction.field, correction.new_value)
    return {"status": "acknowledged", "call_id": correction.call_id}


# ── /handoff_brief ─────────────────────────────────────────────────────
@app.post("/handoff_brief")
async def handoff_brief(input: BrainInput):
    """Returns the headline + key facts an agent reads on handoff."""
    fsm = active_calls.get(input.call_id) or _get_or_create_fsm(input.call_id)
    brief = await llm.summarise_handoff(
        call_id=input.call_id,
        dialect=input.dialect or "unknown",
        language=input.language or "kn-IN",
        sentiment="distressed",  # the dashboard knows the latest sentiment
        urgency=5 if fsm.state == "handoff" else 3,
        issue_summary=fsm.corrections.get("issue_summary", ""),
        reason=fsm.handoff_reason or "verification_complete",
        history=input.history,
    )
    return brief


# ── helpers ─────────────────────────────────────────────────────────────
async def _verify_phrase(
    input: BrainInput,
    *,
    issue: str,
    dialect: str,
) -> str:
    return await llm.generate_verify_phrase(
        issue=issue,
        dialect=dialect,
        original_utterance=input.transcript,
        language=input.language or "kn-IN",
    )


# ── Unified-turn integration (Gemini-ready, sub-1s hot path) ───────────
# A single LLM call returns intent + dialect + verify_phrase in one
# round-trip. Replaces the legacy dialect_classifier+extract_intent+
# generate_verify_phrase cascade. Toggleable via BRAIN_USE_UNIFIED — set
# to "0" to fall back to the old chain (kept as a safety net).
from .providers import (  # noqa: E402  (deliberate late import to avoid SDK cost at top)
    get_llm_provider,
    get_shadow_provider,
    ProviderUnavailable,
)
from .schemas import DialectResult, IntentResult  # noqa: E402

_USE_UNIFIED = os.getenv("BRAIN_USE_UNIFIED", "1").lower() not in ("0", "false", "no")


async def _shadow_compare(turn, *, transcript: str, language: str, dialect_hint: str, history: list[str]) -> None:
    """
    Optional async quality check against a secondary provider. Never on
    the critical path — runs as a fire-and-forget task. If the shadow
    disagrees with the primary on intent or urgency we publish a
    `quality_check` event so the dashboard can flag the call.
    """
    shadow = get_shadow_provider()
    if shadow is None:
        return

    async def _run() -> None:
        try:
            other = await shadow.unified_turn(
                transcript=transcript, language=language,
                dialect_hint=dialect_hint, history=history,
            )
        except Exception as exc:
            logger.debug("shadow provider failed (non-fatal): %s", exc)
            return
        diverged = (
            abs(other.urgency - turn.urgency) >= 2
            or other.dialect != turn.dialect
            or (other.confidence > 0.7 and turn.confidence < 0.4)
        )
        if diverged:
            logger.info(
                "shadow diverged: primary=%s/%s/%d shadow=%s/%s/%d",
                turn.dialect, turn.sentiment, turn.urgency,
                other.dialect, other.sentiment, other.urgency,
            )
            # Track divergence
            try:
                from voice.telemetry import tracker
                tracker.record({"divergence": 1.0})
            except Exception:
                pass
            await _publish({
                "event_type": "quality_check",
                "call_id": "n/a",  # caller can re-emit with the real call_id
                "data": {
                    "primary":  turn.model_dump(exclude={"raw_meta"}),
                    "shadow":   other.model_dump(exclude={"raw_meta"}),
                    "diverged": True,
                },
            })

    asyncio.create_task(_run())


async def _unified_pieces(
    input: BrainInput,
) -> tuple[DialectResult, IntentResult, str, dict]:
    """
    Active brain front door. Returns (dialect_result, intent, verify_phrase, latency)
    so the rest of `process()` keeps its existing shape. ONE LLM call
    instead of three.
    """
    latency: dict[str, float] = {}

    # 1. Local dialect classifier (5 ms) — gives the LLM a hint and acts
    #    as a fallback if the LLM omits dialect.
    t0 = time.perf_counter()
    local_dialect = await asyncio.to_thread(
        dialect_classifier.classify, input.transcript
    )
    latency["dialect_local_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    provider = get_llm_provider()
    t1 = time.perf_counter()
    try:
        turn = await provider.unified_turn(
            transcript=input.transcript,
            language=input.language or "kn-IN",
            dialect_hint=local_dialect.dialect,
            history=input.history,
        )
    except ProviderUnavailable as e:
        logger.warning("provider %s failed (%s) — heuristic fallback", provider.name, e)
        from .providers.heuristic import HeuristicProvider
        turn = await HeuristicProvider().unified_turn(
            transcript=input.transcript,
            language=input.language or "kn-IN",
            dialect_hint=local_dialect.dialect,
            history=input.history,
        )
    latency["unified_ms"] = round((time.perf_counter() - t1) * 1000, 1)

    # Reconcile dialect: prefer provider's pick when it's not 'unknown',
    # else fall back to the local classifier's result.
    dialect = turn.dialect if turn.dialect != "unknown" else local_dialect.dialect
    dialect_result = DialectResult(
        dialect=dialect,  # type: ignore[arg-type]
        confidence=max(turn.confidence, local_dialect.confidence),
        markers_matched=local_dialect.markers_matched,
    )
    intent = IntentResult(
        issue_summary=turn.issue_summary,
        urgency=turn.urgency,
        sentiment=turn.sentiment,
        confidence=turn.confidence,
    )

    # Fire-and-forget shadow quality check (no await).
    await _shadow_compare(
        turn,
        transcript=input.transcript,
        language=input.language or "kn-IN",
        dialect_hint=local_dialect.dialect,
        history=input.history,
    )

    return dialect_result, intent, turn.verify_phrase, latency
