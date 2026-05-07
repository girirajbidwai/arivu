"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { CallWaveform } from "@/components/CallWaveform";
import { CallStatusPill, type CallState } from "@/components/CallStatusPill";
import * as audioBus from "@/lib/audioBus";
import { postSSE } from "@/lib/sseStream";

const VOICE_URL =
  process.env.NEXT_PUBLIC_VOICE_SERVICE_URL ||
  process.env.VOICE_SERVICE_URL ||
  "http://localhost:8000";

// Derive WS URL from HTTP URL
const WS_URL = VOICE_URL.replace(/^http/, "ws");

const SILENCE_THRESHOLD = 55;   // raised above cooler background noise (~45-50)
const SILENCE_DURATION = 800;  
const SPEECH_THRESHOLD = 65;    // speech peaks at ~90-120
const MIN_SPEECH_MS = 300;

type Turn = { speaker: "citizen" | "arivu"; text: string; detail?: string };

type BrainResult = {
  action: string;
  text: string;
  issue_summary: string;
  urgency: number;
  dialect: string;
  sentiment: string;
  fsm_state: string;
  handoff_reason?: string | null;
  latency_ms: Record<string, number>;
};

export default function TalkPage() {
  const [callState, setCallState] = useState<CallState>("idle");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [brainResult, setBrainResult] = useState<BrainResult | null>(null);
  const [muted, setMuted] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [language, setLanguage] = useState("auto");
  const [streamMode, setStreamMode] = useState(true); // WS streaming vs batch

  const callIdRef = useRef(crypto.randomUUID());
  const historyRef = useRef<string[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const [analyserNode, setAnalyserNode] = useState<AnalyserNode | null>(null);
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wasSpeakingRef = useRef(false);
  const speechStartRef = useRef<number>(0);
  const vadRafRef = useRef<number>(0);
  const pcmChunksRef = useRef<Float32Array[]>([]);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const callActiveRef = useRef(false);
  const mutedRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const audioQueueRef = useRef<string[]>([]);
  const playingRef = useRef(false);
  const playerRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    playerRef.current = new Audio();
  }, []);

  // Auto-scroll transcript
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [turns]);

  // Call timer
  useEffect(() => {
    if (callState === "idle" || callState === "ended") {
      if (timerRef.current) clearInterval(timerRef.current);
      return;
    }
    if (!timerRef.current && callState === "connecting") {
      setElapsed(0);
      timerRef.current = setInterval(() => setElapsed((e) => e + 1), 1000);
    }
  }, [callState]);

  const formatTime = (s: number) => {
    const m = Math.floor(s / 60);
    return `${m}:${String(s % 60).padStart(2, "0")}`;
  };

  // ── WebSocket message handler ──────────────────────────────────────
  function handleWSMessage(data: any) {
    switch (data.type) {
      case "status":
        if (data.state === "listening") {
          setCallState("listening");
          // Re-start recording + VAD for next turn
          if (callActiveRef.current && !recorderRef.current?.state?.includes("recording")) {
            startListening();
          }
        }
        else if (data.state === "processing") setCallState("processing");
        else if (data.state === "speaking") setCallState("speaking");
        break;

      case "transcript":
        if (data.text) {
          setTurns((prev) => [...prev, { speaker: "citizen", text: data.text }]);
        }
        break;

      case "brain_result":
        setBrainResult(data as BrainResult);
        if (data.text) {
          setTurns((prev) => [
            ...prev,
            {
              speaker: "arivu",
              text: data.text,
              detail: data.action === "handoff" ? "handoff" : data.fsm_state,
            },
          ]);
        }
        if (data.action === "handoff") {
          setCallState("handoff");
        }
        break;

      case "audio_chunk":
        audioQueueRef.current.push(data.data);
        processAudioQueue();
        break;

      case "audio_end":
        // Fall back to listening mode after audio queue drains (approximate since we don't await play)
        // For WS mode, the server sends {"type": "status", "state": "listening"} explicitly after audio,
        // so we don't strictly need to do it here, but it's safe.
        break;

      case "error":
        setError(data.message);
        break;
    }
  }

  // ── Audio chunk queue player ────────────────────────────────────────
  async function processAudioQueue() {
    if (playingRef.current || audioQueueRef.current.length === 0) return;
    playingRef.current = true;

    while (audioQueueRef.current.length > 0) {
      const b64 = audioQueueRef.current.shift()!;
      try {
        const bytes = audioBus.b64ToBytes(b64);
        await audioBus.enqueueChunk(bytes);
      } catch (e) {
        console.warn("[arivu] audio chunk enqueue failed:", e);
      }
    }
    playingRef.current = false;
  }

  // ── Start Call ─────────────────────────────────────────────────────
  const startCall = useCallback(async () => {
    setError(null);
    setTurns([]);
    setBrainResult(null);
    callIdRef.current = crypto.randomUUID();
    historyRef.current = [];
    callActiveRef.current = true;
    audioQueueRef.current = [];
    playingRef.current = false;
    setCallState("connecting");

    // Unlock both audio paths from inside this user gesture:
    //  1. The legacy <audio> element (still used for the greeting fetch).
    //  2. Our persistent AudioContext (used for streaming TTS chunks).
    if (playerRef.current) {
      playerRef.current.src = "data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA";
      playerRef.current.play().catch(() => {});
    }
    try {
      await audioBus.unlock();
    } catch (e) {
      console.warn("[arivu] audioBus.unlock failed:", e);
    }

    try {
      // Get mic with optimized audio constraints
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          sampleRate: 16000,
          channelCount: 1,
        },
      });
      streamRef.current = stream;

      // Audio context at 16kHz
      const ctx = new AudioContext({ sampleRate: 16000 });
      const src = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.8;
      src.connect(analyser);
      audioCtxRef.current = ctx;
      analyserRef.current = analyser;
      setAnalyserNode(analyser);

      // Create ScriptProcessor for WAV capture
      const processor = ctx.createScriptProcessor(4096, 1, 1);
      processor.onaudioprocess = (e) => {
        if (callActiveRef.current && !mutedRef.current) {
          const inputData = e.inputBuffer.getChannelData(0);
          pcmChunksRef.current.push(new Float32Array(inputData));
        }
      };
      src.connect(processor);
      processor.connect(ctx.destination);
      processorRef.current = processor;

      // Try WebSocket streaming first
      if (streamMode) {
        try {
          const ws = new WebSocket(`${WS_URL}/web/stream`);
          wsRef.current = ws;

          ws.onopen = () => {
            ws.send(JSON.stringify({
              type: "config",
              call_id: callIdRef.current,
              language: language === "auto" ? "unknown" : language,
              history: [],
            }));
          };

          ws.onmessage = (e) => {
            try {
              const data = JSON.parse(e.data);
              handleWSMessage(data);
            } catch { }
          };

          ws.onerror = () => {
            console.warn("WS failed, falling back to batch mode");
            setStreamMode(false);
            wsRef.current = null;
          };

          ws.onclose = () => {
            wsRef.current = null;
          };
        } catch {
          setStreamMode(false);
        }
      }

      // Play greeting — always proceed to listening even if greeting fails
      setCallState("greeting");
      const greetingText = "ನಮಸ್ಕಾರ, ಇದು ಅರಿವು, 1092 ಸಹಾಯವಾಣಿ. ನಿಮ್ಮ ಸಮಸ್ಯೆಯನ್ನು ಹೇಳಿ.";
      setTurns([{ speaker: "arivu", text: greetingText, detail: "greeting" }]);

      try {
        console.log("[arivu] fetching greeting from", `${VOICE_URL}/web/greeting`);
        const greetRes = await fetch(`${VOICE_URL}/web/greeting`);
        if (greetRes.ok) {
          const greet = await greetRes.json();
          console.log("[arivu] greeting response:", greet.text ? "has text" : "no text", greet.audio_b64 ? `audio ${greet.audio_b64.length} chars` : "no audio");
          if (greet.text) {
            setTurns([{ speaker: "arivu", text: greet.text, detail: "greeting" }]);
          }
          if (greet.audio_b64) {
            await playAudio(greet.audio_b64, greet.audio_mime || "audio/wav");
          }
        } else {
          console.warn("[arivu] greeting fetch failed:", greetRes.status);
        }
      } catch (e) {
        console.warn("[arivu] greeting error:", e);
      }

      // Always start listening after greeting (even if greeting audio failed)
      if (callActiveRef.current) {
        startListening();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Microphone access denied.");
      setCallState("idle");
    }
  }, [language, streamMode]);

  // ── End Call ────────────────────────────────────────────────────────
  const endCall = useCallback(() => {
    callActiveRef.current = false;
    stopVAD();
    stopRecording();
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "end_call" }));
      wsRef.current.close();
      wsRef.current = null;
    }
    stopTracks();
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    setCallState("ended");
  }, []);

  // ── Start Listening ────────────────────────────────────────────────
  function startListening() {
    if (!streamRef.current || !callActiveRef.current) return;
    setCallState("listening");
    pcmChunksRef.current = [];
    wasSpeakingRef.current = false;
    startVAD();
  }

  function stopRecording() {
    // We synthesize the WAV blob here instead of in MediaRecorder.onstop
    if (!callActiveRef.current) return;
    const pcmChunks = pcmChunksRef.current;
    if (pcmChunks.length > 0) {
      const blob = encodeWAV(pcmChunks, 16000);
      console.log(`[arivu] recording stopped. blob size: ${blob.size} bytes`);
      if (blob.size > 500) {
        sendTurn(blob).catch(console.error);
      } else if (callActiveRef.current) {
        startListening();
      }
    } else if (callActiveRef.current) {
      startListening();
    }
    stopVAD();
  }

  // ── VAD ────────────────────────────────────────────────────────────
  function startVAD() {
    const analyser = analyserRef.current;
    if (!analyser) return;
    const data = new Uint8Array(analyser.frequencyBinCount);

    function check() {
      if (!callActiveRef.current) return;
      vadRafRef.current = requestAnimationFrame(check);
      analyser!.getByteFrequencyData(data);

      let sum = 0;
      for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
      const rms = Math.sqrt(sum / data.length);

      if (mutedRef.current) return;

      // Debug logging 10% of the time so we can see the actual RMS values in the console
      if (Math.random() < 0.1 && rms > 5) {
        console.log(`[arivu VAD] rms: ${rms.toFixed(1)} | speaking: ${wasSpeakingRef.current}`);
      }

      if (rms > SPEECH_THRESHOLD) {
        if (!wasSpeakingRef.current) {
          wasSpeakingRef.current = true;
          speechStartRef.current = Date.now();
          
          // Barge-in: only stop TTS if speech persists for a bit (>100ms)
          // to avoid accidental interruptions from background pops/clicks.
          setTimeout(() => {
            if (wasSpeakingRef.current && (audioBus.isPlaying() || callState === "speaking")) {
               console.log("[arivu] barge-in detected, resetting audio");
               audioBus.reset();
               audioQueueRef.current = [];
               playingRef.current = false;
               if (wsRef.current?.readyState === WebSocket.OPEN) {
                 wsRef.current.send(JSON.stringify({ type: "end_turn", audio: "" }));
               }
            }
          }, 100);
        }
        if (silenceTimerRef.current) {
          clearTimeout(silenceTimerRef.current);
          silenceTimerRef.current = null;
        }
      } else if (wasSpeakingRef.current && rms < SILENCE_THRESHOLD) {
        const speechDuration = Date.now() - speechStartRef.current;
        if (speechDuration < MIN_SPEECH_MS) {
          wasSpeakingRef.current = false;
          return;
        }
        if (!silenceTimerRef.current) {
          silenceTimerRef.current = setTimeout(() => {
            wasSpeakingRef.current = false;
            silenceTimerRef.current = null;
            stopRecording();
          }, SILENCE_DURATION);
        }
      }
    }
    check();
  }

  function stopVAD() {
    cancelAnimationFrame(vadRafRef.current);
    if (silenceTimerRef.current) { clearTimeout(silenceTimerRef.current); silenceTimerRef.current = null; }
  }

  // ── Send Turn ──────────────────────────────────────────────────────
  async function sendTurn(blob: Blob) {
    if (!callActiveRef.current) return;
    setCallState("processing");
    console.log("[arivu] sendTurn: blob size", blob.size, "type", blob.type);

    // Preferred path: SSE streaming. Brain + transcript come back fast,
    // TTS audio chunks stream in and play through the AudioContext queue
    // — citizen hears the first syllable in <2s without GCP, <1s with.
    try {
      const body = new FormData();
      body.append("audio", blob, `turn.wav`);
      body.append("call_id", callIdRef.current);
      body.append("language", language === "auto" ? "unknown" : language);
      body.append("history", historyRef.current.join("\n"));

      let firstAudioAt: number | null = null;
      const startAt = performance.now();
      let chunksReceived = 0;

      await postSSE(`${VOICE_URL}/web/turn/stream`, body, {
        transcript: (d) => {
          if (d.transcript) {
            setTurns((prev) => [...prev, { speaker: "citizen", text: d.transcript }]);
            historyRef.current = [...historyRef.current, d.transcript];
          }
        },
        brain_result: (d) => {
          setBrainResult({
            action: d.action,
            text: d.verify_phrase,
            issue_summary: d.issue_summary,
            urgency: d.urgency,
            dialect: d.dialect,
            sentiment: d.sentiment,
            fsm_state: d.fsm_state,
            handoff_reason: d.handoff_reason,
            latency_ms: { brain_ms: d.elapsed_ms ?? 0 },
          });
          if (d.verify_phrase) {
            setTurns((prev) => [
              ...prev,
              {
                speaker: "arivu",
                text: d.verify_phrase,
                detail: d.action === "handoff" ? "handoff" : d.fsm_state,
              },
            ]);
          }
          if (d.action === "verify" || d.action === "reprompt") {
            setCallState("speaking");
          }
        },
        audio_chunk: async (d) => {
          if (!callActiveRef.current) return;
          if (firstAudioAt === null) {
            firstAudioAt = performance.now() - startAt;
            console.log(`[arivu] first audio arrived at +${firstAudioAt.toFixed(0)}ms`);
          }
          chunksReceived++;
          const bytes = audioBus.b64ToBytes(d.data);
          await audioBus.enqueueChunk(bytes);
        },
        done: (d) => {
          console.log(
            `[arivu] turn done — chunks=${chunksReceived} firstAudio=${firstAudioAt?.toFixed(0)}ms latency=`,
            d.latency_ms,
          );
          // brainResult may not yet have full latency_ms — merge.
          setBrainResult((prev) =>
            prev ? { ...prev, latency_ms: { ...prev.latency_ms, ...d.latency_ms } } : prev,
          );
        },
        error: (d) => {
          console.error("[arivu] stream error:", d);
          setError(d.message || "Streaming turn failed.");
        },
      });

      // Wait for the audio queue to finish playing before reopening the mic
      // so the citizen doesn't talk over Arivu's verify phrase.
      while (audioBus.isPlaying() && callActiveRef.current) {
        await new Promise((r) => setTimeout(r, 50));
      }

      if (brainResult?.action === "handoff") {
        setCallState("handoff");
        return;
      }
      if (callActiveRef.current) startListening();
      return;
    } catch (err) {
      console.warn("[arivu] SSE path failed, falling back to batch:", err);
    }

    // Fallback: batch HTTP POST (single blob, slower but always works).
    try {
      const body = new FormData();
      body.append("audio", blob, `turn.wav`);
      body.append("call_id", callIdRef.current);
      body.append("language", language === "auto" ? "unknown" : language);
      body.append("history", historyRef.current.join("\n"));

      const res = await fetch(`${VOICE_URL}/web/turn`, { method: "POST", body });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);

      const data = await res.json();
      callIdRef.current = data.call_id;

      setBrainResult({
        action: data.action,
        text: data.verify_phrase,
        issue_summary: data.issue_summary,
        urgency: data.urgency,
        dialect: data.dialect,
        sentiment: data.sentiment,
        fsm_state: data.fsm_state,
        handoff_reason: data.handoff_reason,
        latency_ms: data.latency_ms,
      });

      setTurns((prev) => {
        const next = [...prev];
        if (data.transcript) next.push({ speaker: "citizen", text: data.transcript });
        if (data.verify_phrase) {
          next.push({
            speaker: "arivu",
            text: data.verify_phrase,
            detail: data.action === "handoff" ? "handoff" : data.fsm_state,
          });
        }
        return next;
      });

      if (data.transcript) historyRef.current = [...historyRef.current, data.transcript];

      if (data.audio_b64 && callActiveRef.current) {
        setCallState("speaking");
        await playAudio(data.audio_b64, data.audio_mime || "audio/wav");
      }

      if (data.action === "handoff") {
        setCallState("handoff");
        return;
      }

      if (callActiveRef.current) startListening();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Voice turn failed.");
      if (callActiveRef.current) startListening();
    }
  }

  // ── Audio Playback ─────────────────────────────────────────────────
  function playAudio(b64: string, mime: string): Promise<void> {
    return new Promise((resolve) => {
      const audio = playerRef.current;
      if (!audio) {
        console.error("[arivu] Audio player element not found");
        return resolve();
      }

      console.log(`[arivu] playing audio chunk: ${b64.length} bytes, type: ${mime}`);
      audio.src = `data:${mime};base64,${b64}`;
      
      audio.onended = () => {
        console.log("[arivu] playback finished");
        resolve();
      };
      
      audio.onerror = (e) => {
        console.error("[arivu] audio playback error:", e);
        resolve();
      };

      audio.play().catch((e) => {
        console.warn("[arivu] autoplay blocked or playback failed:", e);
        resolve();
      });
    });
  }

  // ── Mute Toggle ────────────────────────────────────────────────────
  function toggleMute() {
    const newMuted = !muted;
    setMuted(newMuted);
    mutedRef.current = newMuted;
    streamRef.current?.getAudioTracks().forEach((t) => (t.enabled = !newMuted));
  }

  // ── Cleanup ────────────────────────────────────────────────────────
  function stopTracks() {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (processorRef.current) {
      processorRef.current.disconnect();
      processorRef.current = null;
    }
    audioCtxRef.current?.close();
    audioCtxRef.current = null;
    analyserRef.current = null;
    setAnalyserNode(null);
  }

  useEffect(() => () => { callActiveRef.current = false; stopTracks(); }, []);

  // ── Render ─────────────────────────────────────────────────────────
  const isActive = !["idle", "ended"].includes(callState);

  return (
    <main className="min-h-screen bg-warmdark text-[#EFE9DB]">
      <header className="border-b border-warmsoft px-6 py-4 flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="font-display text-2xl text-gold">Arivu Voice</h1>
          <p className="font-mono text-[10px] tracking-widest text-muted">
            {isActive ? `CALL IN PROGRESS · ${formatTime(elapsed)}` : "BROWSER CALL SIMULATOR"}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <CallStatusPill state={callState} />
          {streamMode && isActive && (
            <span className="rounded-full bg-sage/20 px-2 py-0.5 font-mono text-[9px] tracking-widest text-sage">
              WS STREAM
            </span>
          )}
          <Link
            href="/queue"
            className="rounded-md border border-gold/40 px-3 py-1 font-mono text-[11px] tracking-widest text-gold hover:bg-gold/10"
          >
            QUEUE
          </Link>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-4 p-6 lg:grid-cols-12">
        {/* Left: Call Panel */}
        <section className="lg:col-span-7 flex flex-col">
          <div className="flex items-center justify-between gap-3 flex-wrap mb-4">
            <div className="flex items-center gap-2">
              <label className="font-mono text-[10px] tracking-widest text-muted">LANG</label>
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                disabled={isActive}
                className="rounded-md border border-warmsoft bg-warmdark px-3 py-1.5 font-mono text-[11px] text-gold disabled:opacity-40"
              >
                <option value="auto">Auto-detect</option>
                <option value="en-IN">English</option>
                <option value="kn-IN">Kannada</option>
              </select>
            </div>

            <div className="flex items-center gap-2">
              {isActive && (
                <button
                  onClick={toggleMute}
                  className={`rounded-full px-4 py-2.5 font-mono text-[11px] tracking-widest transition ${
                    muted
                      ? "bg-red-500/20 text-red-400 border border-red-500/40"
                      : "bg-warmsoft text-muted border border-warmsoft hover:border-gold hover:text-gold"
                  }`}
                >
                  {muted ? "UNMUTE" : "MUTE"}
                </button>
              )}
              <button
                onClick={isActive ? endCall : startCall}
                className={`rounded-full px-6 py-2.5 font-mono text-[11px] tracking-widest transition ${
                  isActive
                    ? "bg-red-500 text-white hover:bg-red-600"
                    : "bg-sage text-warmdark hover:opacity-90"
                }`}
              >
                {isActive ? "END CALL" : callState === "ended" ? "NEW CALL" : "START CALL"}
              </button>
            </div>
          </div>

          {/* Waveform */}
          <div className="flex items-center justify-center rounded-xl border border-warmsoft bg-warmsoft/20 p-3 mb-4">
            <CallWaveform
              analyser={analyserNode}
              active={callState === "listening" && !muted}
            />
          </div>

          {/* Transcript */}
          <div
            ref={scrollRef}
            className="flex-1 rounded-2xl border border-warmsoft bg-warmsoft/30 p-5 overflow-y-auto space-y-3"
            style={{ minHeight: "45vh", maxHeight: "55vh" }}
          >
            {turns.length === 0 && (
              <div className="text-sm text-muted leading-relaxed text-center py-8">
                {callState === "idle"
                  ? "Press START CALL to begin. Arivu will greet you in Kannada and auto-detect your language."
                  : "Connecting…"}
              </div>
            )}
            {turns.map((turn, i) => (
              <div
                key={`${turn.speaker}-${i}`}
                className={`rounded-xl border p-4 transition-all duration-300 ${
                  turn.speaker === "citizen"
                    ? "border-terracotta/40 bg-terracotta/10 ml-8"
                    : "border-gold/30 bg-gold/10 mr-8"
                }`}
              >
                <div className="font-mono text-[10px] tracking-widest text-muted uppercase">
                  {turn.speaker === "citizen" ? "You" : "Arivu"}
                  {turn.detail ? ` · ${turn.detail}` : ""}
                </div>
                <p className="mt-1.5 text-sm leading-snug font-kn">{turn.text}</p>
              </div>
            ))}
          </div>

          {error && (
            <div className="mt-3 rounded-md border border-red-400/40 bg-red-500/10 p-3 font-mono text-[11px] text-red-300">
              {error}
            </div>
          )}
        </section>

        {/* Right: Brain Output */}
        <aside className="lg:col-span-5 space-y-4">
          <div className="rounded-2xl border border-gold/30 bg-warmsoft/40 p-5">
            <div className="font-mono text-[11px] tracking-widest text-gold">BRAIN OUTPUT</div>
            {brainResult ? (
              <div className="mt-4 space-y-3 text-sm">
                <InfoRow label="Action" value={brainResult.action} />
                <InfoRow label="FSM" value={brainResult.fsm_state} />
                <InfoRow label="Issue" value={brainResult.issue_summary || "—"} />
                <InfoRow label="Urgency" value={`U${brainResult.urgency}`} />
                <InfoRow label="Dialect" value={brainResult.dialect} />
                <InfoRow label="Sentiment" value={brainResult.sentiment} />
              </div>
            ) : (
              <p className="mt-4 text-sm text-muted">Waiting for first turn…</p>
            )}
          </div>

          <div className="rounded-2xl border border-warmsoft bg-warmsoft/30 p-5">
            <div className="font-mono text-[11px] tracking-widest text-muted">LATENCY</div>
            {brainResult?.latency_ms ? (
              <div className="mt-4 space-y-2 font-mono text-xs text-[#EFE9DB]">
                {Object.entries(brainResult.latency_ms).map(([k, v]) => (
                  <div key={k} className="flex items-center justify-between">
                    <span className="uppercase tracking-widest text-muted">{k}</span>
                    <span className={v > 500 ? "text-red-400" : v > 200 ? "text-gold" : "text-sage"}>
                      {Math.round(v)} ms
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="mt-4 text-sm text-muted">After the first turn.</p>
            )}
          </div>

          <div className="rounded-2xl border border-warmsoft bg-warmsoft/30 p-5 text-sm text-muted leading-relaxed">
            Open the <Link href="/queue" className="text-gold underline">queue</Link> in another tab to watch the agent dashboard update live.
          </div>
        </aside>
        {/* Hidden persistent audio element for "Interview Grade" playback reliability */}
        <audio ref={playerRef} autoPlay playsInline className="hidden" />
      </div>
    </main>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <span className="font-mono text-[10px] tracking-widest text-muted uppercase">{label}</span>
      <span className="text-right">{value}</span>
    </div>
  );
}

// ── WAV Encoder helper ────────────────────────────────────────────────
function encodeWAV(chunks: Float32Array[], sampleRate: number): Blob {
  let length = 0;
  for (const chunk of chunks) length += chunk.length;
  const buffer = new ArrayBuffer(44 + length * 2);
  const view = new DataView(buffer);

  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + length * 2, true);
  writeString(view, 8, 'WAVE');
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(view, 36, 'data');
  view.setUint32(40, length * 2, true);

  let offset = 44;
  for (const chunk of chunks) {
    for (let i = 0; i < chunk.length; i++, offset += 2) {
      let s = Math.max(-1, Math.min(1, chunk[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    }
  }

  return new Blob([view], { type: 'audio/wav' });
}

function writeString(view: DataView, offset: number, string: string) {
  for (let i = 0; i < string.length; i++) {
    view.setUint8(offset + i, string.charCodeAt(i));
  }
}
