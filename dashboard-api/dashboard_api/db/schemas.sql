-- Arivu Dashboard schema.
-- Run this in Supabase SQL Editor on the shared project.
-- Safe to re-run.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── agents ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    role TEXT DEFAULT 'agent',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Browser-demo seed agent. The frontend uses this UUID for override and
-- manual handoff actions before full auth assignment is wired.
INSERT INTO public.agents (id, email, name, role)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'demo.agent@arivu.local',
    'Arivu Demo Agent',
    'agent'
)
ON CONFLICT (id) DO UPDATE
SET
    email = EXCLUDED.email,
    name = EXCLUDED.name,
    role = EXCLUDED.role;

-- ── calls ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    twilio_call_sid TEXT UNIQUE,
    caller_number TEXT,
    dialect TEXT,
    language TEXT,
    issue_summary TEXT,
    urgency INT CHECK (urgency BETWEEN 1 AND 5),
    sentiment TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    agent_id UUID REFERENCES public.agents(id),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS calls_status_started_idx
    ON public.calls (status, started_at DESC);
CREATE INDEX IF NOT EXISTS calls_urgency_started_idx
    ON public.calls (urgency DESC, started_at DESC);

-- ── turns ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.turns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id UUID NOT NULL REFERENCES public.calls(id) ON DELETE CASCADE,
    turn_index INT NOT NULL,
    speaker TEXT NOT NULL,
    transcript TEXT,
    interpretation TEXT,
    confidence DOUBLE PRECISION,
    dialect TEXT,
    sentiment TEXT,
    audio_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS turns_call_idx
    ON public.turns (call_id, turn_index);
CREATE UNIQUE INDEX IF NOT EXISTS turns_call_turn_unique_idx
    ON public.turns (call_id, turn_index, speaker, created_at);

-- ── corrections (append-only) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.corrections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id UUID NOT NULL REFERENCES public.calls(id) ON DELETE CASCADE,
    turn_index INT,
    agent_id UUID REFERENCES public.agents(id),
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS corrections_call_idx
    ON public.corrections (call_id, created_at DESC);

-- ── handoffs ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.handoffs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id UUID NOT NULL REFERENCES public.calls(id) ON DELETE CASCADE,
    agent_id UUID REFERENCES public.agents(id),
    reason TEXT NOT NULL,
    time_to_pickup_ms INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS handoffs_call_idx
    ON public.handoffs (call_id, created_at DESC);

-- ── Realtime publication so the dashboard backend can subscribe ────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_publication_tables
        WHERE pubname = 'supabase_realtime'
          AND schemaname = 'public'
          AND tablename = 'turns'
    ) THEN
        ALTER PUBLICATION supabase_realtime ADD TABLE public.turns;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_publication_tables
        WHERE pubname = 'supabase_realtime'
          AND schemaname = 'public'
          AND tablename = 'calls'
    ) THEN
        ALTER PUBLICATION supabase_realtime ADD TABLE public.calls;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_publication_tables
        WHERE pubname = 'supabase_realtime'
          AND schemaname = 'public'
          AND tablename = 'handoffs'
    ) THEN
        ALTER PUBLICATION supabase_realtime ADD TABLE public.handoffs;
    END IF;
END $$;

ALTER TABLE public.calls REPLICA IDENTITY FULL;
ALTER TABLE public.turns REPLICA IDENTITY FULL;
ALTER TABLE public.handoffs REPLICA IDENTITY FULL;

-- ── RLS: permissive for hackathon demo, service role remains primary ──
ALTER TABLE public.calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.turns ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.corrections ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.handoffs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agents ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS calls_read_authenticated ON public.calls;
CREATE POLICY calls_read_authenticated
    ON public.calls
    FOR SELECT
    TO authenticated
    USING (true);

DROP POLICY IF EXISTS turns_read_authenticated ON public.turns;
CREATE POLICY turns_read_authenticated
    ON public.turns
    FOR SELECT
    TO authenticated
    USING (true);

DROP POLICY IF EXISTS handoffs_read_authenticated ON public.handoffs;
CREATE POLICY handoffs_read_authenticated
    ON public.handoffs
    FOR SELECT
    TO authenticated
    USING (true);

DROP POLICY IF EXISTS corrections_read_authenticated ON public.corrections;
CREATE POLICY corrections_read_authenticated
    ON public.corrections
    FOR SELECT
    TO authenticated
    USING (true);

DROP POLICY IF EXISTS corrections_insert_authenticated ON public.corrections;
CREATE POLICY corrections_insert_authenticated
    ON public.corrections
    FOR INSERT
    TO authenticated
    WITH CHECK (true);

DROP POLICY IF EXISTS agents_read_authenticated ON public.agents;
CREATE POLICY agents_read_authenticated
    ON public.agents
    FOR SELECT
    TO authenticated
    USING (true);
