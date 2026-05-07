# tests/test_calls.py
"""
Smoke tests for the Arivu Dashboard API.
Supabase calls are patched so tests run fully offline.
"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from httpx import AsyncClient, ASGITransport
from dashboard_api.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sb_result(data):
    """Return a mock Supabase query result."""
    m = MagicMock()
    m.data = data
    return m


def _sb_mock(table_data=None):
    """Return a sync Supabase client mock."""
    sb = MagicMock()
    tbl = MagicMock()
    # Chain: .table().select().order().execute() / .eq().single().execute()
    chain = MagicMock()
    chain.execute.return_value = _sb_result(table_data or [])
    chain.order.return_value = chain
    chain.eq.return_value = chain
    chain.single.return_value = chain
    chain.insert.return_value = chain
    tbl.select.return_value = chain
    tbl.insert.return_value = chain
    sb.table.return_value = tbl
    return sb


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["service"] == "arivu-dashboard-api"


# ---------------------------------------------------------------------------
# /calls
# ---------------------------------------------------------------------------
async def test_list_calls_empty(client):
    with patch("dashboard_api.routers.calls.get_supabase", return_value=_sb_mock([])):
        r = await client.get("/calls/")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_calls_with_data(client):
    fake = [{"id": "abc", "urgency": 5}]
    with patch("dashboard_api.routers.calls.get_supabase", return_value=_sb_mock(fake)):
        r = await client.get("/calls/")
    assert r.status_code == 200
    assert r.json()[0]["id"] == "abc"


async def test_list_calls_falls_back_to_memory_store(client, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with patch("dashboard_api.routers.events._HAS_SUPABASE", False):
        await client.post("/events/ingest", json={
            "event_type": "verification_pending",
            "call_id": "00000000-0000-0000-0000-00000000f111",
            "timestamp": "2026-05-03T00:00:00Z",
            "data": {"issue_summary": "offline-test", "urgency": 4, "fsm_state": "verifying"},
        })
    r = await client.get("/calls/")
    assert r.status_code == 200
    assert any(call["id"] == "00000000-0000-0000-0000-00000000f111" for call in r.json())


async def test_get_call_bad_uuid(client):
    from postgrest.exceptions import APIError
    sb = MagicMock()
    tbl = MagicMock()
    chain = MagicMock()
    chain.eq.return_value = chain
    chain.single.return_value = chain
    chain.order.return_value = chain
    chain.execute.side_effect = APIError({"message": "invalid uuid", "code": "22P02", "hint": None, "details": None})
    tbl.select.return_value = chain
    sb.table.return_value = tbl
    with patch("dashboard_api.routers.calls.get_supabase", return_value=sb):
        r = await client.get("/calls/not-a-uuid")
    assert r.status_code == 400
    assert "invalid uuid" in r.json()["detail"]


async def test_get_transcript_bad_uuid(client):
    from postgrest.exceptions import APIError
    sb = MagicMock()
    tbl = MagicMock()
    chain = MagicMock()
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.execute.side_effect = APIError({"message": "invalid uuid", "code": "22P02", "hint": None, "details": None})
    tbl.select.return_value = chain
    sb.table.return_value = tbl
    with patch("dashboard_api.routers.calls.get_supabase", return_value=sb):
        r = await client.get("/calls/not-a-uuid/transcript")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /handoffs
# ---------------------------------------------------------------------------
async def test_get_handoffs_bad_uuid(client):
    from postgrest.exceptions import APIError
    sb = MagicMock()
    tbl = MagicMock()
    chain = MagicMock()
    chain.eq.return_value = chain
    chain.execute.side_effect = APIError({"message": "invalid uuid", "code": "22P02", "hint": None, "details": None})
    tbl.select.return_value = chain
    sb.table.return_value = tbl
    with patch("dashboard_api.routers.handoffs.get_supabase", return_value=sb):
        r = await client.get("/handoffs/?call_id=bad-uuid")
    assert r.status_code == 400


async def test_get_handoffs_valid(client):
    with patch("dashboard_api.routers.handoffs.get_supabase", return_value=_sb_mock([])):
        r = await client.get("/handoffs/?call_id=00000000-0000-0000-0000-000000000000")
    assert r.status_code == 200


async def test_post_handoff(client):
    fake = [{"id": "mock-id"}]
    with patch("dashboard_api.routers.handoffs.get_supabase", return_value=_sb_mock(fake)):
        r = await client.post("/handoffs/", json={
            "call_id": "00000000-0000-0000-0000-000000000000",
            "reason": "safety_whisper"
        })
    assert r.status_code == 200
    assert r.json()["status"] == "logged"


# ---------------------------------------------------------------------------
# /corrections
# ---------------------------------------------------------------------------
async def test_post_correction(client):
    fake = [{"id": "corr-1"}]
    with patch("dashboard_api.routers.corrections.get_supabase", return_value=_sb_mock(fake)), \
         patch("dashboard_api.routers.corrections.forward_to_brain", new_callable=AsyncMock):
        r = await client.post("/corrections/", json={
            "call_id": "00000000-0000-0000-0000-000000000000",
            "turn_index": 1,
            "agent_id": "00000000-0000-0000-0000-000000000001",
            "field": "urgency",
            "old_value": "3",
            "new_value": "5"
        })
    assert r.status_code == 200
    assert r.json()["status"] == "saved"


# ---------------------------------------------------------------------------
# /agents
# ---------------------------------------------------------------------------
async def test_agents_me_no_token(client):
    r = await client.get("/agents/me")
    assert r.status_code == 401


async def test_agents_me_bad_token(client):
    sb = MagicMock()
    sb.auth.get_user.side_effect = Exception("invalid JWT")
    with patch("dashboard_api.routers.agents.get_supabase", return_value=sb):
        r = await client.get("/agents/me", headers={"Authorization": "Bearer bad-token"})
    assert r.status_code == 401
