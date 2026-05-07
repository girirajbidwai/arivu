"""
Arivu Voice Service — FastAPI entry point.

Endpoints:

  GET  /health      — liveness
  ANY  /twiml       — Twilio webhook (TELEPHONY_PENDING — wire later)
  WS   /ws          — Twilio Media Streams WebSocket (TELEPHONY_PENDING)
  POST /demo/turn   — synthetic text-only turn for dashboard dry runs
  POST /web/turn    — browser mic path: audio in → STT → Brain → TTS → audio out
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from voice.events.publisher import get_publisher
from voice.processors.brain_proxy import BrainProxy
from voice.processors.event_emitter import EventEmitter
from voice.processors.safety_triggers import SafetyTriggerProcessor
from voice.sarvam_speech import synthesize, transcribe
from voice.speech import get_tts_provider
from voice.telemetry import tracker as latency_tracker
from voice.transports.twilio_ws import build_twiml, parse_twilio_start

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s │ %(name)s │ %(levelname)s │ %(message)s",
)
logger = logging.getLogger("arivu.voice")

# ── Per-call session language memory ────────────────────────────────────
# Tracks the detected language across turns so we can fallback to the
# caller's established language, not a hardcoded kn-IN.
session_languages: dict[str, str] = {}

# Language-aware reprompt for when STT returns empty.
REPROMPT = {
    "kn-IN": "ಕ್ಷಮಿಸಿ, ನಾನು ಸ್ಪಷ್ಟವಾಗಿ ಕೇಳಿಸಲಿಲ್ಲ. ಮತ್ತೊಮ್ಮೆ ಹೇಳಿ.",
    "hi-IN": "माफ़ कीजिए, मैं ठीक से सुन नहीं पाया। कृपया फिर से बताइए।",
    "en-IN": "Sorry, I didn't catch that clearly. Could you please repeat?",
}


# ── Lifespan ────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🎙️  voice service starting (port %s)", os.getenv("PORT", "8000"))
    logger.info("    twilio sid: %s", (os.getenv("TWILIO_ACCOUNT_SID", "") or "MISSING")[:8] + "…")
    logger.info("    sarvam key: %s", "set" if os.getenv("SARVAM_API_KEY") else "MISSING")
    logger.info("    brain url:  %s", os.getenv("BRAIN_SERVICE_URL", "http://localhost:8002"))
    yield
    logger.info("🎙️  voice service shutting down")


app = FastAPI(title="Arivu Voice Service", version="0.1.0", lifespan=lifespan)

# Browser-mic mode runs cross-origin (Vercel front, Render back).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── WebSocket streaming path (low-latency) ─────────────────────────────
from voice.stream import handle_stream

@app.websocket("/web/stream")
async def ws_stream(ws: WebSocket):
    brain_url = os.getenv("BRAIN_SERVICE_URL", "http://localhost:8002")
    await handle_stream(ws, brain_url)


# ── Health ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "service": "arivu-voice",
        "version": "0.2.0",
        "twilio_configured": bool(os.getenv("TWILIO_ACCOUNT_SID")),
        "sarvam_configured": bool(os.getenv("SARVAM_API_KEY")),
        "gcp_configured": bool(os.getenv("GOOGLE_CLOUD_PROJECT")),
        "providers": {
            "brain": os.getenv("BRAIN_LLM_PROVIDER", "auto"),
            "stt": os.getenv("STT_PROVIDER", "auto"),
            "tts": os.getenv("TTS_PROVIDER", "sarvam"),
        },
    }


@app.get("/health/latency")
async def health_latency() -> dict:
    """Live latency telemetry — p50/p95 per pipeline stage."""
    snap = latency_tracker.snapshot()
    # Calculate sub-1s rate from e2e samples
    stages = snap.get("stages", {})
    e2e = stages.get("total_ms", {})
    sub_1s_rate = 0.0
    if e2e:
        p50 = e2e.get("p50_ms", 9999)
        sub_1s_rate = 1.0 if p50 < 1000 else 0.5 if p50 < 1500 else 0.0
    snap["sub_1s_rate"] = sub_1s_rate
    snap["providers"] = {
        "brain": os.getenv("BRAIN_LLM_PROVIDER", "auto"),
        "stt": os.getenv("STT_PROVIDER", "auto"),
        "tts": os.getenv("TTS_PROVIDER", "sarvam"),
    }
    return snap


# ── /twiml ─────────────────────────────────────────────────────────────
@app.api_route("/twiml", methods=["GET", "POST"])
async def twiml(request: Request) -> Response:
    """
    Twilio hits this URL when a citizen calls our number. We tell Twilio
    to open a Media Stream WebSocket back to /ws.
    """
    forced_host = os.getenv("NGROK_DOMAIN") or os.getenv("PUBLIC_HOST")
    host = forced_host or request.headers.get("host") or "localhost:8000"
    protocol = "wss" if any(t in host for t in ("ngrok", "render", "fly", "vercel")) else "ws"
    body = build_twiml(ws_url=f"{protocol}://{host}/ws")
    return Response(content=body, media_type="application/xml")


# ── /ws — Twilio Media Streams ─────────────────────────────────────────
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    """
    Bidirectional Twilio Media Stream WebSocket.

    We attempt to construct a full Pipecat pipeline. If Pipecat or its
    Sarvam adapters aren't installed (e.g. dev environment without GPU
    stacks), we fall back to a "metadata only" mode that still emits
    lifecycle events to the dashboard so the rest of the system can be
    demoed end-to-end.
    """
    await ws.accept()
    call_id = str(uuid.uuid4())
    logger.info("📞 new call %s", call_id)

    emitter = EventEmitter(call_id=call_id, publisher=get_publisher())
    brain = BrainProxy()
    safety = SafetyTriggerProcessor()
    history: list[str] = []

    await emitter.call_started()

    try:
        # ── Try the full pipeline first ────────────────────────────────
        try:
            from voice.pipeline import create_pipeline
            runner, pipeline, ctx = await create_pipeline(ws, call_id)
        except Exception as e:
            logger.warning("pipeline build failed (%s) — running metadata-only mode", e)
            runner, pipeline, ctx = None, None, None

        if runner and pipeline:
            await runner.run(pipeline)
            return

        # ── Fallback: parse Twilio events, run brain on synthetic turns ─
        async for raw in ws.iter_text():
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            ev = msg.get("event")
            if ev == "connected":
                logger.info("📞 %s twilio connected", call_id)
            elif ev == "start":
                start = parse_twilio_start(msg)
                if start:
                    logger.info("📞 %s stream %s call %s",
                                call_id, start.stream_sid, start.call_sid)
            elif ev == "media":
                # No STT here in fallback mode; nothing to do.
                pass
            elif ev == "mark":
                pass
            elif ev == "stop":
                logger.info("📞 %s stream stopped", call_id)
                break

    except WebSocketDisconnect:
        logger.info("📞 %s disconnected", call_id)
    except Exception as e:
        logger.exception("ws error on call %s: %s", call_id, e)
    finally:
        await emitter.call_ended()
        try:
            await brain.close()
        except Exception:
            pass


# ── /demo/turn — synthetic turn for dashboard dry-runs ─────────────────
class DemoTurnRequest(BaseModel):
    call_id: Optional[str] = None
    transcript: str
    language: str = "kn-IN"
    dialect: Optional[str] = None
    history: list[str] = []
    safety_flags: list[str] = []


@app.post("/demo/turn")
async def demo_turn(req: DemoTurnRequest) -> JSONResponse:
    """
    Fire a single synthetic turn through the brain — used by demo runs
    where we want to test verification + dashboard wiring without dialing
    a real phone. Emits the same events as a real Twilio call.
    """
    call_id = req.call_id or str(uuid.uuid4())
    emitter = EventEmitter(call_id=call_id, publisher=get_publisher())
    brain = BrainProxy()

    await emitter.call_started(language=req.language)
    await emitter.turn_started(speaker="citizen")
    await emitter.transcript_final(
        transcript=req.transcript, speaker="citizen", language=req.language,
    )

    t0 = time.perf_counter()
    result = await brain.process(
        call_id=call_id,
        turn_index=1,
        transcript=req.transcript,
        language=req.language,
        dialect=req.dialect,
        safety_flags=req.safety_flags,
        history=req.history,
    )
    e2e_ms = (time.perf_counter() - t0) * 1000

    if result.get("dialect_out") and result["dialect_out"] != "unknown":
        await emitter.dialect_detected(
            dialect=result["dialect_out"],
            confidence=result.get("confidence", 0.7),
        )
    if result.get("sentiment"):
        await emitter.sentiment_updated(
            sentiment=result["sentiment"],
            confidence=result.get("confidence", 0.7),
        )
    action = result.get("action")

    await brain.close()
    return JSONResponse({
        "call_id": call_id,
        "e2e_ms": round(e2e_ms, 1),
        "brain": result,
    })


# ── /web/turn — browser mic path (no Twilio yet) ───────────────────────
#
# Browser captures mic via MediaRecorder, POSTs the audio blob here.
# Server pipes:  audio  →  Sarvam STT  →  Brain  →  Sarvam TTS  →  audio
# Same events.json envelopes are emitted, so the agent dashboard sees
# the call exactly as it would on a real phone leg.
#
# When Twilio is wired this stays — it's the canonical "demo without a
# phone" path and is what the website's "try it now" widget uses.
@app.post("/web/turn")
async def web_turn(
    audio: UploadFile = File(...),
    call_id: Optional[str] = Form(None),
    language: str = Form("unknown"),
    history: str = Form(""),  # newline-joined for simple form upload
) -> JSONResponse:
    cid = call_id or str(uuid.uuid4())
    hist = [h for h in history.split("\n") if h.strip()] if history else []
    audio_bytes = await audio.read()

    emitter = EventEmitter(call_id=cid, publisher=get_publisher())
    brain = BrainProxy()
    latency: dict[str, float] = {}
    t_total = time.perf_counter()

    if not hist:
        await emitter.call_started(language=language if language != "unknown" else "kn-IN")
    await emitter.turn_started(speaker="citizen")

    # 1. STT
    t0 = time.perf_counter()
    stt = await transcribe(
        audio_bytes,
        filename=audio.filename or "audio.webm",
        content_type=audio.content_type or "audio/webm",
        language_code=language,
    )
    latency["stt_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    transcript = stt["transcript"]

    # Language resolution priority:
    # 1. Explicit user selection (language param != "unknown")
    # 2. STT auto-detected language
    # 3. Session language from previous turns
    # 4. Kannada as the helpline default
    if language != "unknown" and language:
        detected_lang = language
    elif stt.get("language_code"):
        detected_lang = stt["language_code"]
    elif cid in session_languages:
        detected_lang = session_languages[cid]
    else:
        detected_lang = "kn-IN"  # 1092 is a Karnataka helpline

    # Persist for future turns in this session
    if detected_lang and detected_lang != "unknown":
        session_languages[cid] = detected_lang

    if not transcript:
        # Empty recording / STT fail — reprompt in the session language.
        session_lang = session_languages.get(cid, detected_lang)
        fallback_text = REPROMPT.get(session_lang, REPROMPT["en-IN"])
        tts = await synthesize(fallback_text, target_language_code=session_lang)
        latency["total_ms"] = round((time.perf_counter() - t_total) * 1000, 1)
        await brain.close()
        return JSONResponse({
            "call_id": cid,
            "transcript": "",
            "detected_language": session_lang,
            "action": "reprompt",
            "verify_phrase": fallback_text,
            "audio_b64": tts["audio_b64"],
            "audio_mime": tts["mime"],
            "history": hist,
            "latency_ms": latency,
        })

    await emitter.transcript_final(
        transcript=transcript, speaker="citizen", language=detected_lang,
    )

    # 2. Brain
    t1 = time.perf_counter()
    result = await brain.process(
        call_id=cid,
        turn_index=len(hist) + 1,
        transcript=transcript,
        language=detected_lang,
        history=hist,
    )
    latency["brain_ms"] = round((time.perf_counter() - t1) * 1000, 1)

    # Emit dashboard events
    if result.get("dialect_out") and result["dialect_out"] != "unknown":
        await emitter.dialect_detected(
            dialect=result["dialect_out"],
            confidence=result.get("confidence", 0.7),
        )
    if result.get("sentiment"):
        await emitter.sentiment_updated(
            sentiment=result["sentiment"],
            confidence=result.get("confidence", 0.7),
        )
    action = result.get("action", "verify")

    # 3. TTS the assistant text (verify phrase or handoff line)
    t2 = time.perf_counter()
    spoken_text = result.get("text") or ""
    if spoken_text:
        tts = await synthesize(
            spoken_text,
            target_language_code=result.get("language_out") or detected_lang or "kn-IN",
        )
    else:
        tts = {"audio_b64": "", "mime": "audio/wav", "latency_ms": 0}
    latency["tts_ms"] = round((time.perf_counter() - t2) * 1000, 1)
    latency["total_ms"] = round((time.perf_counter() - t_total) * 1000, 1)

    await brain.close()
    telephony_ready = bool(os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_AUTH_TOKEN"))
    return JSONResponse({
        "call_id": cid,
        "transcript": transcript,
        "detected_language": detected_lang,
        "action": action,
        "verify_phrase": spoken_text,
        "audio_b64": tts["audio_b64"],
        "audio_mime": tts["mime"],
        "issue_summary": result.get("issue_summary", ""),
        "urgency": result.get("urgency_1_to_5", 3),
        "dialect": result.get("dialect_out", "unknown"),
        "sentiment": result.get("sentiment", "neutral"),
        "fsm_state": result.get("fsm_state", "pending"),
        "handoff_reason": result.get("handoff_reason"),
        "telephony_status": "ready" if telephony_ready else "pending",
        "handoff_note": (
            "Emergency transfer is staged; Twilio call routing will be used once telephony is wired."
            if action == "handoff" and not telephony_ready else None
        ),
        "history": hist + [transcript],
        "latency_ms": latency,
    })


# ── /web/greeting — opening call greeting ───────────────────────────────
# Always in Kannada (1092 is a Karnataka helpline). The frontend auto-plays
# this, then switches language on the first citizen turn based on STT.
GREETING_TEXT = "ನಮಸ್ಕಾರ, ಇದು ಅರಿವು, 1092 ಸಹಾಯವಾಣಿ. ನಿಮ್ಮ ಸಮಸ್ಯೆಯನ್ನು ಹೇಳಿ."

@app.get("/web/greeting")
async def web_greeting() -> JSONResponse:
    """Return the Kannada opening greeting as TTS audio."""
    tts = await synthesize(GREETING_TEXT, target_language_code="kn-IN")
    return JSONResponse({
        "text": GREETING_TEXT,
        "language": "kn-IN",
        "audio_b64": tts["audio_b64"],
        "audio_mime": tts["mime"],
    })


# ── /web/turn/stream — SSE: events as they happen ──────────────────────
#
# Same inputs as /web/turn (multipart audio + form fields), but returns
# a Server-Sent Event stream so the browser can:
#   1. Show the recognised transcript the moment STT lands
#   2. Show the brain's interpretation the moment the brain returns
#   3. Start playing TTS audio chunks as they arrive (sub-1.5s first byte)
#
# Each SSE event is `data: <json>\n\n`. The `event:` line is set to one of:
#   transcript | brain_result | audio_chunk | done | error
#
# The frontend connects via fetch + ReadableStream (not EventSource —
# we need POST + multipart, EventSource is GET-only).
@app.post("/web/turn/stream")
async def web_turn_stream(
    audio: UploadFile = File(...),
    call_id: Optional[str] = Form(None),
    language: str = Form("unknown"),
    history: str = Form(""),
):
    cid = call_id or str(uuid.uuid4())
    hist = [h for h in history.split("\n") if h.strip()] if history else []
    audio_bytes = await audio.read()
    filename = audio.filename or "audio.webm"
    content_type = audio.content_type or "audio/webm"

    async def emit() -> "AsyncIterator[bytes]":  # type: ignore[name-defined]
        emitter = EventEmitter(call_id=cid, publisher=get_publisher())
        brain = BrainProxy()
        latency: dict[str, float] = {}
        t_total = time.perf_counter()

        try:
            if not hist:
                await emitter.call_started(language=language if language != "unknown" else "kn-IN")
            await emitter.turn_started(speaker="citizen")

            # ── 1. STT ─────────────────────────────────────────────────
            t0 = time.perf_counter()
            stt = await transcribe(
                audio_bytes,
                filename=filename,
                content_type=content_type,
                language_code=language,
            )
            latency["stt_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            transcript = stt.get("transcript", "") or ""
            detected_lang = stt.get("language_code") or (
                language if language != "unknown" else "kn-IN"
            )

            yield _sse("transcript", {
                "transcript": transcript,
                "detected_language": detected_lang,
                "elapsed_ms": latency["stt_ms"],
            })

            if not transcript:
                fallback = "ಕ್ಷಮಿಸಿ, ನಾನು ಸ್ಪಷ್ಟವಾಗಿ ಕೇಳಿಸಲಿಲ್ಲ. ಮತ್ತೊಮ್ಮೆ ಹೇಳಿ."
                yield _sse("brain_result", {
                    "action": "reprompt",
                    "verify_phrase": fallback,
                    "fsm_state": "pending",
                })
                async for chunk_b64, mime, ms in _stream_tts_b64(fallback, "kn-IN"):
                    yield _sse("audio_chunk", {"data": chunk_b64, "mime": mime, "elapsed_ms": ms})
                latency["total_ms"] = round((time.perf_counter() - t_total) * 1000, 1)
                yield _sse("done", {"latency_ms": latency, "history": hist})
                return

            await emitter.transcript_final(
                transcript=transcript, speaker="citizen", language=detected_lang,
            )

            # ── 2. Brain ──────────────────────────────────────────────
            t1 = time.perf_counter()
            result = await brain.process(
                call_id=cid,
                turn_index=len(hist) + 1,
                transcript=transcript,
                language=detected_lang,
                history=hist,
            )
            latency["brain_ms"] = round((time.perf_counter() - t1) * 1000, 1)

            yield _sse("brain_result", {
                "action": result.get("action"),
                "verify_phrase": result.get("text", ""),
                "issue_summary": result.get("issue_summary", ""),
                "urgency": result.get("urgency_1_to_5", 3),
                "dialect": result.get("dialect_out", "unknown"),
                "sentiment": result.get("sentiment", "neutral"),
                "fsm_state": result.get("fsm_state", "pending"),
                "handoff_reason": result.get("handoff_reason"),
                "elapsed_ms": latency["brain_ms"],
            })

            # Dashboard events (fire-and-forget — no await on critical path).
            if result.get("dialect_out") and result["dialect_out"] != "unknown":
                asyncio.create_task(emitter.dialect_detected(
                    dialect=result["dialect_out"],
                    confidence=result.get("confidence", 0.7),
                ))
            if result.get("sentiment"):
                asyncio.create_task(emitter.sentiment_updated(
                    sentiment=result["sentiment"],
                    confidence=result.get("confidence", 0.7),
                ))
            action = result.get("action", "verify")
            if action == "verify":
                asyncio.create_task(emitter.verification_pending(
                    verify_phrase=result.get("text", ""),
                    issue_summary=result.get("issue_summary", ""),
                    urgency=result.get("urgency_1_to_5", 3),
                ))
            elif action == "handoff":
                asyncio.create_task(emitter.verification_confirmed(
                    issue_summary=result.get("issue_summary", ""),
                    urgency=result.get("urgency_1_to_5", 3),
                    confidence=result.get("confidence", 0.7),
                ))
                asyncio.create_task(emitter.handoff_initiated(
                    reason=result.get("handoff_reason", "verified"),
                ))

            # ── 3. Stream TTS chunks ─────────────────────────────────
            spoken_text = result.get("text") or ""
            if spoken_text:
                t2 = time.perf_counter()
                first_byte_emitted = False
                async for chunk_b64, mime, ms in _stream_tts_b64(
                    spoken_text,
                    result.get("language_out") or detected_lang or "kn-IN",
                ):
                    if not first_byte_emitted:
                        latency["tts_first_byte_ms"] = round(
                            (time.perf_counter() - t2) * 1000, 1
                        )
                        first_byte_emitted = True
                    yield _sse("audio_chunk", {"data": chunk_b64, "mime": mime, "elapsed_ms": ms})

            latency["total_ms"] = round((time.perf_counter() - t_total) * 1000, 1)
            # Record to latency tracker for /health/latency
            latency_tracker.record(latency)
            yield _sse("done", {"latency_ms": latency, "history": hist + [transcript]})
        except Exception as e:
            logger.exception("web_turn_stream error: %s", e)
            yield _sse("error", {"message": str(e)})
        finally:
            try:
                await brain.close()
            except Exception:
                pass

    return StreamingResponse(
        emit(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx/proxy buffering
        },
    )


def _sse(event: str, data: dict) -> bytes:
    """Format one SSE event. Always includes a stable `event:` line so the
    frontend can dispatch via addEventListener instead of parsing payloads."""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


async def _stream_tts_b64(text: str, language: str):
    """
    Yield (b64_chunk, mime, elapsed_ms) tuples from the streaming TTS
    provider so the SSE handler can wrap them. We base64 here so the JSON
    envelope is JSON-safe; the browser decodes back to bytes for playback.
    """
    import base64 as _b64
    try:
        tts = get_tts_provider()
        buffer_pcm = b""
        last_mime = "audio/mpeg"
        last_ms = 0.0
        chunk_count = 0

        async for chunk in tts.synthesize_stream(text, target_language_code=language):
            buffer_pcm += chunk.pcm
            last_mime = chunk.mime
            last_ms = chunk.elapsed_ms
            chunk_count += 1
            
            # Group 5 chunks (approx 100-200ms) to improve browser decoding reliability
            if chunk_count >= 5:
                yield (
                    _b64.b64encode(buffer_pcm).decode("ascii"),
                    last_mime,
                    round(last_ms, 1),
                )
                buffer_pcm = b""
                chunk_count = 0
        
        # Final remaining buffer
        if buffer_pcm:
            yield (
                _b64.b64encode(buffer_pcm).decode("ascii"),
                last_mime,
                round(last_ms, 1),
            )
    except Exception as e:
        logger.warning("streaming TTS failed (%s) — falling back to one-shot", e)
        # Single-blob fallback so the browser always gets *something*.
        one_shot = await synthesize(text, target_language_code=language)
        if one_shot.get("audio_b64"):
            yield (one_shot["audio_b64"], one_shot.get("mime", "audio/wav"), 0.0)

