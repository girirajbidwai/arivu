// AudioBus — gapless streaming audio playback via MediaSource Extensions.
//
// Why MSE instead of decodeAudioData:
//   decodeAudioData treats each chunk as a complete file. MP3 encoding adds
//   ~20-50ms padding at the start/end of every frame. When you decode 10
//   chunks independently and schedule them back-to-back, you get 10 × 40ms
//   = 400ms of cumulative silence injected into playback. MSE handles the
//   container-level timestamp alignment that eliminates these gaps.
//
// Fallback: if MSE is unavailable (Safari iOS < 17), we fall back to
// decodeAudioData with the old scheduling approach.

let audioEl: HTMLAudioElement | null = null;
let mediaSource: MediaSource | null = null;
let sourceBuffer: SourceBuffer | null = null;
let mseReady = false;
let mseQueue: Uint8Array[] = [];
let mseAppending = false;

// Fallback path (decodeAudioData)
let ctx: AudioContext | null = null;
let nextStartAt = 0;
const activeSources: Set<AudioBufferSourceNode> = new Set();
let useMSE = false;

const SCHEDULE_AHEAD_S = 0.05;

function canUseMSE(): boolean {
  if (typeof window === "undefined") return false;
  if (!("MediaSource" in window)) return false;
  try {
    return MediaSource.isTypeSupported("audio/mpeg");
  } catch {
    return false;
  }
}

function getCtx(): AudioContext {
  if (ctx === null) {
    const Cls =
      (window as any).AudioContext || (window as any).webkitAudioContext;
    ctx = new Cls({ latencyHint: "interactive" });
  }
  return ctx!;
}

/**
 * Unlock the audio system. Must be called from inside a user-gesture
 * handler (click, tap).
 */
export async function unlock(): Promise<void> {
  useMSE = canUseMSE();

  if (useMSE) {
    // MSE path: create a persistent <audio> element + MediaSource
    _initMSE();
  } else {
    // Fallback: Web Audio API
    const c = getCtx();
    if (c.state === "suspended") await c.resume();
    const silent = c.createBuffer(1, 1, c.sampleRate);
    const src = c.createBufferSource();
    src.buffer = silent;
    src.connect(c.destination);
    src.start(0);
    nextStartAt = c.currentTime;
  }
}

function _initMSE(): void {
  // Clean up any previous session
  if (audioEl) {
    audioEl.pause();
    audioEl.removeAttribute("src");
    audioEl.load();
  }

  mediaSource = new MediaSource();
  audioEl = document.createElement("audio");
  audioEl.autoplay = true;
  // @ts-ignore
  audioEl.playsInline = true;
  audioEl.src = URL.createObjectURL(mediaSource);

  mseReady = false;
  mseQueue = [];
  mseAppending = false;
  sourceBuffer = null;

  mediaSource.addEventListener(
    "sourceopen",
    () => {
      if (!mediaSource || mediaSource.readyState !== "open") return;
      try {
        sourceBuffer = mediaSource.addSourceBuffer("audio/mpeg");
        sourceBuffer.mode = "sequence"; // gapless concatenation
        sourceBuffer.addEventListener("updateend", _drainMSEQueue);
        mseReady = true;
        _drainMSEQueue();
      } catch (e) {
        console.warn("[audioBus] MSE addSourceBuffer failed:", e);
        useMSE = false; // fall back
      }
    },
    { once: true }
  );
}

function _drainMSEQueue(): void {
  if (!sourceBuffer || sourceBuffer.updating || mseQueue.length === 0) return;
  mseAppending = true;
  const chunk = mseQueue.shift()!;
  try {
    sourceBuffer.appendBuffer(chunk);
  } catch (e) {
    console.warn("[audioBus] MSE appendBuffer failed:", e);
    // If quota exceeded, try removing old data
    if (e instanceof DOMException && e.name === "QuotaExceededError") {
      try {
        const buffered = sourceBuffer.buffered;
        if (buffered.length > 0) {
          sourceBuffer.remove(0, buffered.end(buffered.length - 1) - 5);
        }
      } catch {}
    }
    mseAppending = false;
  }
}

/**
 * Decode + enqueue a chunk for playback. The chunk should be MP3 bytes
 * (Sarvam Bulbul streams base64 MP3).
 */
export async function enqueueChunk(bytes: Uint8Array): Promise<void> {
  if (useMSE) {
    mseQueue.push(bytes);
    if (mseReady && sourceBuffer && !sourceBuffer.updating) {
      _drainMSEQueue();
    }
    // Auto-play if paused (user gesture already unlocked via unlock())
    if (audioEl && audioEl.paused) {
      audioEl.play().catch(() => {});
    }
    return;
  }

  // Fallback: Web Audio API decodeAudioData
  const c = getCtx();
  let audioBuffer: AudioBuffer;
  try {
    audioBuffer = await c.decodeAudioData(
      bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)
    );
  } catch (e) {
    console.warn("[audioBus] decode failed:", e);
    return;
  }
  const src = c.createBufferSource();
  src.buffer = audioBuffer;
  src.connect(c.destination);
  const startAt = Math.max(c.currentTime + SCHEDULE_AHEAD_S, nextStartAt);
  src.start(startAt);
  nextStartAt = startAt + audioBuffer.duration;

  // Track for barge-in cancellation
  activeSources.add(src);
  src.onended = () => {
    activeSources.delete(src);
    src.disconnect();
  };
}

/** Stop everything immediately and reset the queue (for "barge-in"). */
export async function reset(): Promise<void> {
  if (useMSE) {
    mseQueue = [];
    if (audioEl) {
      audioEl.pause();
      // Re-initialize MSE for clean state
      _initMSE();
    }
    return;
  }

  // Fallback: stop all active Web Audio sources
  for (const src of activeSources) {
    try {
      src.stop();
      src.disconnect();
    } catch {}
  }
  activeSources.clear();

  if (ctx) {
    nextStartAt = ctx.currentTime;
  }
}

/**
 * Returns true if audio is currently playing or buffered ahead.
 */
export function isPlaying(): boolean {
  if (useMSE) {
    if (!audioEl) return false;
    if (!audioEl.paused && audioEl.currentTime > 0) return true;
    if (mseQueue.length > 0) return true;
    if (sourceBuffer && sourceBuffer.buffered.length > 0) {
      const end = sourceBuffer.buffered.end(sourceBuffer.buffered.length - 1);
      return audioEl.currentTime < end - 0.05;
    }
    return false;
  }

  // Fallback
  if (!ctx) return false;
  return nextStartAt > ctx.currentTime + 0.005 || activeSources.size > 0;
}

/** Helper: decode base64 → Uint8Array. */
export function b64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}
