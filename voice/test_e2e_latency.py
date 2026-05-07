"""
Real end-to-end latency test — measures each stage independently.
Also tests the streaming pipeline separately.
"""
import asyncio
import base64
import time

from dotenv import load_dotenv
load_dotenv()


async def test_e2e():
    from voice.sarvam_speech import synthesize, transcribe
    from voice.brain.providers.factory import get_llm_provider

    brain = get_llm_provider()
    print(f"Brain provider:  {brain.name}")
    print(f"Has streaming:   {hasattr(brain, 'unified_turn_stream')}")
    print()

    # ── Create test audio ──
    citizen_text = "ನಮ್ಮ ಏರಿಯಾದಲ್ಲಿ ರಸ್ತೆ ತುಂಬಾ ಹಾಳಾಗಿದೆ, ಮಳೆ ಬಂದಾಗ ನೀರು ನಿಲ್ಲುತ್ತೆ"
    print(f'Creating test audio: "{citizen_text[:50]}..."')
    t0 = time.perf_counter()
    tts_result = await synthesize(citizen_text, target_language_code="kn-IN")
    audio_b64 = tts_result.get("audio_b64", "")
    if not audio_b64:
        print("ERROR: Could not synthesize test audio")
        return
    audio_bytes = base64.b64decode(audio_b64)
    print(f"  Audio ready: {len(audio_bytes)} bytes in {(time.perf_counter()-t0)*1000:.0f}ms")
    print()

    # ═══════════════════════════════════════════════════
    # TEST 1: BATCH PIPELINE (sequential)
    # ═══════════════════════════════════════════════════
    print("=" * 55)
    print("  TEST 1: BATCH PIPELINE (sequential)")
    print("=" * 55)
    t_e2e = time.perf_counter()

    # STT
    t1 = time.perf_counter()
    stt_result = await transcribe(audio_bytes, filename="test.wav",
                                   content_type="audio/wav", language_code="unknown")
    stt_ms = (time.perf_counter() - t1) * 1000
    transcript = stt_result.get("transcript", "")
    detected_lang = stt_result.get("language_code", "kn-IN")
    print(f'  STT:    {stt_ms:6.0f}ms | "{transcript[:55]}"')

    if not transcript:
        print("  ERROR: STT returned empty transcript")
        return

    # Brain (batch)
    t2 = time.perf_counter()
    turn = await brain.unified_turn(
        transcript=transcript, language=detected_lang,
        dialect_hint="", history=[],
    )
    brain_ms = (time.perf_counter() - t2) * 1000
    print(f"  Brain:  {brain_ms:6.0f}ms | u={turn.urgency} {turn.issue_summary[:45]}")
    print(f'          phrase="{turn.verify_phrase[:55]}..."')

    # TTS (streaming — measure first byte)
    from voice.speech.sarvam_streaming import SarvamTTS
    tts_prov = SarvamTTS()

    t3 = time.perf_counter()
    first_byte_ms = None
    chunk_count = 0
    total_audio_bytes = 0

    async for chunk in tts_prov.synthesize_stream(turn.verify_phrase,
                                                    target_language_code=detected_lang):
        chunk_count += 1
        total_audio_bytes += len(chunk.pcm)
        if first_byte_ms is None:
            first_byte_ms = (time.perf_counter() - t3) * 1000
    tts_total_ms = (time.perf_counter() - t3) * 1000

    print(f"  TTS 1st: {first_byte_ms:5.0f}ms")
    print(f"  TTS all: {tts_total_ms:5.0f}ms | {chunk_count} chunks")

    e2e_ms = (time.perf_counter() - t_e2e) * 1000
    perceived_ms = stt_ms + brain_ms + (first_byte_ms or 0)

    print()
    print(f"  SEQUENTIAL: {e2e_ms:.0f}ms total")
    print(f"  PERCEIVED:  {perceived_ms:.0f}ms (STT + Brain + TTS first byte)")

    # ═══════════════════════════════════════════════════
    # TEST 2: STREAMING BRAIN (verify_phrase → TTS overlap)
    # ═══════════════════════════════════════════════════
    if hasattr(brain, 'unified_turn_stream'):
        print()
        print("=" * 55)
        print("  TEST 2: STREAMING BRAIN (with TTS overlap)")
        print("=" * 55)
        import re

        t_stream = time.perf_counter()
        accumulated = ""
        verify_phrase = None
        first_token_ms = None
        verify_at_ms = None

        async for chunk_text in brain.unified_turn_stream(
            transcript=transcript, language=detected_lang,
            dialect_hint="", history=[],
        ):
            if first_token_ms is None:
                first_token_ms = (time.perf_counter() - t_stream) * 1000
            accumulated += chunk_text

            if verify_phrase is None:
                m = re.search(r'"verify_phrase"\s*:\s*"((?:[^"\\]|\\.)*)"', accumulated)
                if m:
                    verify_phrase = m.group(1).replace('\\"', '"')
                    verify_at_ms = (time.perf_counter() - t_stream) * 1000

        stream_total_ms = (time.perf_counter() - t_stream) * 1000

        print(f"  First token:      {first_token_ms:5.0f}ms")
        print(f"  verify_phrase at: {verify_at_ms:5.0f}ms  ← TTS starts HERE")
        print(f"  Full response:    {stream_total_ms:5.0f}ms")
        print(f'  verify_phrase: "{(verify_phrase or "")[:55]}..."')

        # In the real pipeline, TTS starts at verify_at_ms
        # So perceived = STT + verify_at_ms + TTS_first_byte
        overlap_perceived = stt_ms + (verify_at_ms or stream_total_ms) + (first_byte_ms or 0)
        print()
        print(f"  STREAMING PERCEIVED: {overlap_perceived:.0f}ms")
        print(f"    = STT({stt_ms:.0f}) + BrainStream({verify_at_ms:.0f}) + TTS({first_byte_ms:.0f})")

    # ═══════════════════════════════════════════════════
    # VERDICT
    # ═══════════════════════════════════════════════════
    print()
    print("=" * 55)
    print("  VERDICT")
    print("=" * 55)
    print()
    print(f"  Batch perceived:     {perceived_ms:.0f}ms")
    if hasattr(brain, 'unified_turn_stream'):
        print(f"  Streaming perceived: {overlap_perceived:.0f}ms")
        best = overlap_perceived
    else:
        best = perceived_ms

    print()
    if best < 1000:
        print(f"  ✅ SUB-1s ACHIEVED: {best:.0f}ms")
    elif best < 1500:
        print(f"  ⚠️  Under 1.5s: {best:.0f}ms")
    elif best < 2000:
        print(f"  ⚠️  Under 2s: {best:.0f}ms (add GEMINI_API_KEY to go faster)")
    else:
        print(f"  ❌ Over 2s: {best:.0f}ms")

    print()
    stages = {"STT": stt_ms, "Brain_batch": brain_ms, "TTS_first": first_byte_ms or 0}
    if hasattr(brain, 'unified_turn_stream'):
        stages["Brain_stream_vp"] = verify_at_ms or 0
    biggest = max(stages, key=stages.get)
    print(f"  BOTTLENECK: {biggest} = {stages[biggest]:.0f}ms")
    print()
    if not brain._using_api_key if hasattr(brain, '_using_api_key') else True:
        print("  💡 TIP: Add GEMINI_API_KEY to .env for ~300-500ms faster brain")


asyncio.run(test_e2e())
