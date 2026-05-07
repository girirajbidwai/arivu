"""
Twilio Media Streams handler — full bidirectional voice pipeline.

When a citizen calls the Twilio number:
  1. Twilio POSTs to /twiml → we return <Connect><Stream/> TwiML
  2. Twilio opens WebSocket to /ws → this handler runs
  3. Inbound: Twilio sends μ-law 8kHz audio packets
  4. We accumulate audio, detect end-of-speech (silence), then:
     STT (Sarvam) → Brain (Gemini) → TTS (Sarvam) → back to Twilio
  5. Outbound: We send μ-law 8kHz audio back through the Media Stream

Audio format: Twilio sends/receives base64-encoded μ-law 8kHz mono.
"""

from __future__ import annotations

import asyncio
import audioop
import base64
import json
import logging
import os
import re
import time
import uuid
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("arivu.twilio_handler")

# VAD thresholds for phone audio (μ-law 8kHz)
PHONE_SILENCE_THRESHOLD = 500     # RMS threshold for silence
PHONE_SILENCE_DURATION_MS = 600   # ms of silence before end-of-speech
PHONE_MIN_SPEECH_MS = 400         # minimum speech duration to process
PHONE_SAMPLE_RATE = 8000          # Twilio Media Streams = 8kHz μ-law

# Greeting in Kannada
GREETING_TEXT = "ನಮಸ್ಕಾರ, ಇದು ಅರಿವು, 1092 ಸಹಾಯವಾಣಿ. ನಿಮ್ಮ ಸಮಸ್ಯೆಯನ್ನು ಹೇಳಿ."

REPROMPT = {
    "kn-IN": "ಕ್ಷಮಿಸಿ, ನಾನು ಸ್ಪಷ್ಟವಾಗಿ ಕೇಳಿಸಲಿಲ್ಲ. ಮತ್ತೊಮ್ಮೆ ಹೇಳಿ.",
    "hi-IN": "माफ़ कीजिए, मैं ठीक से सुन नहीं पाया। कृपया फिर से बताइए।",
    "en-IN": "Sorry, I didn't catch that clearly. Could you please repeat?",
}


