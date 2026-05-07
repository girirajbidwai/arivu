// Mirrors shared/schemas/events.json. Hand-maintained for the prototype;
// generate with quicktype if/when the schema grows beyond a screen.

export type Speaker = "citizen" | "agent" | "arivu" | "system";
export type Language = "kn-IN" | "hi-IN" | "en-IN";
export type Dialect = "dharwad" | "mangaluru" | "mysuru" | "bengaluru" | "unknown";
export type Sentiment =
  | "neutral" | "confused" | "anxious"
  | "fearful" | "distressed" | "calm";
export type FSMState = "pending" | "verifying" | "verified" | "handoff";
export type SafetyTrigger =
  | "whisper_detected" | "silent_after_distress" | "third_voice";

export type EventType =
  | "call_started"
  | "turn_started"
  | "transcript_partial"
  | "transcript_final"
  | "dialect_detected"
  | "sentiment_updated"
  | "verification_pending"
  | "verification_confirmed"
  | "verification_failed"
  | "safety_alert"
  | "handoff_initiated"
  | "interpreter_mode_active"
  | "call_ended";

export interface ArivuEvent {
  event_type: EventType;
  call_id: string;
  turn_index?: number;
  timestamp: string;
  data?: {
    speaker?: Speaker;
    transcript?: string;
    language?: Language;
    dialect?: Dialect;
    dialect_confidence?: number;
    sentiment?: Sentiment;
    sentiment_confidence?: number;
    issue_summary?: string;
    urgency?: number;
    confidence?: number;
    verify_phrase?: string;
    safety_trigger?: SafetyTrigger;
    handoff_reason?: string;
    fsm_state?: FSMState;
    reasoning_trace?: string;
  };
}
