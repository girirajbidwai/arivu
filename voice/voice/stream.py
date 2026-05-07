"""
Streaming voice pipeline — WebSocket-based, concurrent STT→Brain→TTS.

Browser → WS /web/stream → STT (streaming) → Brain → TTS (streaming) → Browser

Architecture for sub-1s latency:
  1. Audio chunks from browser → streaming STT (partials flow back instantly)
  2. On EOU (end-of-utterance) → Brain processes final transcript
  3. Brain returns verify_phrase → sentence-split → stream TTS immediately
  4. TTS chunks flow back to browser → plays via AudioContext on first byte

Barge-in: if citizen speaks while TTS is playing, TTS is cancelled and
the pipeline switches back to listening mode.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("arivu.voice.stream")

# Session language memory (shared across turns within a call)
_session_languages: dict[str, str] = {}

# Sentence-splitting punctuation for Indic + English
_SENTENCE_ENDS = frozenset(".?!।|")


def _get_sarvam_key() -> str:
    return os.getenv("SARVAM_API_KEY", "")


async def handle_stream(ws: WebSocket, brain_url: str) -> None:
    """
    Main streaming handler. Protocol:

    Browser → Server messages (JSON):
      {type: "config", call_id, language, history: []}
      {type: "audio", data: "<base64 PCM 16kHz int16>"}
      {type: "end_turn"}  — explicit turn-end signal
      {type: "end_call"}

    Server → Browser messages (JSON):
      {type: "status", state: "listening"|"processing"|"speaking"}
      {type: "partial_transcript", text: "..."}
      {type: "transcript", text: "...", language: "..."}
      {type: "brain_result", action, text, issue_summary, urgency, ...}
      {type: "audio_chunk", data: "<base64 audio>", seq: N}
      {type: "audio_end"}
      {type: "latency", ...}
      {type: "error", message: "..."}
    """
    await ws.accept()
    logger.info("stream ws connected")

    call_id = ""
    language = "unknown"
    history: list[str] = []
    sarvam_key = _get_sarvam_key()

    if not sarvam_key:
        await ws.send_json({"type": "error", "message": "SARVAM_API_KEY not set"})
        await ws.close()
        return

    # Track TTS playback for barge-in
    tts_playing = False
    tts_cancel_event = asyncio.Event()

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type", "")

            if msg_type == "config":
                call_id = msg.get("call_id", call_id)
                language = msg.get("language", "unknown")
                history = msg.get("history", [])
                await ws.send_json({"type": "status", "state": "listening"})
                logger.info("stream configured: call_id=%s lang=%s", call_id, language)

            elif msg_type == "end_turn":
                # Barge-in: cancel any in-progress TTS
                if tts_playing:
                    tts_cancel_event.set()
                    tts_playing = False
                    logger.info("barge-in: citizen spoke while TTS playing")

                audio_b64 = msg.get("audio", "")
                if not audio_b64:
                    await ws.send_json({"type": "status", "state": "listening"})
                    continue

                audio_bytes = base64.b64decode(audio_b64)
                if len(audio_bytes) < 2000:  # Too short
                    await ws.send_json({"type": "status", "state": "listening"})
                    continue

                await ws.send_json({"type": "status", "state": "processing"})
                t_start = time.perf_counter()
                latency: dict[str, float] = {}

                # ── Concurrent STT ─────────────────────────────────────
                t0 = time.perf_counter()
                transcript = ""
                detected_lang = language if language != "unknown" else "kn-IN"

                transcript, detected_lang = await _run_stt(
                    audio_bytes, language, sarvam_key
                )
                latency["stt_ms"] = round((time.perf_counter() - t0) * 1000, 1)

                # Update session language
                if detected_lang and detected_lang != "unknown":
                    _session_languages[call_id] = detected_lang
                elif call_id in _session_languages:
                    detected_lang = _session_languages[call_id]

                await ws.send_json({
                    "type": "transcript",
                    "text": transcript,
                    "language": detected_lang,
                    "elapsed_ms": latency["stt_ms"],
                })

                if not transcript:
                    from voice.main import REPROMPT
                    session_lang = _session_languages.get(call_id, "kn-IN")
                    fallback = REPROMPT.get(session_lang, REPROMPT["en-IN"])
                    await ws.send_json({
                        "type": "brain_result",
                        "action": "reprompt",
                        "text": fallback,
                        "issue_summary": "",
                        "urgency": 3,
                        "dialect": "unknown",
                        "sentiment": "neutral",
                        "fsm_state": "pending",
                        "latency_ms": latency,
                    })
                    tts_cancel_event.clear()
                    tts_playing = True
                    await _stream_tts(ws, fallback, session_lang, sarvam_key,
                                       latency, tts_cancel_event)
                    tts_playing = False
                    await ws.send_json({"type": "status", "state": "listening"})
                    continue

                # ── Brain (concurrent with TTS prep) ───────────────────
                t1 = time.perf_counter()
                import httpx
                async with httpx.AsyncClient(timeout=15.0) as http:
                    brain_resp = await http.post(
                        f"{brain_url}/process",
                        json={
                            "call_id": call_id,
                            "turn_index": len(history),
                            "transcript": transcript,
                            "language": detected_lang,
                            "history": history,
                        },
                    )
                    brain_resp.raise_for_status()
                    result = brain_resp.json()

                latency["brain_ms"] = round((time.perf_counter() - t1) * 1000, 1)

                spoken_text = result.get("text", "")
                action = result.get("action", "verify")

                await ws.send_json({
                    "type": "brain_result",
                    "action": action,
                    "text": spoken_text,
                    "issue_summary": result.get("issue_summary", ""),
                    "urgency": result.get("urgency_1_to_5", 3),
                    "dialect": result.get("dialect_out", "unknown"),
                    "sentiment": result.get("sentiment", "neutral"),
                    "fsm_state": result.get("fsm_state", "pending"),
                    "handoff_reason": result.get("handoff_reason"),
                    "latency_ms": latency,
                })

                history.append(transcript)

                # ── TTS (streaming, with barge-in support) ─────────────
                if spoken_text:
                    tts_lang = result.get("language_out") or detected_lang or "kn-IN"
                    tts_cancel_event.clear()
                    tts_playing = True
                    await _stream_tts(ws, spoken_text, tts_lang, sarvam_key,
                                       latency, tts_cancel_event)
                    tts_playing = False

                latency["total_ms"] = round((time.perf_counter() - t_start) * 1000, 1)

                # Emit latency summary for telemetry
                try:
                    from voice.telemetry import tracker
                    tracker.record(latency)
                except Exception:
                    pass

                await ws.send_json({
                    "type": "latency",
                    **latency,
                })
                await ws.send_json({"type": "status", "state": "listening"})

            elif msg_type == "end_call":
                logger.info("stream call ended: %s", call_id)
                await ws.close()
                return

    except WebSocketDisconnect:
        logger.info("stream ws disconnected: %s", call_id)
    except Exception as e:
        logger.error("stream error: %s", e, exc_info=True)
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


async def _run_stt(
    audio_bytes: bytes,
    language: str,
    sarvam_key: str,
) -> tuple[str, str]:
    """
    Run STT with streaming-first approach, batch fallback.
    Returns (transcript, detected_language).
    """
    detected_lang = language if language != "unknown" else "kn-IN"
    transcript = ""

    try:
        from sarvamai import AsyncSarvamAI
        client = AsyncSarvamAI(api_subscription_key=sarvam_key)
        stt_lang = language if language not in ("unknown", "auto") else "unknown"

        async with client.speech_to_text_streaming.connect(
            model="saaras:v3",
            mode="transcribe",
            language_code=stt_lang,
        ) as stt_ws:
            # Send audio in chunks for streaming processing
            chunk_size = 16000  # 0.5s of 16kHz 16-bit audio
            for i in range(0, len(audio_bytes), chunk_size):
                chunk = audio_bytes[i:i + chunk_size]
                await stt_ws.transcribe(audio=chunk)

            # Get final result
            response = await stt_ws.recv()
            if hasattr(response, 'transcript'):
                transcript = response.transcript or ""
                if hasattr(response, 'language_code') and response.language_code:
                    detected_lang = response.language_code
            elif isinstance(response, dict):
                transcript = response.get("transcript", "")
                detected_lang = response.get("language_code", detected_lang)

    except Exception as e:
        logger.error("streaming STT failed: %s, falling back to batch", e)
        transcript, detected_lang = await _batch_stt_fallback(
            audio_bytes, language, detected_lang
        )

    return transcript, detected_lang


async def _batch_stt_fallback(
    audio_bytes: bytes,
    language: str,
    detected_lang: str,
) -> tuple[str, str]:
    """Convert audio and run batch STT as a fallback."""
    import subprocess
    import tempfile

    wav_bytes = audio_bytes  # fallback

    try:
        f_in = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
        f_out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        f_in.write(audio_bytes)
        f_in.close()
        f_out.close()

        res = subprocess.run(
            ["ffmpeg", "-y", "-i", f_in.name,
             "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
             f_out.name],
            check=True, capture_output=True
        )
        with open(f_out.name, "rb") as f:
            wav_bytes = f.read()

        import os
        os.unlink(f_in.name)
        os.unlink(f_out.name)
    except subprocess.CalledProcessError as conv_e:
        logger.error("ffmpeg conversion failed: %s", conv_e)
    except Exception as conv_e:
        logger.error("ffmpeg error: %s", conv_e)

    # Batch STT
    from voice.sarvam_speech import transcribe
    stt_result = await transcribe(
        wav_bytes,
        filename="audio.wav",
        content_type="audio/wav",
        language_code=language if language != "unknown" else "unknown",
    )
    transcript = stt_result.get("transcript", "")
    detected_lang = stt_result.get("language_code", detected_lang)

    return transcript, detected_lang


async def _stream_tts(
    ws: WebSocket,
    text: str,
    language: str,
    sarvam_key: str,
    latency: dict,
    cancel_event: asyncio.Event,
) -> None:
    """
    Stream TTS audio chunks back to the browser.

    Supports barge-in: if cancel_event is set, stop sending immediately.
    First tries streaming Sarvam Bulbul, falls back to batch.
    """
    t_tts = time.perf_counter()
    await ws.send_json({"type": "status", "state": "speaking"})

    try:
        from sarvamai import AsyncSarvamAI
        client = AsyncSarvamAI(api_subscription_key=sarvam_key)
        seq = 0

        async with client.text_to_speech_streaming.connect(model="bulbul:v3") as tts_ws:
            await tts_ws.configure(
                target_language_code=language,
                speaker=os.getenv("SARVAM_TTS_SPEAKER", "pooja"),
            )
            await tts_ws.send_text(text)

            async for audio_chunk in tts_ws:
                # Barge-in check
                if cancel_event.is_set():
                    logger.info("TTS cancelled (barge-in) at chunk %d", seq)
                    break

                if audio_chunk:
                    if isinstance(audio_chunk, bytes):
                        chunk_b64 = base64.b64encode(audio_chunk).decode()
                    elif hasattr(audio_chunk, 'audio'):
                        chunk_b64 = (
                            base64.b64encode(audio_chunk.audio).decode()
                            if isinstance(audio_chunk.audio, bytes)
                            else audio_chunk.audio
                        )
                    elif hasattr(audio_chunk, 'data') and hasattr(audio_chunk.data, 'audio'):
                        raw = audio_chunk.data.audio
                        chunk_b64 = (
                            base64.b64encode(raw).decode()
                            if isinstance(raw, bytes)
                            else raw
                        )
                    else:
                        continue

                    if seq == 0:
                        latency["tts_first_byte_ms"] = round(
                            (time.perf_counter() - t_tts) * 1000, 1
                        )
                        logger.info("TTS first byte in %.0fms", latency["tts_first_byte_ms"])

                    await ws.send_json({
                        "type": "audio_chunk",
                        "data": chunk_b64,
                        "seq": seq,
                    })
                    seq += 1

        latency["tts_ms"] = round((time.perf_counter() - t_tts) * 1000, 1)
        logger.info("tts streaming done: %d chunks in %.0fms", seq, latency["tts_ms"])

    except Exception as e:
        logger.error("streaming TTS failed: %s, falling back to batch", e)
        # Fallback to batch TTS
        from voice.sarvam_speech import synthesize
        tts = await synthesize(text, target_language_code=language)
        latency["tts_ms"] = round((time.perf_counter() - t_tts) * 1000, 1)
        if tts.get("audio_b64"):
            await ws.send_json({
                "type": "audio_chunk",
                "data": tts["audio_b64"],
                "seq": 0,
            })

    await ws.send_json({"type": "audio_end"})