async def handle_twilio_ws(ws: WebSocket) -> None:
    """
    Main Twilio Media Streams WebSocket handler.
    Runs the full STT → Brain → TTS pipeline over phone audio.
    """
    await ws.accept()
    call_id = str(uuid.uuid4())
    stream_sid: Optional[str] = None
    call_sid: Optional[str] = None
    language = "kn-IN"  # 1092 is Karnataka helpline — default Kannada
    history: list[str] = []
    is_speaking_tts = False
    mark_counter = 0

    # Audio buffer for VAD
    audio_buffer = bytearray()
    speech_started = False
    speech_start_time = 0.0
    last_speech_time = 0.0

    # Get brain provider
    from voice.brain.providers.factory import get_llm_provider
    brain_provider = None
    try:
        brain_provider = get_llm_provider()
        logger.info("📞 %s brain: %s", call_id, brain_provider.name)
    except Exception as e:
        logger.warning("📞 %s brain init failed: %s", call_id, e)

    # Event emitter for dashboard
    from voice.events.publisher import get_publisher
    from voice.processors.event_emitter import EventEmitter
    emitter = EventEmitter(call_id=call_id, publisher=get_publisher())

    logger.info("📞 %s new Twilio call connected", call_id)
    await emitter.call_started(language=language)

    try:
        async for raw in ws.iter_text():
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            event = msg.get("event", "")

            # ── Connected: Twilio confirms WebSocket is up ──
            if event == "connected":
                logger.info("📞 %s twilio connected (protocol=%s)",
                            call_id, msg.get("protocol", "?"))

            # ── Start: call metadata arrives ──
            elif event == "start":
                start = msg.get("start", {})
                stream_sid = start.get("streamSid", "")
                call_sid = start.get("callSid", "")
                logger.info("📞 %s stream=%s call=%s from=%s",
                            call_id, stream_sid, call_sid,
                            start.get("customParameters", {}).get("From", "?"))

                # Send greeting immediately
                asyncio.create_task(
                    _speak_to_caller(ws, stream_sid, GREETING_TEXT, language,
                                     call_id, mark_counter)
                )
                is_speaking_tts = True

            # ── Media: raw audio from the caller ──
            elif event == "media":
                media = msg.get("media", {})
                payload = media.get("payload", "")
                if not payload:
                    continue

                # Decode base64 μ-law
                mulaw_bytes = base64.b64decode(payload)
                
                # Convert μ-law to linear PCM (16-bit) to calculate accurate RMS
                try:
                    pcm_bytes = audioop.ulaw2lin(mulaw_bytes, 2)
                except Exception:
                    pcm_bytes = b""

                # Barge-in detection: if TTS is playing and caller speaks
                if is_speaking_tts:
                    try:
                        rms = audioop.rms(pcm_bytes, 2)
                    except Exception:
                        rms = 0
                    if rms > PHONE_SILENCE_THRESHOLD * 2:
                        # Caller is interrupting — stop TTS
                        logger.info("📞 %s barge-in detected (rms=%d)", call_id, rms)
                        await _clear_twilio_audio(ws, stream_sid)
                        is_speaking_tts = False
                        audio_buffer.clear()
                        speech_started = False
                        continue

                # Accumulate audio + VAD
                audio_buffer.extend(mulaw_bytes)
                now = time.monotonic()

                try:
                    rms = audioop.rms(pcm_bytes, 2)
                except Exception:
                    rms = 0

                if rms > PHONE_SILENCE_THRESHOLD:
                    if not speech_started:
                        speech_started = True
                        speech_start_time = now
                    last_speech_time = now
                elif speech_started:
                    silence_ms = (now - last_speech_time) * 1000
                    speech_ms = (now - speech_start_time) * 1000

                    if silence_ms > PHONE_SILENCE_DURATION_MS and speech_ms > PHONE_MIN_SPEECH_MS:
                        # End of speech detected — process the turn
                        logger.info(
                            "📞 %s end-of-speech: %.0fms speech, %.0fms silence, %d bytes",
                            call_id, speech_ms, silence_ms, len(audio_buffer)
                        )
                        speech_started = False

                        # Convert μ-law → WAV for STT
                        wav_bytes = _mulaw_to_wav(bytes(audio_buffer))
                        audio_buffer.clear()

                        # Process the turn (STT → Brain → TTS → back to caller)
                        mark_counter += 1
                        is_speaking_tts = True
                        asyncio.create_task(
                            _process_phone_turn(
                                ws=ws,
                                stream_sid=stream_sid,
                                wav_bytes=wav_bytes,
                                language=language,
                                history=history,
                                call_id=call_id,
                                brain_provider=brain_provider,
                                emitter=emitter,
                                mark_id=mark_counter,
                                set_language=lambda l: None,  # updated below
                            )
                        )

            # ── Mark: Twilio confirms our audio finished playing ──
            elif event == "mark":
                mark_name = msg.get("mark", {}).get("name", "")
                logger.info("📞 %s mark received: %s", call_id, mark_name)
                is_speaking_tts = False

            # ── Stop: call ended ──
            elif event == "stop":
                logger.info("📞 %s stream stopped", call_id)
                break

    except WebSocketDisconnect:
        logger.info("📞 %s disconnected", call_id)
    except Exception as e:
        logger.exception("📞 %s ws error: %s", call_id, e)
    finally:
        await emitter.call_ended()
        logger.info("📞 %s call ended (turns=%d)", call_id, len(history))


