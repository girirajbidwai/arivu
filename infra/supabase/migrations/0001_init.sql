-- Arivu Dashboard schema. Run on Giriraj's Supabase project's SQL editor.
-- Idempotent; safe to re-run after each schema bump.

-- ── agents ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    role TEXT DEFAULT 'agent',
    created_at TIMESTAMPTZ DEFAULT now()
);

-- ── calls ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    twilio_call_sid TEXT UNIQUE,
    caller_number TEXT,
    dialect TEXT,
    language TEXT,
    issue_summary TEXT,
    urgency INT CHECK (urgency BETWEEN 1 AND 5),
    sentiment TEXT,
    status TEXT DEFAULT 'active',
    agent_id UUID REFERENCES agents(id),
    started_at TIMESTAMPTZ DEFAULT now(),
    ended_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'::jsonb
);

-- ── turns ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS turns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id UUID REFERENCES calls(id) ON DELETE CASCADE,
    turn_index INT NOT NULL,
    speaker TEXT NOT NULL,
    transcript TEXT,
    interpretation TEXT,
    confidence FLOAT,
    dialect TEXT,
    sentiment TEXT,
    audio_url TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS turns_call_idx ON turns (call_id, turn_index);

-- ── corrections (append-only) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS corrections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id UUID REFERENCES calls(id) ON DELETE CASCADE,
    turn_index INT,
    agent_id UUID REFERENCES agents(id),
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS corrections_call_idx ON corrections (call_id);

-- ── handoffs ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS handoffs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id UUID REFERENCES calls(id) ON DELETE CASCADE,
    agent_id UUID REFERENCES agents(id),
    reason TEXT NOT NULL,
    time_to_pickup_ms INT,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS handoffs_call_idx ON handoffs (call_id);

-- ── Realtime publication so the dashboard backend can subscribe ────
-- supabase-py's `channel.on_postgres_changes(...)` uses this.
ALTER PUBLICATION supabase_realtime ADD TABLE turns;
ALTER PUBLICATION supabase_realtime ADD TABLE calls;
ALTER PUBLICATION supabase_realtime ADD TABLE handoffs;

-- ── RLS — agents see only their assigned calls; corrections append-only ──
ALTER TABLE calls ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agents_own_calls ON calls;
CREATE POLICY agents_own_calls ON calls FOR SELECT USING (agent_id = auth.uid());

ALTER TABLE corrections ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agents_insert_corrections ON corrections;
CREATE POLICY agents_insert_corrections ON corrections FOR INSERT WITH CHECK (agent_id = auth.uid());
DROP POLICY IF EXISTS agents_read_corrections ON corrections;
CREATE POLICY agents_read_corrections ON corrections FOR SELECT USING (true);
