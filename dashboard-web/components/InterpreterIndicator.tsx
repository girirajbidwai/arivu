"use client";

import type { ArivuEvent } from "@/lib/types";

interface Props {
  events: ArivuEvent[];
  agentLang?: string;
  citizenLang?: string;
}

export function InterpreterIndicator({
  events, agentLang = "Hindi", citizenLang = "Kannada",
}: Props) {
  const active = events.some((e) => e.event_type === "interpreter_mode_active");
  if (!active) return null;

  return (
    <div className="flex items-center gap-3 rounded-md border border-gold/40 bg-gold/10 px-3 py-2">
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-gold opacity-75" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-gold" />
      </span>
      <span className="font-mono text-[11px] tracking-widest text-gold">
        INTERPRETER · {agentLang} ↔ {citizenLang} · LIVE
      </span>
    </div>
  );
}