async def _process_phone_turn(
    *,
    ws: WebSocket,
    stream_sid: Optional[str],
    wav_bytes: bytes,
    language: str,
    history: list[str],
    call_id: str,
    brain_provider,
    emitter,
    mark_id: int,
    set_language,
) -> None:
    """Process one turn: STT → Brain → TTS → send audio back to Twilio."""
    latency: dict[str, float] = {}
    t_start = time.perf_counter()

    # ── 1. STT ──
    t0 = time.perf_counter()
    from voice.sarvam_speech import transcribe
    stt = await transcribe(
        wav_bytes,
        filename="call_audio.wav",
        content_type="audio/wav",
        language_code=language if language != "unknown" else "unknown",
    )
    latency["stt_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    transcript = stt.get("transcript", "") or ""
    detected_lang = stt.get("language_code") or language
    logger.info("📞 %s STT (%.0fms): '%s'", call_id, latency["stt_ms"], transcript[:60])

    await emitter.transcript_final(
        transcript=transcript, speaker="citizen", language=detected_lang,
    )

    if not transcript:
        # Empty STT — reprompt
        fallback = REPROMPT.get(detected_lang, REPROMPT["en-IN"])
        await _speak_to_caller(ws, stream_sid, fallback, detected_lang, call_id, mark_id)
        return

    history.append(transcript)

    # ── 2. Brain (streaming Gemini) ──
    t1 = time.perf_counter()
    verify_phrase = ""

    if brain_provider and hasattr(brain_provider, 'unified_turn_stream'):
        accumulated = ""
        try:
            async for chunk_text in brain_provider.unified_turn_stream(
                transcript=transcript,
                language=detected_lang,
                dialect_hint="",
                history=history,
            ):
                accumulated += chunk_text
                if not verify_phrase:
                    m = re.search(r'"verify_phrase"\s*:\s*"((?:[^"\\]|\\.)*)"', accumulated)
                    if m:
                        verify_phrase = m.group(1).replace('\\"', '"').replace('\\n', '\n')

            latency["brain_ms"] = round((time.perf_counter() - t1) * 1000, 1)

            # Parse full response for metadata
            result = _safe_parse(accumulated)
            if result and not verify_phrase:
                verify_phrase = result.get("verify_phrase", "")

            # Emit dashboard events
            if result:
                urgency = result.get("urgency", 3)
                if result.get("dialect") and result["dialect"] != "unknown":
                    asyncio.create_task(emitter.dialect_detected(
                        dialect=result["dialect"],
                        confidence=result.get("confidence", 0.7),
                    ))
                if result.get("sentiment"):
                    asyncio.create_task(emitter.sentiment_updated(
                        sentiment=result["sentiment"],
                        confidence=result.get("confidence", 0.7),
                    ))

        except Exception as e:
            logger.error("📞 %s brain streaming failed: %s", call_id, e)
            latency["brain_ms"] = round((time.perf_counter() - t1) * 1000, 1)

    elif brain_provider:
        # Batch fallback
        try:
            turn = await brain_provider.unified_turn(
                transcript=transcript,
                language=detected_lang,
                dialect_hint="",
                history=history,
            )
            verify_phrase = turn.verify_phrase
            latency["brain_ms"] = round((time.perf_counter() - t1) * 1000, 1)
        except Exception as e:
            logger.error("📞 %s brain batch failed: %s", call_id, e)
            latency["brain_ms"] = round((time.perf_counter() - t1) * 1000, 1)

    logger.info("📞 %s Brain (%.0fms): '%s'", call_id, latency.get("brain_ms", 0), verify_phrase[:60])

    if not verify_phrase:
        verify_phrase = REPROMPT.get(detected_lang, REPROMPT["en-IN"])

    # ── 3. TTS → Twilio ──
    t2 = time.perf_counter()
    await _speak_to_caller(ws, stream_sid, verify_phrase, detected_lang, call_id, mark_id)
    latency["tts_ms"] = round((time.perf_counter() - t2) * 1000, 1)

    latency["total_ms"] = round((time.perf_counter() - t_start) * 1000, 1)
    logger.info("📞 %s turn complete: %s", call_id, latency)


async def _speak_to_caller(
    ws: WebSocket,
    stream_sid: Optional[str],
    text: str,
    language: str,
    call_id: str,
    mark_id: int,
) -> None:
    """Convert text to speech and send audio back through Twilio Media Stream."""
    if not stream_sid:
        logger.warning("📞 %s no stream_sid — can't send audio", call_id)
        return

    try:
        from voice.sarvam_speech import synthesize
        tts = await synthesize(text, target_language_code=language)
        audio_b64 = tts.get("audio_b64", "")
        if not audio_b64:
            logger.warning("📞 %s TTS returned no audio", call_id)
            return

        # Sarvam returns WAV (PCM 16-bit). Twilio needs μ-law 8kHz base64.
        wav_bytes = base64.b64decode(audio_b64)
        mulaw_b64_chunks = _wav_to_mulaw_chunks(wav_bytes)

        for chunk_b64 in mulaw_b64_chunks:
            await ws.send_text(json.dumps({
                "event": "media",
                "streamSid": stream_sid,
                "media": {
                    "payload": chunk_b64,
                },
            }))

        # Send a mark so Twilio tells us when audio finishes
        await ws.send_text(json.dumps({
            "event": "mark",
            "streamSid": stream_sid,
            "mark": {"name": f"tts_done_{mark_id}"},
        }))

        logger.info("📞 %s sent TTS audio (%d chunks)", call_id, len(mulaw_b64_chunks))

    except Exception as e:
        logger.error("📞 %s TTS/send failed: %s", call_id, e)


async def _clear_twilio_audio(ws: WebSocket, stream_sid: Optional[str]) -> None:
    """Send clear event to stop Twilio from playing queued audio (barge-in)."""
    if not stream_sid:
        return
    try:
        await ws.send_text(json.dumps({
            "event": "clear",
            "streamSid": stream_sid,
        }))
    except Exception as e:
        logger.warning("clear audio failed: %s", e)


def _mulaw_to_wav(mulaw_bytes: bytes) -> bytes:
    """Convert μ-law 8kHz mono → WAV (PCM 16-bit 16kHz) for STT."""
    import struct
    import io

    # μ-law → PCM 16-bit
    pcm_8k = audioop.ulaw2lin(mulaw_bytes, 2)
    # Upsample 8kHz → 16kHz (Sarvam STT expects 16kHz)
    pcm_16k, _ = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)

    # Build WAV header
    buf = io.BytesIO()
    data_size = len(pcm_16k)
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<I", 16))        # chunk size
    buf.write(struct.pack("<H", 1))         # PCM format
    buf.write(struct.pack("<H", 1))         # mono
    buf.write(struct.pack("<I", 16000))     # sample rate
    buf.write(struct.pack("<I", 32000))     # byte rate
    buf.write(struct.pack("<H", 2))         # block align
    buf.write(struct.pack("<H", 16))        # bits per sample
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(pcm_16k)
    return buf.getvalue()


