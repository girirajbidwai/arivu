"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallEvents } from "@/lib/ws";
import { api, type CallDetail } from "@/lib/api";
import { LiveTranscript } from "@/components/LiveTranscript";
import { IssueCard } from "@/components/IssueCard";
import { DialectBadge } from "@/components/DialectBadge";
import { SentimentMeter } from "@/components/SentimentMeter";
import { HandoffAlert } from "@/components/HandoffAlert";
import { OverridePanel } from "@/components/OverridePanel";
import { InterpreterIndicator } from "@/components/InterpreterIndicator";
import type { ArivuEvent } from "@/lib/types";
import { DEMO_AGENT_ID } from "@/lib/demoAgent";
const AGENT_ID = DEMO_AGENT_ID;

export default function CallPage() {
  const params = useParams<{ id: string }>();
  const callId = params.id;

  const [callDetail, setCallDetail] = useState<CallDetail | null>(null);
  const { events: liveEvents, connected } = useCallEvents(AGENT_ID);

  // Filter live events to just this call so the UI never gets crossed.
  const liveCallEvents = useMemo<ArivuEvent[]>(
    () => liveEvents.filter((e) => e.call_id === callId),
    [liveEvents, callId],
  );

  const fallbackEvents = useMemo<ArivuEvent[]>(() => {
    if (!callDetail) return [];
    const synthetic: ArivuEvent[] = callDetail.turns.map((turn) => ({
      event_type: "transcript_final",
      call_id: callId,
      turn_index: turn.turn_index,
      timestamp: turn.created_at,
      data: {
        speaker: (turn.speaker as any) || "citizen",
        transcript: turn.transcript,
        language: (callDetail.language as any) || "kn-IN",
      },
    }));
    if (callDetail.dialect) {
      synthetic.push({
        event_type: "dialect_detected",
        call_id: callId,
        timestamp: callDetail.started_at || new Date().toISOString(),
        data: { dialect: callDetail.dialect as any },
      });
    }
    if (callDetail.sentiment) {
      synthetic.push({
        event_type: "sentiment_updated",
        call_id: callId,
        timestamp: callDetail.started_at || new Date().toISOString(),
        data: { sentiment: callDetail.sentiment as any },
      });
    }
    if (callDetail.issue_summary) {
      synthetic.push({
        event_type: "verification_confirmed",
        call_id: callId,
        timestamp: callDetail.started_at || new Date().toISOString(),
        data: {
          issue_summary: callDetail.issue_summary,
          urgency: callDetail.urgency,
          dialect: callDetail.dialect as any,
          sentiment: callDetail.sentiment as any,
          fsm_state: "verified",
        },
      });
    }
    return synthetic;
  }, [callDetail, callId]);

  const events = liveCallEvents.length > 0 ? liveCallEvents : fallbackEvents;

  // Backfill from REST so a hard reload mid-call doesn't lose history.
  useEffect(() => {
    let alive = true;
    api.getCall(callId)
      .then((d) => { if (alive) setCallDetail(d); })
      .catch(() => {});
    return () => { alive = false; };
  }, [callId]);

  const lastDialect = [...events].reverse().find((e) => e.data?.dialect)?.data;
  const lastSentiment = [...events].reverse().find((e) => e.data?.sentiment)?.data;
  const lastIssue = [...events].reverse().find((e) => e.data?.issue_summary)?.data;

  const current = {
    issue_summary: lastIssue?.issue_summary || callDetail?.issue_summary || "",
    urgency: lastIssue?.urgency ?? callDetail?.urgency ?? 3,
    dialect: lastDialect?.dialect || callDetail?.dialect || "unknown",
    sentiment: lastSentiment?.sentiment || callDetail?.sentiment || "neutral",
  };

  return (
    <main className="min-h-screen bg-warmdark text-[#EFE9DB]">
      <header className="border-b border-warmsoft px-6 py-4 flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-4">
          <Link
            href="/queue"
            className="font-mono text-[11px] tracking-widest text-muted hover:text-gold"
          >
            ← QUEUE
          </Link>
          <div>
            <h1 className="font-display text-xl text-gold">Active Call</h1>
            <p className="font-mono text-[10px] tracking-widest text-muted">
              call · {callId.slice(0, 8)} · {connected ? "live" : "reconnecting…"}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <DialectBadge
            dialect={(current.dialect as any) ?? "unknown"}
            confidence={lastDialect?.dialect_confidence}
          />
          <InterpreterIndicator events={events} />
        </div>
      </header>

      <div className="grid grid-cols-1 gap-4 p-6 lg:grid-cols-12">
        <section className="lg:col-span-7 h-[72vh]">
          <h2 className="mb-2 font-mono text-[11px] tracking-widest text-muted">
            LIVE TRANSCRIPT
          </h2>
          <LiveTranscript events={events} />
        </section>

        <section className="lg:col-span-5 space-y-4">
          <IssueCard
            events={events}
            fallback={current}
          />
          <div className="rounded-lg border border-warmsoft bg-warmsoft/30 p-4">
            <SentimentMeter
              sentiment={(current.sentiment as any) || "neutral"}
              confidence={lastSentiment?.sentiment_confidence}
            />
          </div>
          <OverridePanel
            callId={callId}
            agentId={AGENT_ID}
            current={current}
          />
        </section>
      </div>

      <HandoffAlert
        events={events}
        onTakeOver={() =>
          api.postHandoff({ call_id: callId, reason: "agent_takeover", agent_id: AGENT_ID })
            .catch(() => {})
        }
      />
    </main>
  );
}
