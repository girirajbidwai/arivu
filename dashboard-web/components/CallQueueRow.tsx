"use client";

import Link from "next/link";
import type { CallSummary } from "@/lib/api";
import { DialectBadge } from "./DialectBadge";

const urgencyTint: Record<number, string> = {
  5: "border-l-red-500 bg-red-500/10",
  4: "border-l-terracotta bg-terracotta/10",
  3: "border-l-amber-400 bg-amber-400/5",
  2: "border-l-sage bg-sage/5",
  1: "border-l-muted bg-warmsoft/30",
};

export function CallQueueRow({ call }: { call: CallSummary }) {
  const urgency = call.urgency ?? 3;
  return (
    <Link
      href={`/call/${call.id}`}
      className={`block border-l-4 ${urgencyTint[urgency] ?? urgencyTint[3]} rounded-r-lg p-4 transition hover:bg-warmsoft/60`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] tracking-widest text-muted">
              U{urgency}
            </span>
            <span className="font-display text-lg leading-tight truncate">
              {call.issue_summary || "(awaiting verification)"}
            </span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <DialectBadge
              dialect={(call.dialect as any) ?? "unknown"}
            />
            {call.sentiment && (
              <span className="font-mono text-[10px] uppercase tracking-widest text-muted">
                · {call.sentiment}
              </span>
            )}
            {call.status && (
              <span className="font-mono text-[10px] uppercase tracking-widest text-muted">
                · {call.status}
              </span>
            )}
          </div>
        </div>
        <div className="text-right">
          <div className="font-mono text-[10px] text-muted">
            {call.started_at ? new Date(call.started_at).toLocaleTimeString() : "—"}
          </div>
          {call.caller_number && (
            <div className="font-mono text-[10px] text-muted/60">
              {maskNumber(call.caller_number)}
            </div>
          )}
        </div>
      </div>
    </Link>
  );
}

function maskNumber(n: string): string {
  if (n.length < 6) return n;
  return n.slice(0, 3) + "•••••" + n.slice(-2);
}
