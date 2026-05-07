"""
EventPublisher — voice / brain → dashboard-api.

The voice pipeline emits an `events.json`-conformant envelope on every
turn / dialect / sentiment / safety / handoff transition. We POST every
event to the dashboard-api `/events/ingest` endpoint, which is the
single point of truth for fan-out (Supabase Realtime + WSHub broadcast
+ table mirror).

Why a single channel: keeping the voice service ignorant of Supabase
internals means we can swap Supabase for anything else later without
touching the pipeline. It also makes the voice service trivially mockable
in tests — point at a localhost dashboard-api stub.

Failures are logged but never raised. The voice pipeline must never
crash because the dashboard is slow.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger("arivu.events")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventPublisher:
    """
    Constructed once per process. All `publish` calls are async and
    fire-and-forget from the brain's POV — the brain never waits on
    a network round trip.
    """

    def __init__(self, dashboard_url: Optional[str] = None) -> None:
        resolved = dashboard_url or os.getenv("DASHBOARD_API_URL", "http://localhost:8001")
        self.dashboard_url = resolved.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.dashboard_url,
            timeout=httpx.Timeout(connect=0.5, read=1.5, write=0.5, pool=0.5),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        logger.info("event publisher → %s", self.dashboard_url)

    async def publish(self, event: dict) -> None:
        """Add a timestamp if missing and fire to dashboard-api."""
        if "timestamp" not in event:
            event["timestamp"] = _now_iso()

        # Detach so the brain hot path never waits on a network call.
        asyncio.create_task(self._send(event))

    async def _send(self, event: dict) -> None:
        try:
            t0 = time.perf_counter()
            r = await self._client.post("/events/ingest", json=event)
            dt = (time.perf_counter() - t0) * 1000
            if r.status_code >= 400:
                logger.debug("event ingest %s in %.0fms: %s",
                             r.status_code, dt, r.text[:120])
        except httpx.HTTPError as e:
            logger.debug("event ingest unreachable (non-fatal): %s", e)

    async def close(self) -> None:
        await self._client.aclose()


# ── Module-level singleton, lazily constructed ─────────────────────────
_publisher: Optional[EventPublisher] = None


def get_publisher() -> EventPublisher:
    global _publisher
    if _publisher is None:
        _publisher = EventPublisher()
    return _publisher
