"""
/events/ingest tests — voice service fallback channel.

We patch the Supabase client so the test runs offline, and we exercise
broadcast via the WSHub by attaching a fake WebSocket and asserting
the right payload reaches it.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from dashboard_api.main import app
from dashboard_api.services.ws_hub import hub


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class _FakeWS:
    def __init__(self):
        self.sent = []
        self.client_state = MagicMock()

    async def accept(self):  # called by hub.connect (we skip it via direct add)
        pass

    async def send_json(self, payload):
        self.sent.append(payload)


@pytest.fixture
def attached_ws():
    """Inject a fake websocket into the hub so broadcast lands somewhere."""
    fake = _FakeWS()
    hub._connections.setdefault("agent-test", set()).add(fake)
    yield fake
    hub._connections.pop("agent-test", None)


async def test_ingest_basic(client, attached_ws):
    event = {
        "event_type": "verification_pending",
        "call_id": "00000000-0000-0000-0000-000000000abc",
        "turn_index": 1,
        "data": {
            "issue_summary": "Neighbour harassment",
            "urgency": 4,
            "fsm_state": "verifying",
        },
    }
    with patch("dashboard_api.routers.events._supabase_or_none", return_value=None):
        r = await client.post("/events/ingest", json=event)
    assert r.status_code == 200
    assert r.json()["event_type"] == "verification_pending"
    assert any(s["event_type"] == "verification_pending" for s in attached_ws.sent)


async def test_ingest_handoff_writes_handoff_table(client):
    sb = MagicMock()
    sb.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])
    sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[])

    event = {
        "event_type": "handoff_initiated",
        "call_id": "00000000-0000-0000-0000-000000000def",
        "data": {"handoff_reason": "verification_complete"},
    }

    with patch("dashboard_api.routers.events._supabase_or_none", return_value=sb):
        r = await client.post("/events/ingest", json=event)
    assert r.status_code == 200

    # The mirror should have hit at least the handoffs table.
    tables_called = [c.args[0] for c in sb.table.call_args_list]
    assert "handoffs" in tables_called
    assert "calls" in tables_called  # always upserted first


async def test_ingest_never_rejects_garbage(client):
    """Even a minimal envelope must come back 200; we don't lose events."""
    with patch("dashboard_api.routers.events._supabase_or_none", return_value=None):
        r = await client.post("/events/ingest", json={"event_type": "weird"})
    assert r.status_code == 200
