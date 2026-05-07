"""
In-memory store — fallback when Supabase env isn't set or Realtime is
flaky. Kept tiny on purpose; the durable record always lives in
Supabase. This buys us:

  * Local dev without internet.
  * Continuous demo if Realtime drops mid-call.
  * A debugging scratchpad — events you can `tail` from a REST endpoint.

Lossy across process restarts. That's by design.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _MemoryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: dict[str, dict] = {}
        self._turns: dict[str, list[dict]] = defaultdict(list)
        self._events: list[dict] = []
        self._corrections: list[dict] = []
        self._handoffs: list[dict] = []

    # ── reads ──────────────────────────────────────────────────────────
    def list_calls(self) -> list[dict]:
        with self._lock:
            calls = list(self._calls.values())
        calls.sort(
            key=lambda c: (c.get("urgency") or 0, c.get("started_at") or ""),
            reverse=True,
        )
        return calls

    def get_call(self, call_id: str) -> dict | None:
        with self._lock:
            call = self._calls.get(call_id)
            if call is None:
                return None
            return {**call, "turns": list(self._turns.get(call_id, []))}

    def get_turns(self, call_id: str) -> list[dict]:
        with self._lock:
            return list(self._turns.get(call_id, []))

    def list_handoffs(self, call_id: str | None = None) -> list[dict]:
        with self._lock:
            if call_id is None:
                return list(self._handoffs)
            return [h for h in self._handoffs if h.get("call_id") == call_id]

    # ── writes ─────────────────────────────────────────────────────────
    def append_event(self, event: dict) -> None:
        """Apply an `events.json` envelope to derived state."""
        with self._lock:
            self._events.append(event)
            call_id = event.get("call_id")
            if not call_id:
                return
            data = event.get("data") or {}
            etype = event.get("event_type")
            ts = event.get("timestamp") or _now()

            call = self._calls.setdefault(call_id, {
                "id": call_id, "status": "active", "started_at": ts,
            })

            if etype == "transcript_final":
                self._turns[call_id].append({
                    "id": f"mem-turn-{len(self._turns[call_id]) + 1}",
                    "call_id": call_id,
                    "turn_index": event.get("turn_index", 0),
                    "speaker": data.get("speaker", "citizen"),
                    "transcript": data.get("transcript", ""),
                    "interpretation": data.get("issue_summary", ""),
                    "confidence": data.get("confidence"),
                    "created_at": ts,
                })
            elif etype in ("verification_pending", "verification_confirmed"):
                if data.get("issue_summary"):
                    call["issue_summary"] = data["issue_summary"]
                if data.get("urgency") is not None:
                    call["urgency"] = data["urgency"]
                if etype == "verification_confirmed":
                    call["status"] = "verified"
            elif etype == "dialect_detected":
                call["dialect"] = data.get("dialect")
            elif etype == "sentiment_updated":
                call["sentiment"] = data.get("sentiment")
            elif etype == "handoff_initiated":
                call["status"] = "handed_off"
                self._handoffs.append({
                    "id": f"mem-handoff-{len(self._handoffs) + 1}",
                    "call_id": call_id,
                    "reason": data.get("handoff_reason", "unknown"),
                    "created_at": ts,
                })
            elif etype == "call_ended":
                call["status"] = "ended"
                call["ended_at"] = ts

    def append_correction(self, payload: dict[str, Any]) -> dict:
        with self._lock:
            row = {**payload, "id": f"mem-corr-{len(self._corrections) + 1}",
                   "created_at": _now()}
            self._corrections.append(row)
            return row

    def append_handoff(self, payload: dict[str, Any]) -> dict:
        with self._lock:
            row = {**payload, "id": f"mem-handoff-{len(self._handoffs) + 1}",
                   "created_at": _now()}
            self._handoffs.append(row)
            return row


memory_store = _MemoryStore()
