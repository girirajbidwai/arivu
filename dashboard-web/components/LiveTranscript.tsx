"use client";
import { useEffect, useRef } from "react";
import type { ArivuEvent, Speaker } from "@/lib/types";

const speakerStyle: Record<Speaker, string> = {
  citizen: "border-l-terracotta",
  agent:   "border-l-sage",
  arivu:   "border-l-gold",
  system:  "border-l-muted",
};

const speakerLabel: Record<Speaker, string> = {
  citizen: "Citizen",
  agent:   "Agent",
  arivu:   "Arivu",
  system:  "System",
};

export function LiveTranscript({ events }: { events: ArivuEvent[] }) {
  const ref = useRef<HTMLDivElement>(null);

  // Keep transcript pinned to latest turn.
  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight, behavior: "smooth" });
  }, [events.length]);

  const turns = events.filter(
    (e) => e.event_type === "transcript_partial" || e.event_type === "transcript_final",
  );

  return (
    <div
      ref={ref}
      className="h-full overflow-y-auto rounded-lg bg-warmsoft/40 p-4 space-y-3"
    >
      {turns.length === 0 && (
        <p className="font-mono text-xs text-muted">
          Waiting for the citizen to speak…
        </p>
      )}
      {turns.map((e, i) => {
        const speaker = (e.data?.speaker || "citizen") as Speaker;
        const partial = e.event_type === "transcript_partial";
        return (
          <div
            key={`${e.timestamp}-${i}`}
            className={`border-l-2 ${speakerStyle[speaker]} pl-3 ${
              partial ? "opacity-60 italic" : ""
            }`}
          >
            <div className="flex items-baseline gap-2">
              <span className="font-mono text-[10px] uppercase tracking-widest text-muted">
                {speakerLabel[speaker]} · {new Date(e.timestamp).toLocaleTimeString()}
              </span>
            </div>
            <p className="font-kn text-base leading-snug">
              {e.data?.transcript}
            </p>
          </div>
        );
      })}
    </div>
  );
}
