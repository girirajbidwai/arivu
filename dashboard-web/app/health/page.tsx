"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { CallStatusPill } from "@/components/CallStatusPill";

const VOICE_URL =
  process.env.NEXT_PUBLIC_VOICE_SERVICE_URL ||
  process.env.VOICE_SERVICE_URL ||
  "http://localhost:8000";

type LatencyData = {
  turns: number;
  sub_1s_count: number;
  sub_1s_rate: number;
  uptime_s: number;
  stages: Record<
    string,
    { n: number; p50_ms: number; p95_ms: number; avg_ms: number }
  >;
  providers: Record<string, string>;
  last_providers?: Record<string, string>;
};

export default function HealthPage() {
  const [data, setData] = useState<LatencyData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchLatency() {
      try {
        const res = await fetch(`${VOICE_URL}/health/latency`);
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        const json = await res.json();
        setData(json);
        setError(null);
      } catch (e: any) {
        setError(e.message);
      }
    }
    fetchLatency();
    const t = setInterval(fetchLatency, 2000);
    return () => clearInterval(t);
  }, []);

  return (
    <main className="min-h-screen bg-warmdark text-[#EFE9DB] p-6">
      <header className="mb-8 border-b border-warmsoft pb-6 flex items-center justify-between">
        <div>
          <h1 className="font-display text-3xl text-gold">System Health</h1>
          <p className="font-mono text-xs text-muted mt-2 tracking-widest">
            LIVE LATENCY TELEMETRY
          </p>
        </div>
        <div className="flex gap-4">
          <Link
            href="/talk"
            className="rounded-md border border-gold/40 px-4 py-2 font-mono text-[11px] tracking-widest text-gold hover:bg-gold/10 transition"
          >
            TALK DEMO
          </Link>
        </div>
      </header>

      {error ? (
        <div className="rounded-xl border border-red-500/40 bg-red-500/10 p-4 text-red-400 font-mono text-sm">
          Failed to connect to Voice Service: {error}
        </div>
      ) : !data ? (
        <div className="text-muted font-mono text-sm animate-pulse">Loading telemetry...</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Hero Metric */}
          <div className="md:col-span-3 rounded-2xl border border-gold/30 bg-warmsoft/20 p-8 flex flex-col items-center justify-center relative overflow-hidden">
            <div className="absolute inset-0 bg-gradient-to-br from-gold/5 to-transparent pointer-events-none" />
            <h2 className="font-mono text-sm tracking-widest text-muted uppercase mb-4 z-10">
              Sub-1s Success Rate
            </h2>
            <div className="text-6xl md:text-8xl font-display text-gold z-10 tracking-tight">
              {(data.sub_1s_rate * 100).toFixed(0)}<span className="text-4xl text-gold/60">%</span>
            </div>
            <p className="text-muted font-mono text-xs mt-4 z-10">
              {data.sub_1s_count} of {data.turns} turns completed in &lt; 1000ms
            </p>
          </div>

          {/* Providers */}
          <div className="rounded-2xl border border-warmsoft bg-warmsoft/20 p-6">
            <h3 className="font-mono text-xs tracking-widest text-muted uppercase mb-6">
              Active Providers
            </h3>
            <div className="space-y-4">
              <ProviderRow label="Brain LLM" value={data.last_providers?.brain || data.providers?.brain} />
              <ProviderRow label="STT" value={data.last_providers?.stt || data.providers?.stt} />
              <ProviderRow label="TTS" value={data.last_providers?.tts || data.providers?.tts} />
            </div>
          </div>

          {/* Pipeline Latency Breakdown */}
          <div className="md:col-span-2 rounded-2xl border border-warmsoft bg-warmsoft/20 p-6">
            <h3 className="font-mono text-xs tracking-widest text-muted uppercase mb-6">
              Pipeline Stage Latency (p50 / p95)
            </h3>
            <div className="space-y-6">
              <StageBar
                label="STT (partial → final)"
                stage={data.stages["stt_ms"]}
                target={200}
                max={1500}
              />
              <StageBar
                label="Brain (intent + verify phrase)"
                stage={data.stages["brain_ms"]}
                target={250}
                max={1500}
              />
              <StageBar
                label="TTS (first byte)"
                stage={data.stages["tts_first_byte_ms"] || data.stages["tts_ms"]}
                target={250}
                max={1500}
              />
              <StageBar
                label="End-to-End Total"
                stage={data.stages["total_ms"]}
                target={1000}
                max={3000}
                isTotal
              />
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

function ProviderRow({ label, value }: { label: string; value?: string }) {
  const isHeuristic = value === "heuristic";
  return (
    <div className="flex items-center justify-between">
      <span className="font-mono text-xs text-muted uppercase">{label}</span>
      <span
        className={`font-mono text-xs px-2 py-1 rounded-md border ${
          isHeuristic
            ? "border-red-500/40 text-red-400 bg-red-500/10"
            : "border-sage/40 text-sage bg-sage/10"
        }`}
      >
        {value || "unknown"}
      </span>
    </div>
  );
}

function StageBar({
  label,
  stage,
  target,
  max,
  isTotal = false,
}: {
  label: string;
  stage?: { p50_ms: number; p95_ms: number };
  target: number;
  max: number;
  isTotal?: boolean;
}) {
  if (!stage) {
    return (
      <div>
        <div className="flex justify-between font-mono text-[11px] text-muted uppercase mb-2">
          <span>{label}</span>
          <span>--</span>
        </div>
        <div className="h-2 w-full bg-warmdark rounded-full overflow-hidden" />
      </div>
    );
  }

  const p50Width = Math.min((stage.p50_ms / max) * 100, 100);
  const p95Width = Math.min((stage.p95_ms / max) * 100, 100);

  const colorClass =
    stage.p50_ms <= target
      ? "bg-sage"
      : stage.p50_ms <= target * 1.5
      ? "bg-gold"
      : "bg-terracotta";

  return (
    <div>
      <div className="flex justify-between font-mono text-[11px] uppercase mb-2">
        <span className="text-muted">{label}</span>
        <span className={isTotal && stage.p50_ms <= 1000 ? "text-sage font-bold" : "text-[#EFE9DB]"}>
          {stage.p50_ms} <span className="text-muted lowercase">ms</span>{" "}
          <span className="text-muted/50 mx-1">/</span>{" "}
          <span className="text-muted">{stage.p95_ms}</span>
        </span>
      </div>
      <div className="h-2 w-full bg-warmdark rounded-full overflow-hidden relative">
        <div
          className="absolute top-0 bottom-0 left-0 bg-warmsoft/30 transition-all duration-500"
          style={{ width: `${p95Width}%` }}
        />
        <div
          className={`absolute top-0 bottom-0 left-0 ${colorClass} transition-all duration-500`}
          style={{ width: `${p50Width}%` }}
        />
        {/* Target Marker */}
        <div
          className="absolute top-0 bottom-0 w-px bg-white/30 z-10"
          style={{ left: `${(target / max) * 100}%` }}
        />
      </div>
    </div>
  );
}
