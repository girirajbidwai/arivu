"use client";
import type { Dialect } from "@/lib/types";

const labels: Record<Dialect, string> = {
  dharwad: "NORTH KARNATAKA",
  mangaluru: "COASTAL",
  mysuru: "OLD MYSURU",
  bengaluru: "BENGALURU URBAN",
  unknown: "DETECTING…",
};

const dot: Record<Dialect, string> = {
  dharwad: "bg-terracotta",
  mangaluru: "bg-sage",
  mysuru: "bg-gold",
  bengaluru: "bg-amber-400",
  unknown: "bg-muted",
};

export function DialectBadge({
  dialect, confidence,
}: { dialect: Dialect; confidence?: number }) {
  return (
    <div className="inline-flex items-center gap-2 rounded-full border border-warmsoft bg-warmsoft/60 px-3 py-1">
      <span className={`h-2 w-2 rounded-full ${dot[dialect]}`} />
      <span className="font-mono text-[11px] tracking-widest text-muted">
        {labels[dialect]}
      </span>
      {confidence !== undefined && (
        <span className="font-mono text-[10px] text-muted/70">
          {Math.round(confidence * 100)}%
        </span>
      )}
    </div>
  );
}
