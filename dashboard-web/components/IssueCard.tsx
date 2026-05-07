"use client";
import type { ArivuEvent } from "@/lib/types";

interface Props {
  events: ArivuEvent[];
  fallback?: {
    issue_summary?: string;
    urgency?: number;
    dialect?: string;
    sentiment?: string;
  };
  onEdit?: (field: "issue_summary" | "urgency" | "dialect" | "sentiment") => void;
}

export function IssueCard({ events, fallback, onEdit }: Props) {
  const verified = [...events]
    .reverse()
    .find((e) => e.event_type === "verification_confirmed");
  const pending = [...events]
    .reverse()
    .find((e) => e.event_type === "verification_pending");

  // Show pending state until verified — keeps the agent oriented.
  const data =
    verified?.data ||
    pending?.data ||
    (fallback?.issue_summary
      ? {
          issue_summary: fallback.issue_summary,
          urgency: fallback.urgency,
          dialect: fallback.dialect,
          sentiment: fallback.sentiment,
        }
      : undefined);
  const state = verified ? "verified" : pending ? "verifying" : fallback?.issue_summary ? "verified" : "pending";

  if (!data) {
    return (
      <div className="rounded-lg border border-warmsoft bg-warmsoft/30 p-4">
        <div className="font-mono text-[11px] tracking-widest text-muted">ISSUE CARD</div>
        <p className="mt-3 text-sm text-muted">Waiting for verified understanding…</p>
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-lg border border-gold/30 bg-warmsoft/60 p-4">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[11px] tracking-widest text-gold">
          {state === "verified" ? "VERIFIED ISSUE" : "AWAITING CONFIRMATION"}
        </span>
        <UrgencyPill urgency={data.urgency ?? 3} />
      </div>

      <FieldRow label="Issue" value={data.issue_summary || "—"}
                onEdit={() => onEdit?.("issue_summary")} />

      {pending?.data?.verify_phrase && state === "verifying" && (
        <div className="rounded-md border border-warmsoft bg-warmdark/60 p-3">
          <div className="font-mono text-[10px] tracking-widest text-muted">
            VERIFY PHRASE (read aloud to citizen)
          </div>
          <p className="font-kn text-sm leading-snug">{pending.data.verify_phrase}</p>
        </div>
      )}

      <FieldRow label="Dialect" value={data.dialect || "—"} onEdit={() => onEdit?.("dialect")} />
      <FieldRow label="Sentiment" value={data.sentiment || "—"} onEdit={() => onEdit?.("sentiment")} />
    </div>
  );
}

function FieldRow({
  label, value, onEdit,
}: { label: string; value: string | number; onEdit?: () => void }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div>
        <div className="font-mono text-[10px] uppercase tracking-widest text-muted">{label}</div>
        <div className="text-sm">{value}</div>
      </div>
      {onEdit && (
        <button
          onClick={onEdit}
          className="rounded border border-warmsoft px-2 py-1 font-mono text-[10px] uppercase tracking-widest text-muted hover:border-gold hover:text-gold"
        >
          edit
        </button>
      )}
    </div>
  );
}

function UrgencyPill({ urgency }: { urgency: number }) {
  const label = urgency >= 5 ? "SEVERE" : urgency >= 4 ? "HIGH" : urgency >= 3 ? "ELEVATED" : "LOW";
  const color =
    urgency >= 5 ? "bg-red-500" :
    urgency >= 4 ? "bg-terracotta" :
    urgency >= 3 ? "bg-amber-400" :
    "bg-sage";
  return (
    <span className={`rounded-full ${color} px-2 py-0.5 font-mono text-[10px] tracking-widest text-warmdark`}>
      U{urgency} · {label}
    </span>
  );
}