def _wav_to_mulaw_chunks(wav_bytes: bytes, chunk_ms: int = 20) -> list[str]:
    """
    Convert WAV audio → list of base64 μ-law 8kHz chunks for Twilio.
    Twilio expects chunks of ~20ms (160 samples at 8kHz).
    """
    import struct

    # Skip WAV header (find 'data' chunk)
    pcm_data = wav_bytes
    data_offset = wav_bytes.find(b"data")
    if data_offset >= 0:
        size_offset = data_offset + 4
        data_size = struct.unpack_from("<I", wav_bytes, size_offset)[0]
        pcm_data = wav_bytes[size_offset + 4: size_offset + 4 + data_size]

    # Read WAV parameters from header
    if wav_bytes[:4] == b"RIFF":
        sample_rate = struct.unpack_from("<I", wav_bytes, 24)[0]
        bits_per_sample = struct.unpack_from("<H", wav_bytes, 34)[0]
        channels = struct.unpack_from("<H", wav_bytes, 22)[0]
    else:
        # Assume 16kHz 16-bit mono
        sample_rate = 16000
        bits_per_sample = 16
        channels = 1

    sample_width = bits_per_sample // 8

    # If stereo, convert to mono
    if channels == 2:
        pcm_data = audioop.tomono(pcm_data, sample_width, 0.5, 0.5)

    # Resample to 8kHz if needed
    if sample_rate != 8000:
        pcm_data, _ = audioop.ratecv(pcm_data, sample_width, 1, sample_rate, 8000, None)

    # PCM → μ-law
    if sample_width != 2:
        pcm_data = audioop.lin2lin(pcm_data, sample_width, 2)
    mulaw_data = audioop.lin2ulaw(pcm_data, 2)

    # Split into ~20ms chunks (160 bytes at 8kHz μ-law)
    chunk_size = (8000 * chunk_ms) // 1000  # 160 bytes per 20ms
    chunks = []
    for i in range(0, len(mulaw_data), chunk_size):
        chunk = mulaw_data[i:i + chunk_size]
        if len(chunk) > 0:
            chunks.append(base64.b64encode(chunk).decode("ascii"))

    return chunks


def _safe_parse(text: str) -> Optional[dict]:
    """Parse JSON from Gemini output."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        text = re.sub(r",\s*}", "}", text)
        if not text.endswith("}"):
            text += "}"
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
