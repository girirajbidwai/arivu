"use client";

import { useEffect, useState } from "react";

type CallState =
  | "idle"
  | "connecting"
  | "greeting"
  | "listening"
  | "processing"
  | "speaking"
  | "handoff"
  | "ended";

const CONFIG: Record<CallState, { label: string; color: string; pulse: boolean }> = {
  idle:       { label: "Ready",               color: "bg-warmsoft text-muted",        pulse: false },
  connecting: { label: "Connecting…",         color: "bg-gold/20 text-gold",          pulse: true  },
  greeting:   { label: "Arivu is speaking…",  color: "bg-gold/30 text-gold",          pulse: true  },
  listening:  { label: "Listening…",          color: "bg-sage/30 text-sage",          pulse: true  },
  processing: { label: "Processing…",        color: "bg-gold/20 text-gold",          pulse: true  },
  speaking:   { label: "Arivu is speaking…",  color: "bg-gold/30 text-gold",          pulse: true  },
  handoff:    { label: "Transferring…",       color: "bg-red-500/20 text-red-400",    pulse: true  },
  ended:      { label: "Call Ended",          color: "bg-warmsoft text-muted",        pulse: false },
};

export function CallStatusPill({ state }: { state: CallState }) {
  const cfg = CONFIG[state];
  return (
    <span
      className={`
        inline-flex items-center gap-2 rounded-full px-4 py-1.5
        font-mono text-[11px] tracking-widest uppercase
        transition-all duration-300 ${cfg.color}
      `}
    >
      {cfg.pulse && (
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-current opacity-60" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-current" />
        </span>
      )}
      {cfg.label}
    </span>
  );
}

export type { CallState };
