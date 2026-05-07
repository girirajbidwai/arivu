// useCallEvents(agent_id) — subscribes to dashboard-api WebSocket and
// returns a typed stream of events. Auto-reconnects with backoff.

"use client";

import { useEffect, useRef, useState } from "react";
import type { ArivuEvent } from "./types";

const API_URL =
  process.env.NEXT_PUBLIC_DASHBOARD_API_URL ||
  process.env.DASHBOARD_API_URL ||
  "http://localhost:8001";

function toWsUrl(httpUrl: string, path: string): string {
  const u = new URL(path, httpUrl);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  return u.toString();
}

export function useCallEvents(agentId: string) {
  const [events, setEvents] = useState<ArivuEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef(0);

  useEffect(() => {
    let cancelled = false;

    function connect() {
      if (cancelled) return;
      const ws = new WebSocket(toWsUrl(API_URL, `/ws/agent/${agentId}`));
      wsRef.current = ws;
      ws.onopen = () => {
        retryRef.current = 0;
        setConnected(true);
      };
      ws.onmessage = (ev) => {
        try {
          const parsed: ArivuEvent = JSON.parse(ev.data);
          setEvents((prev) => [...prev, parsed].slice(-500));
        } catch {
          /* ignore */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (cancelled) return;
        retryRef.current = Math.min(retryRef.current + 1, 6);
        setTimeout(connect, 250 * 2 ** retryRef.current);
      };
      ws.onerror = () => ws.close();
    }

    connect();
    return () => {
      cancelled = true;
      wsRef.current?.close();
    };
  }, [agentId]);

  return { events, connected };
}
