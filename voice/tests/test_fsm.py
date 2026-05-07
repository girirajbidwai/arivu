"""
FSM correctness — the single most safety-critical piece of code.

Every transition in the table from `prod.md` and the implicit "safety
override always wins" rule is asserted here. If any of these tests
fail, the system can keep an at-risk citizen on the line. Treat
failures here as a P0.
"""

from voice.brain.fsm import VerificationFSM, MAX_VERIFY_LOOPS


def _new(call_id: str = "c1") -> VerificationFSM:
    return VerificationFSM(call_id=call_id)


def test_initial_state_is_pending():
    assert _new().state == "pending"


def test_pending_to_verifying_on_verify_sent():
    fsm = _new()
    fsm.transition("verify_sent")
    assert fsm.state == "verifying"
    assert fsm.verify_count == 1


def test_verifying_to_verified_on_confirmed():
    fsm = _new()
    fsm.transition("verify_sent")
    fsm.transition("confirmed")
    assert fsm.state == "verified"


def test_re_verify_increments_count():
    fsm = _new()
    fsm.transition("verify_sent")        # count=1
    fsm.transition("re_verify")           # count=2
    assert fsm.state == "verifying"
    assert fsm.verify_count == 2


def test_re_verify_past_limit_forces_handoff():
    fsm = _new()
    fsm.transition("verify_sent")           # count=1
    for _ in range(MAX_VERIFY_LOOPS + 1):
        fsm.transition("re_verify")
    assert fsm.state == "handoff"
    assert fsm.handoff_reason == "max_verify_loops"


def test_safety_override_from_pending():
    fsm = _new()
    fsm.force_handoff(reason="safety_whisper_detected")
    assert fsm.state == "handoff"
    assert fsm.handoff_reason == "safety_whisper_detected"


def test_safety_override_from_verifying():
    fsm = _new()
    fsm.transition("verify_sent")
    fsm.force_handoff(reason="safety_third_voice")
    assert fsm.state == "handoff"


def test_safety_override_from_verified_still_handoff():
    fsm = _new()
    fsm.transition("verify_sent")
    fsm.transition("confirmed")
    fsm.force_handoff(reason="agent_request")
    assert fsm.state == "handoff"


def test_handoff_is_terminal():
    fsm = _new()
    fsm.force_handoff(reason="x")
    fsm.transition("verify_sent")
    fsm.transition("confirmed")
    assert fsm.state == "handoff"
    assert fsm.is_terminal()


def test_parse_failed_forces_handoff():
    fsm = _new()
    fsm.transition("verify_sent")
    fsm.transition("parse_failed")
    assert fsm.state == "handoff"


def test_corrections_accumulate():
    fsm = _new()
    fsm.add_correction("issue_summary", "Domestic violence — neighbour")
    fsm.add_correction("urgency", "5")
    assert fsm.corrections["issue_summary"] == "Domestic violence — neighbour"
    assert fsm.corrections["urgency"] == "5"


def test_history_records_every_transition():
    fsm = _new()
    fsm.transition("verify_sent")
    fsm.transition("confirmed")
    assert fsm.history == [
        ("pending", "verify_sent", "verifying"),
        ("verifying", "confirmed", "verified"),
    ]
