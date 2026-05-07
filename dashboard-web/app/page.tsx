"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { startMockCall } from "@/lib/mockEvents";
import type { ArivuEvent } from "@/lib/types";
import { LiveTranscript } from "@/components/LiveTranscript";
import { IssueCard } from "@/components/IssueCard";
import { DialectBadge } from "@/components/DialectBadge";
import { SentimentMeter } from "@/components/SentimentMeter";
import { HandoffAlert } from "@/components/HandoffAlert";

export default function Home() {
  const [events, setEvents] = useState<ArivuEvent[]>([]);

  // Auto-start a scripted call on first load so the dashboard is never
  // empty for the demo. Replaced by useCallEvents() once the WebSocket
  // is wired to the dashboard-api.
  useEffect(() => {
    setEvents([]);
    startMockCall("demo-001", (e) => setEvents((prev) => [...prev, e]));
  }, []);

  const lastDialect = [...events].reverse().find((e) => e.data?.dialect)?.data;
  const lastSentiment = [...events].reverse().find((e) => e.data?.sentiment)?.data;
  const verifyPending = events.some((e) => e.event_type === "verification_pending");
  const verified = events.some((e) => e.event_type === "verification_confirmed");
  const handoff = events.some((e) => e.event_type === "handoff_initiated");

  return (
    <main className="min-h-screen bg-warmdark text-[#EFE9DB]">
      <header className="border-b border-warmsoft px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="font-display text-2xl text-gold">Arivu</h1>
          <p className="font-mono text-[10px] tracking-widest text-muted">
            1092 HELPLINE CONSOLE · Understand first. Respond right.
          </p>
        </div>
        <div className="flex gap-3 items-center">
          <Link
            href="/talk"
            className="rounded-md bg-gold px-3 py-1 font-mono text-[11px] tracking-widest text-warmdark hover:opacity-90"
          >
            BROWSER VOICE →
          </Link>
          <DialectBadge
            dialect={(lastDialect?.dialect as any) ?? "unknown"}
            confidence={lastDialect?.dialect_confidence}
          />
          <StatusPill verifyPending={verifyPending} verified={verified} handoff={handoff} />
          <Link
            href="/queue"
            className="rounded-md border border-gold/40 px-3 py-1 font-mono text-[11px] tracking-widest text-gold hover:bg-gold/10"
          >
            ENTER QUEUE →
          </Link>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-4 p-6 lg:grid-cols-12">
        <section className="lg:col-span-7 h-[70vh]">
          <h2 className="mb-2 font-mono text-[11px] tracking-widest text-muted">LIVE TRANSCRIPT</h2>
          <LiveTranscript events={events} />
        </section>

        <section className="lg:col-span-5 space-y-4">
          <IssueCard events={events} onEdit={(f) => console.log("edit", f)} />
          <div className="rounded-lg border border-warmsoft bg-warmsoft/30 p-4">
            <SentimentMeter
              sentiment={(lastSentiment?.sentiment as any) || "neutral"}
              confidence={lastSentiment?.sentiment_confidence}
            />
          </div>
        </section>
      </div>

      <HandoffAlert events={events} />
    </main>
  );
}

function StatusPill({
  verifyPending, verified, handoff,
}: { verifyPending: boolean; verified: boolean; handoff: boolean }) {
  const label = handoff ? "HANDOFF" : verified ? "VERIFIED" : verifyPending ? "VERIFYING" : "LISTENING";
  const color = handoff
    ? "bg-red-500 text-warmdark"
    : verified
    ? "bg-sage text-warmdark"
    : verifyPending
    ? "bg-gold text-warmdark"
    : "bg-warmsoft text-muted";
  return (
    <span className={`rounded-full ${color} px-3 py-1 font-mono text-[11px] tracking-widest`}>
      {label}
    </span>
  );
}
