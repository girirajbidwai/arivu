// Thin client over the dashboard-api REST surface. Strict types via
// shared Event shapes; CORS is handled server-side by FastAPI.

import type { ArivuEvent } from "./types";

export const API_URL =
  process.env.NEXT_PUBLIC_DASHBOARD_API_URL ||
  process.env.DASHBOARD_API_URL ||
  "http://localhost:8001";

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init.headers || {}),
    },
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${await r.text()}`);
  return (await r.json()) as T;
}

export interface CallSummary {
  id: string;
  caller_number?: string;
  dialect?: string;
  language?: string;
  issue_summary?: string;
  urgency?: number;
  sentiment?: string;
  status?: string;
  started_at?: string;
}

export interface CallDetail extends CallSummary {
  turns: Array<{
    id: string;
    turn_index: number;
    speaker: string;
    transcript: string;
    interpretation?: string;
    confidence?: number;
    created_at: string;
  }>;
}

export const api = {
  health: () => req<Record<string, unknown>>("/health"),
  listCalls: () => req<CallSummary[]>("/calls/"),
  getCall: (id: string) => req<CallDetail>(`/calls/${id}`),
  postCorrection: (body: {
    call_id: string;
    turn_index: number;
    agent_id: string;
    field: "issue_summary" | "urgency" | "dialect" | "sentiment";
    old_value: string;
    new_value: string;
  }) => req<{ id: string; status: string }>("/corrections/", {
    method: "POST",
    body: JSON.stringify(body),
  }),
  postHandoff: (body: { call_id: string; reason: string; agent_id?: string }) =>
    req<{ id: string; status: string }>("/handoffs/", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  loginMagicLink: (email: string) =>
    req<{ message: string }>("/agents/login", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),
  postEventIngest: (event: ArivuEvent) =>
    req<{ ok: boolean }>("/events/ingest", {
      method: "POST",
      body: JSON.stringify(event),
    }),
};
