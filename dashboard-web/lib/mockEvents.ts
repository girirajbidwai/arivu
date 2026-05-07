// A scripted sequence of events that simulates a real call. Used by
// the dashboard's offline mode and as a demo fallback if Twilio is dead.

import type { ArivuEvent } from "./types";

export function scriptedCall(callId: string): ArivuEvent[] {
  const t0 = Date.now();
  const at = (ms: number) => new Date(t0 + ms).toISOString();

  return [
    { event_type: "call_started", call_id: callId, timestamp: at(0),
      data: { speaker: "system", language: "kn-IN" } },
    { event_type: "turn_started", call_id: callId, turn_index: 1, timestamp: at(400),
      data: { speaker: "citizen" } },
    { event_type: "transcript_partial", call_id: callId, turn_index: 1, timestamp: at(900),
      data: { speaker: "citizen", transcript: "ನಮ್ಮ ಪಕ್ಕದವರು…", language: "kn-IN" } },
    { event_type: "transcript_final", call_id: callId, turn_index: 1, timestamp: at(2100),
      data: { speaker: "citizen", transcript: "ನಮ್ಮ ಪಕ್ಕದವರು ತೊಂದರ್ ಕೊಡ್ತಿದಾರ ರೀ", language: "kn-IN" } },
    { event_type: "dialect_detected", call_id: callId, turn_index: 1, timestamp: at(2200),
      data: { dialect: "dharwad", dialect_confidence: 0.78 } },
    { event_type: "sentiment_updated", call_id: callId, turn_index: 1, timestamp: at(2300),
      data: { sentiment: "fearful", sentiment_confidence: 0.81 } },
    { event_type: "verification_pending", call_id: callId, turn_index: 1, timestamp: at(2900),
      data: {
        verify_phrase: "ನಿಮ್ಮ ಪಕ್ಕದವರು ತೊಂದರ್ ಕೊಡ್ತಿದಾರ ಅಂತ ಹೇಳ್ತಿದೀರಾ ರೀ?",
        issue_summary: "Neighbour is harassing the caller",
        urgency: 4,
        fsm_state: "verifying",
      } },
    { event_type: "transcript_final", call_id: callId, turn_index: 2, timestamp: at(5400),
      data: { speaker: "citizen", transcript: "ಹೌದು ರೀ", language: "kn-IN" } },
    { event_type: "verification_confirmed", call_id: callId, turn_index: 2, timestamp: at(5800),
      data: {
        issue_summary: "Neighbour is harassing the caller",
        urgency: 4, confidence: 0.92, fsm_state: "verified",
      } },
    { event_type: "handoff_initiated", call_id: callId, turn_index: 2, timestamp: at(6200),
      data: { handoff_reason: "verification_complete" } },
  ];
}

export function startMockCall(
  callId: string,
  onEvent: (e: ArivuEvent) => void,
  speed = 1,
) {
  const events = scriptedCall(callId);
  const startTs = Date.parse(events[0].timestamp);
  events.forEach((ev) => {
    const delay = Math.max(0, (Date.parse(ev.timestamp) - startTs) / speed);
    setTimeout(() => onEvent(ev), delay);
  });
}
