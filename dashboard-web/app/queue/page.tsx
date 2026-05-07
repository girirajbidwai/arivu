"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type CallSummary } from "@/lib/api";
import { CallQueueRow } from "@/components/CallQueueRow";
import { useCallEvents } from "@/lib/ws";
import { DEMO_AGENT_ID } from "@/lib/demoAgent";
const AGENT_ID = DEMO_AGENT_ID;

export default function QueuePage() {
  const [calls, setCalls] = useState<CallSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Live updates: any new event re-fetches the call list.
  // Cheap enough for a hackathon and keeps the queue accurate.
  const { events, connected } = useCallEvents(AGENT_ID);

  async function refresh() {
    try {
      const list = await api.listCalls();
      setCalls(
        [...list].sort((a, b) => (b.urgency ?? 0) - (a.urgency ?? 0))
      );
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { refresh(); }, []);
  useEffect(() => {
    if (events.length === 0) return;
    refresh();
  }, [events.length]);

  return (
    <main className="min-h-screen bg-warmdark text-[#EFE9DB]">
      <header className="flex items-center justify-between border-b border-warmsoft px-6 py-4">
        <div>
          <Link href="/" className="font-display text-2xl text-gold">Arivu</Link>
          <p className="font-mono text-[10px] tracking-widest text-muted">
            CALL QUEUE · sorted by urgency
          </p>
        </div>
        <ConnState connected={connected} />
      </header>

      <section className="mx-auto max-w-3xl space-y-3 p-6">
        {loading && (
          <p className="font-mono text-[11px] text-muted">loading queue…</p>
        )}
        {error && (
          <div className="rounded-md border border-red-400/40 bg-red-500/10 p-3 font-mono text-[11px] text-red-300">
            {error}
          </div>
        )}
        {!loading && !error && calls.length === 0 && (
          <div className="rounded-md border border-warmsoft bg-warmsoft/30 p-6 text-center">
            <p className="font-mono text-[11px] tracking-widest text-muted">
              NO ACTIVE CALLS
            </p>
            <p className="mt-2 text-sm text-muted">
              When a citizen dials the 1092 number, the call lands here in real time.
            </p>
          </div>
        )}
        {calls.map((c) => <CallQueueRow key={c.id} call={c} />)}
      </section>
    </main>
  );
}

function ConnState({ connected }: { connected: boolean }) {
  return (
    <span className={`flex items-center gap-2 rounded-full border px-3 py-1 font-mono text-[11px] tracking-widest ${
      connected
        ? "border-sage/50 text-sage"
        : "border-warmsoft text-muted"
    }`}>
      <span className={`h-2 w-2 rounded-full ${connected ? "bg-sage" : "bg-muted"}`} />
      {connected ? "LIVE" : "CONNECTING…"}
    </span>
  );
}
