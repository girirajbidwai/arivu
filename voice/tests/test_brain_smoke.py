"""
Brain smoke test — exercise /process without hitting any external LLM.

We patch the active provider (unified-turn seam) so the test runs
offline in <1s. The point is to assert the FSM wiring + event
publishing + branch logic in `process()` is correct, independent of
which provider (Sarvam, Gemini, heuristic) is configured.
"""

import pytest

from voice.brain.main import active_calls
from voice.brain.providers.base import UnifiedTurn
from voice.brain.schemas import BrainInput
from voice.brain import main as brain_main


class _FakeProvider:
    name = "fake"

    async def unified_turn(self, *, transcript, language, dialect_hint, history):
        return UnifiedTurn(
            issue_summary="neighbour-harassment",
            urgency=4,
            sentiment="anxious",
            dialect="dharwad",
            verify_phrase="[fake] neighbour-harassment — ಸರಿಯಾ?",
            confidence=0.8,
        )

    async def close(self):
        return None


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    # Replace provider factory so process() never reaches the network.
    monkeypatch.setattr(brain_main, "get_llm_provider", lambda: _FakeProvider())
    monkeypatch.setattr(brain_main, "get_shadow_provider", lambda: None)

    async def fake_confirm(*, transcript, language="kn-IN"):
        from voice.brain.llm import _ConfirmationResult
        return _ConfirmationResult(
            confirmed="houdu" in (transcript or "").lower(), confidence=0.95,
        )

    monkeypatch.setattr(brain_main.llm, "check_confirmation", fake_confirm)
    active_calls.clear()
    yield
    active_calls.clear()


@pytest.mark.asyncio
async def test_pending_returns_verify():
    out = await brain_main.process(BrainInput(
        call_id="c-test-1",
        transcript="ಪಕ್ಕದವರು ತೊಂದರ್ ಕೊಡ್ತಿದಾರ ರೀ",
        language="kn-IN",
    ))
    assert out.action == "verify"
    assert out.fsm_state == "verifying"
    assert out.urgency_1_to_5 == 4


@pytest.mark.asyncio
async def test_houdu_advances_to_handoff():
    cid = "c-test-2"
    await brain_main.process(BrainInput(
        call_id=cid, transcript="harassment", language="kn-IN",
    ))
    out = await brain_main.process(BrainInput(
        call_id=cid, turn_index=1, transcript="houdu", language="kn-IN",
    ))
    assert out.action == "handoff"
    assert out.fsm_state == "verified"
    assert out.issue_summary == "neighbour-harassment"


@pytest.mark.asyncio
async def test_correction_overrides_next_turn():
    cid = "c-test-4"
    await brain_main.process(BrainInput(
        call_id=cid, transcript="harassment", language="kn-IN",
    ))
    await brain_main.correction_received(brain_main.CorrectionInput(
        call_id=cid,
        agent_id="agent-1",
        field="issue_summary",
        old_value="neighbour-harassment",
        new_value="domestic-violence-risk",
    ))
    out = await brain_main.process(BrainInput(
        call_id=cid, turn_index=1, transcript="houdu", language="kn-IN",
    ))
    assert out.issue_summary == "domestic-violence-risk"


@pytest.mark.asyncio
async def test_safety_flag_overrides_immediately():
    out = await brain_main.process(BrainInput(
        call_id="c-test-3",
        transcript="...",
        language="kn-IN",
        safety_flags=["whisper_detected"],
    ))
    assert out.action == "handoff"
    assert out.handoff_reason == "safety_whisper_detected"
    assert out.text == ""
