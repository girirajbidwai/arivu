"use client";

import { useState } from "react";
import { api } from "@/lib/api";

type Field = "issue_summary" | "urgency" | "dialect" | "sentiment";

interface Props {
  callId: string;
  agentId: string;
  current: Partial<Record<Field, string | number>>;
  onSaved?: (field: Field, newValue: string) => void;
}

const fieldLabels: Record<Field, string> = {
  issue_summary: "Issue Summary",
  urgency: "Urgency (1–5)",
  dialect: "Dialect",
  sentiment: "Sentiment",
};

const dialectOptions = ["dharwad", "mangaluru", "mysuru", "bengaluru", "unknown"];
const sentimentOptions = ["calm", "neutral", "confused", "anxious", "fearful", "distressed"];

export function OverridePanel({ callId, agentId, current, onSaved }: Props) {
  const [editing, setEditing] = useState<Field | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function startEdit(field: Field) {
    setEditing(field);
    setDraft(String(current[field] ?? ""));
    setError(null);
  }

  async function save() {
    if (!editing) return;
    setSaving(true);
    try {
      await api.postCorrection({
        call_id: callId,
        turn_index: 0,
        agent_id: agentId,
        field: editing,
        old_value: String(current[editing] ?? ""),
        new_value: draft,
      });
      onSaved?.(editing, draft);
      setEditing(null);
    } catch (e: any) {
      setError(e.message || "Failed to save");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-lg border border-warmsoft bg-warmsoft/30 p-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="font-mono text-[11px] tracking-widest text-muted">
          OVERRIDE PANEL
        </span>
        <span className="font-mono text-[10px] text-muted/70">agent · {agentId.slice(0, 8)}</span>
      </div>

      <div className="space-y-3">
        {(Object.keys(fieldLabels) as Field[]).map((f) => (
          <div key={f}>
            <div className="flex items-center justify-between">
              <div>
                <div className="font-mono text-[10px] uppercase tracking-widest text-muted">
                  {fieldLabels[f]}
                </div>
                <div className="text-sm">
                  {editing === f ? (
                    <Editor
                      field={f}
                      value={draft}
                      onChange={setDraft}
                    />
                  ) : (
                    <span>{current[f] ?? "—"}</span>
                  )}
                </div>
              </div>
              {editing === f ? (
                <div className="flex gap-1">
                  <button
                    disabled={saving}
                    onClick={save}
                    className="rounded bg-gold px-2 py-1 font-mono text-[10px] uppercase tracking-widest text-warmdark disabled:opacity-50"
                  >
                    {saving ? "saving…" : "save"}
                  </button>
                  <button
                    onClick={() => setEditing(null)}
                    className="rounded border border-warmsoft px-2 py-1 font-mono text-[10px] uppercase tracking-widest text-muted"
                  >
                    cancel
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => startEdit(f)}
                  className="rounded border border-warmsoft px-2 py-1 font-mono text-[10px] uppercase tracking-widest text-muted hover:border-gold hover:text-gold"
                >
                  edit
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {error && (
        <p className="mt-3 font-mono text-[10px] text-red-400">{error}</p>
      )}
    </div>
  );
}

function Editor({
  field, value, onChange,
}: { field: Field; value: string; onChange: (v: string) => void }) {
  const base =
    "w-full rounded bg-warmdark border border-warmsoft px-2 py-1 text-sm focus:border-gold focus:outline-none";
  if (field === "urgency") {
    return (
      <select className={base} value={value} onChange={(e) => onChange(e.target.value)}>
        {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
      </select>
    );
  }
  if (field === "dialect") {
    return (
      <select className={base} value={value} onChange={(e) => onChange(e.target.value)}>
        {dialectOptions.map((d) => <option key={d} value={d}>{d}</option>)}
      </select>
    );
  }
  if (field === "sentiment") {
    return (
      <select className={base} value={value} onChange={(e) => onChange(e.target.value)}>
        {sentimentOptions.map((s) => <option key={s} value={s}>{s}</option>)}
      </select>
    );
  }
  return (
    <input
      className={base}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      autoFocus
    />
  );
}
