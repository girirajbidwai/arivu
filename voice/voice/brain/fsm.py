"""
VerificationFSM — the safety net.

Three nominal states (pending → verifying → verified) plus a terminal
handoff state. Maximum two verification loops. Any safety event or
parser failure forces an immediate handoff. Boring on purpose.

The FSM is the only piece of logic that decides whether the AI keeps
talking to the citizen or hands off to a human. It must be deterministic.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Literal, Optional

logger = logging.getLogger("arivu.brain.fsm")

State = Literal["pending", "verifying", "verified", "handoff"]
Event = Literal[
    "verify_sent",       # we asked the citizen to confirm
    "confirmed",         # citizen confirmed
    "denied",            # citizen denied / re-clarified
    "re_verify",         # generated a second attempt
    "safety_override",   # whisper / silence / 3rd-voice etc.
    "parse_failed",      # LLM gave malformed JSON
    "max_loops",         # too many re-verify attempts
    "manual_handoff",    # operator explicitly forced handoff
    "timeout",           # caller went silent past threshold
]


MAX_VERIFY_LOOPS = 2


@dataclass
class VerificationFSM:
    call_id: str
    state: State = "pending"
    verify_count: int = 0
    handoff_reason: Optional[str] = None
    corrections: dict[str, str] = field(default_factory=dict)
    pending_interpretation: dict[str, object] = field(default_factory=dict)
    verified_interpretation: dict[str, object] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_transition_at: float = field(default_factory=time.time)
    history: list[tuple[State, Event, State]] = field(default_factory=list)

    # ── Public API ──────────────────────────────────────────────────────

    def transition(self, event: Event) -> State:
        """
        Apply an event to the FSM. Returns the new state. Records every
        transition in `history` for debug + audit. Any unrecognised event
        in any state is a no-op — callers must not rely on side effects.
        """
        prev = self.state
        next_state = self._next(self.state, event)

        if next_state != prev:
            self.last_transition_at = time.time()
            logger.info(
                "fsm[%s] %s --%s--> %s (verify_count=%d)",
                self.call_id, prev, event, next_state, self.verify_count,
            )
        self.history.append((prev, event, next_state))
        self.state = next_state
        return next_state

    def force_handoff(self, reason: str) -> None:
        """Hard override — used by safety triggers and parse failures."""
        self.handoff_reason = reason
        self.transition("safety_override")

    def add_correction(self, field_name: str, new_value: str) -> None:
        """
        Record an agent-side correction so the next Brain response can
        use the corrected interpretation in its prompt.
        """
        self.corrections[field_name] = new_value
        logger.info("fsm[%s] correction %s=%r", self.call_id, field_name, new_value)

    def set_pending_interpretation(
        self,
        *,
        issue_summary: str,
        urgency: int,
        sentiment: str,
        confidence: float,
        dialect: str,
    ) -> None:
        """
        Cache the interpretation we are asking the citizen to confirm.
        The confirmation turn itself is often just "yes / no", so the
        verified handoff must come from this snapshot, not a re-parse of
        the confirmation utterance.
        """
        self.pending_interpretation = {
            "issue_summary": issue_summary,
            "urgency": urgency,
            "sentiment": sentiment,
            "confidence": confidence,
            "dialect": dialect,
        }

    def mark_verified(self) -> None:
        """Freeze the currently pending interpretation as the verified one."""
        if self.pending_interpretation:
            self.verified_interpretation = dict(self.pending_interpretation)

    def is_terminal(self) -> bool:
        return self.state == "handoff"

    # ── Internal transition table ───────────────────────────────────────

    def _next(self, state: State, event: Event) -> State:
        # Safety overrides always win.
        if event in ("safety_override", "parse_failed", "manual_handoff", "timeout"):
            if self.handoff_reason is None:
                self.handoff_reason = event
            return "handoff"

        if state == "pending":
            if event == "verify_sent":
                self.verify_count = 1
                return "verifying"

        elif state == "verifying":
            if event == "confirmed":
                return "verified"
            if event == "denied":
                # Stay verifying; caller must follow with re_verify or max_loops.
                return "verifying"
            if event == "re_verify":
                self.verify_count += 1
                if self.verify_count > MAX_VERIFY_LOOPS:
                    self.handoff_reason = "max_verify_loops"
                    return "handoff"
                return "verifying"
            if event == "max_loops":
                self.handoff_reason = "max_verify_loops"
                return "handoff"

        elif state == "verified":
            # Once verified the only forward motion is interpreter mode
            # or operator-initiated handoff (already covered above).
            return state

        # handoff is terminal — every other event is a no-op
        return state
