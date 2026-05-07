"use client";
import { useEffect, useState } from "react";
import type { ArivuEvent, SafetyTrigger } from "@/lib/types";

const labels: Record<SafetyTrigger, string> = {
  whisper_detected: "WHISPER DETECTED",
  silent_after_distress: "SILENCE AFTER DISTRESS",
  third_voice: "THIRD VOICE ON LINE",
};

export function HandoffAlert({ events, onTakeOver }: {
  events: ArivuEvent[];
  onTakeOver?: () => void;
}) {
  const safety = [...events].reverse().find((e) => e.event_type === "safety_alert");
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => { setDismissed(false); }, [safety?.timestamp]);

  if (!safety || dismissed) return null;
  const trigger = safety.data?.safety_trigger;

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-red-500/30 backdrop-blur-sm animate-pulse">
      <div className="max-w-md rounded-2xl border border-red-500 bg-warmdark p-8 text-center">
        <div className="font-mono text-[10px] tracking-widest text-red-400">SAFETY OVERRIDE</div>
        <div className="mt-3 font-mono text-3xl tracking-widest text-red-400">
          {trigger ? labels[trigger] : "HANDOFF"}
        </div>
        <p className="mt-4 text-sm text-muted">
          Arivu is fading to silent interpreter mode. Pick up the call now.
        </p>
        <button
          onClick={() => { setDismissed(true); onTakeOver?.(); }}
          className="mt-6 rounded-lg bg-gold px-6 py-3 font-mono text-sm tracking-widest text-warmdark hover:opacity-90"
        >
          TAKE OVER
        </button>
      </div>
    </div>
  );
}
