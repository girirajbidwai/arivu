"""
/events/ingest — voice-service fallback channel.

The voice pipeline emits an `events.json`-conformant envelope on every
turn / dialect / sentiment / safety / handoff transition. The primary
path is Supabase Realtime; this REST endpoint is the fallback so we
never lose an event when Realtime drops or isn't configured.

What this router does on every event:
  1. Mirror it into Supabase tables (calls / turns / handoffs) so the
     normal `GET /calls/...` endpoints stay accurate.
  2. Broadcast it to every connected agent over the WSHub.
  3. Always append to the in-memory store so /demo/turn works offline.

The endpoint never rejects a payload — even malformed events are useful
for debugging. Unknown fields are ignored, not errored.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter

from ..services.ws_hub import hub
from ..store import memory_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/events", tags=["events"])

_HAS_SUPABASE = bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY"))


def _supabase_or_none():
    if not _HAS_SUPABASE:
        return None
    try:
        from ..db.client import get_supabase
        return get_supabase()
    except Exception as e:
        logger.warning("supabase client unavailable: %s", e)
        return None


@router.post("/ingest")
async def ingest(event: dict[str, Any]) -> dict[str, Any]:
    """Single ingest point — voice service POSTs every events.json envelope here."""
    # 1. In-memory mirror (always)
    memory_store.append_event(event)

    # 2. Supabase mirror — best-effort.
    sb = _supabase_or_none()
    if sb is not None:
        try:
            await _mirror_to_supabase(sb, event)
        except Exception as e:
            # Never fail the ingest; log and move on.
            logger.warning("supabase mirror failed for %s: %s",
                           event.get("event_type"), e)

    # 3. Fan-out to every agent (broadcast_all walks the connection map).
    try:
        await hub.broadcast_all(event)
    except Exception as e:  # pragma: no cover
        logger.debug("broadcast failed (non-fatal): %s", e)

    return {"ok": True, "event_type": event.get("event_type")}


# ── helpers ────────────────────────────────────────────────────────────
async def _mirror_to_supabase(sb, event: dict[str, Any]) -> None:
    """
    Translate one events.json envelope into upserts on calls/turns/handoffs.
    Synchronous Supabase client → run on a thread to avoid blocking the loop.
    """
    import asyncio

    etype = event.get("event_type")
    call_id = event.get("call_id")
    if not call_id:
        return
    data = event.get("data") or {}
    ts = event.get("timestamp")

    def _do() -> None:
        # Always upsert the calls row first so foreign-key inserts on
        # turns / handoffs / corrections succeed.
        call_row = {"id": call_id}
        for key in ("dialect", "language", "issue_summary", "urgency", "sentiment"):
            v = data.get(key)
            if v is not None:
                call_row[key] = v

        if etype == "call_started":
            call_row.setdefault("status", "active")
            call_row.setdefault("started_at", ts)
        elif etype == "verification_confirmed":
            call_row["status"] = "verified"
        elif etype == "handoff_initiated":
            call_row["status"] = "handed_off"
        elif etype == "call_ended":
            call_row["status"] = "ended"
            call_row["ended_at"] = ts

        if len(call_row) > 1 or etype == "call_started":
            try:
                sb.table("calls").upsert(call_row, on_conflict="id").execute()
            except Exception as e:
                logger.debug("calls upsert noop: %s", e)

        # Per-event-type child writes
        if etype == "transcript_final":
            sb.table("turns").insert({
                "call_id": call_id,
                "turn_index": event.get("turn_index", 0),
                "speaker": data.get("speaker", "citizen"),
                "transcript": data.get("transcript", ""),
                "interpretation": data.get("issue_summary"),
                "confidence": data.get("confidence"),
                "dialect": data.get("dialect"),
                "sentiment": data.get("sentiment"),
            }).execute()
        elif etype == "handoff_initiated":
            sb.table("handoffs").insert({
                "call_id": call_id,
                "reason": data.get("handoff_reason", "verification_complete"),
            }).execute()

    await asyncio.to_thread(_do)
