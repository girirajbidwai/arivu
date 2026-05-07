from httpx import ASGITransport, AsyncClient
import pytest

from voice import main as voice_main


class _FakePublisher:
    def __init__(self):
        self.events = []

    async def publish(self, envelope):
        self.events.append(envelope)


class _FakeBrainProxy:
    async def process(self, **kwargs):
        return {
            "action": "handoff",
            "language_out": "kn-IN",
            "dialect_out": "dharwad",
            "text": "ಧನ್ಯವಾದ. ನಿಮ್ಮನ್ನು ಈಗ ಅಧಿಕಾರಿಯೊಂದಿಗೆ ಸಂಪರ್ಕಿಸುತ್ತೇನೆ.",
            "issue_summary": "Neighbour harassment",
            "urgency_1_to_5": 5,
            "sentiment": "distressed",
            "confidence": 0.91,
            "fsm_state": "verified",
            "handoff_reason": "verification_complete",
        }

    async def close(self):
        return None


@pytest.fixture
async def client():
    transport = ASGITransport(app=voice_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_web_turn_handoff_response(monkeypatch, client):
    publisher = _FakePublisher()

    async def fake_transcribe(*args, **kwargs):
        return {
            "transcript": "ನಮ್ಮ ಪಕ್ಕದವರು ತೊಂದರೆ ಕೊಡ್ತಿದ್ದಾರೆ",
            "language_code": "kn-IN",
            "duration_s": 1.2,
            "latency_ms": 120.0,
        }

    async def fake_synthesize(text, **kwargs):
        return {"audio_b64": "ZmFrZQ==", "mime": "audio/wav", "latency_ms": 90.0}

    monkeypatch.setattr(voice_main, "transcribe", fake_transcribe)
    monkeypatch.setattr(voice_main, "synthesize", fake_synthesize)
    monkeypatch.setattr(voice_main, "BrainProxy", _FakeBrainProxy)
    monkeypatch.setattr(voice_main, "get_publisher", lambda: publisher)

    response = await client.post(
        "/web/turn",
        files={"audio": ("turn.webm", b"fake-audio", "audio/webm")},
        data={"call_id": "33333333-3333-3333-3333-333333333333", "history": ""},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "handoff"
    assert body["issue_summary"] == "Neighbour harassment"
    assert body["telephony_status"] in ("ready", "pending")
    assert body.get("handoff_note") is None or "Twilio" in body["handoff_note"]
    assert body["transcript"] == "ನಮ್ಮ ಪಕ್ಕದವರು ತೊಂದರೆ ಕೊಡ್ತಿದ್ದಾರೆ"
    assert any(e["event_type"] == "transcript_final" for e in publisher.events)


@pytest.mark.asyncio
async def test_web_turn_empty_transcript_repompts(monkeypatch, client):
    publisher = _FakePublisher()

    async def fake_transcribe(*args, **kwargs):
        return {"transcript": "", "language_code": "kn-IN", "duration_s": 0.5, "latency_ms": 100.0}

    async def fake_synthesize(text, **kwargs):
        return {"audio_b64": "ZmFrZQ==", "mime": "audio/wav", "latency_ms": 80.0}

    monkeypatch.setattr(voice_main, "transcribe", fake_transcribe)
    monkeypatch.setattr(voice_main, "synthesize", fake_synthesize)
    monkeypatch.setattr(voice_main, "get_publisher", lambda: publisher)

    response = await client.post(
        "/web/turn",
        files={"audio": ("turn.webm", b"fake-audio", "audio/webm")},
        data={"call_id": "44444444-4444-4444-4444-444444444444", "history": ""},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "reprompt"
    assert body["transcript"] == ""
    assert body["audio_b64"] == "ZmFrZQ=="
