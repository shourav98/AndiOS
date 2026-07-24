-- ============================================================
--  AndiOS Phase 1 — Supabase Database Schema
--  Run this in your Supabase SQL editor
-- ============================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ─── AGENTS / TEAM ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agents (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                TEXT NOT NULL,
    phone               TEXT UNIQUE,
    email               TEXT UNIQUE NOT NULL,
    role                TEXT NOT NULL DEFAULT 'agent',   -- agent | senior_agent | manager | owner
    calendar_id         TEXT,                            -- Google Calendar ID (per-agent mode)
    whatsapp_number     TEXT,
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ─── LEADS ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS leads (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    external_lead_id    TEXT UNIQUE,                     -- PF / Bayut / Dubizzle lead ID (dedup key)
    name                TEXT NOT NULL,
    phone               TEXT NOT NULL,
    email               TEXT,
    source              TEXT NOT NULL DEFAULT 'property_finder',  -- property_finder | bayut | dubizzle | direct | referral
    property_ref        TEXT,                            -- listing reference from portal
    property_address    TEXT,
    bedrooms            INTEGER,
    budget_min          NUMERIC,
    budget_max          NUMERIC,
    currency            TEXT DEFAULT 'AED',
    location_pref       TEXT,
    purpose             TEXT DEFAULT 'rent',             -- rent | buy
    status              TEXT DEFAULT 'new',              -- new | qualifying | viewing_booked | viewing_done | negotiating | closed | lost | handover
    ai_stage            TEXT DEFAULT 'greeting',         -- greeting | qualifying | slot_offering | confirmed | handover | done
    assigned_agent_id   UUID REFERENCES agents(id) ON DELETE SET NULL,
    is_ai_handling      BOOLEAN DEFAULT TRUE,
    handover_reason     TEXT,
    qualification_score INTEGER,                         -- 0-100 AI qualification score
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(phone);
CREATE INDEX IF NOT EXISTS idx_leads_source ON leads(source);
CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_external_id ON leads(external_lead_id);

-- ─── CONVERSATIONS ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conversations (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lead_id             UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    direction           TEXT NOT NULL,                   -- inbound | outbound
    channel             TEXT NOT NULL DEFAULT 'whatsapp', -- whatsapp | email | call
    message_body        TEXT NOT NULL,
    sender_type         TEXT NOT NULL,                   -- ai | agent | lead
    sender_id           UUID,                            -- agent ID if sender_type=agent
    whatsapp_message_id TEXT,                            -- provider message ID
    is_read             BOOLEAN DEFAULT FALSE,
    timestamp           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_lead_id ON conversations(lead_id);
CREATE INDEX IF NOT EXISTS idx_conversations_timestamp ON conversations(timestamp DESC);

-- ─── VIEWINGS ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS viewings (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lead_id             UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    agent_id            UUID REFERENCES agents(id) ON DELETE SET NULL,
    property_address    TEXT NOT NULL,
    property_ref        TEXT,
    viewing_datetime    TIMESTAMPTZ NOT NULL,
    duration_minutes    INTEGER DEFAULT 60,
    status              TEXT DEFAULT 'scheduled',        -- scheduled | confirmed | completed | cancelled | no_show
    google_event_id     TEXT,                            -- Google Calendar event ID
    google_meet_link    TEXT,
    reminder_24h_sent   BOOLEAN DEFAULT FALSE,
    reminder_2h_sent    BOOLEAN DEFAULT FALSE,
    feedback_requested  BOOLEAN DEFAULT FALSE,
    feedback_received   TEXT,
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_viewings_datetime ON viewings(viewing_datetime);
CREATE INDEX IF NOT EXISTS idx_viewings_agent_id ON viewings(agent_id);
CREATE INDEX IF NOT EXISTS idx_viewings_lead_id ON viewings(lead_id);

-- ─── FOLLOW-UPS ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS follow_ups (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lead_id             UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    viewing_id          UUID REFERENCES viewings(id) ON DELETE SET NULL,
    type                TEXT NOT NULL,                   -- reminder_24h | reminder_2h | post_viewing | feedback | re_engagement
    scheduled_at        TIMESTAMPTZ NOT NULL,
    sent_at             TIMESTAMPTZ,
    content             TEXT,
    status              TEXT DEFAULT 'pending',          -- pending | sent | failed | skipped
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_follow_ups_scheduled_at ON follow_ups(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_follow_ups_status ON follow_ups(status);

-- ─── OWNER REPORTS ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS owner_reports (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    generated_at        TIMESTAMPTZ DEFAULT NOW(),
    period_start        TIMESTAMPTZ NOT NULL,
    period_end          TIMESTAMPTZ NOT NULL,
    total_leads         INTEGER DEFAULT 0,
    new_leads           INTEGER DEFAULT 0,
    qualified_leads     INTEGER DEFAULT 0,
    viewings_booked     INTEGER DEFAULT 0,
    viewings_completed  INTEGER DEFAULT 0,
    closed_deals        INTEGER DEFAULT 0,
    lost_leads          INTEGER DEFAULT 0,
    avg_response_time_s INTEGER,                         -- seconds
    lead_to_viewing_pct NUMERIC,
    viewing_to_close_pct NUMERIC,
    total_revenue_aed   NUMERIC DEFAULT 0,
    ai_narrative        TEXT,                            -- GPT-generated summary
    report_json         JSONB,                           -- full structured data
    generated_by        UUID REFERENCES agents(id) ON DELETE SET NULL
);

-- ─── WEBHOOK LOGS ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS webhook_logs (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source              TEXT NOT NULL,                   -- property_finder | whatsapp | bayut
    payload             JSONB NOT NULL,
    received_at         TIMESTAMPTZ DEFAULT NOW(),
    processed           BOOLEAN DEFAULT FALSE,
    lead_id             UUID REFERENCES leads(id) ON DELETE SET NULL,
    error               TEXT,
    processing_time_ms  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_webhook_logs_received_at ON webhook_logs(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_webhook_logs_source ON webhook_logs(source);

-- ─── CONNECTORS ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS connectors (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                TEXT NOT NULL UNIQUE,            -- property_finder | whatsapp | google_calendar | bayut
    is_connected        BOOLEAN DEFAULT FALSE,
    auth_data           JSONB,                           -- encrypted tokens (stored securely)
    last_sync           TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Seed default connectors
INSERT INTO connectors (name, is_connected) VALUES
    ('property_finder', FALSE),
    ('whatsapp', FALSE),
    ('google_calendar', FALSE),
    ('bayut', FALSE),
    ('dubizzle', FALSE)
ON CONFLICT (name) DO NOTHING;

-- ─── UPDATED_AT TRIGGERS ───────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_leads_updated_at
    BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_viewings_updated_at
    BEFORE UPDATE ON viewings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_agents_updated_at
    BEFORE UPDATE ON agents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ─── ROW LEVEL SECURITY (RLS) ──────────────────────────────────────────────────
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE viewings ENABLE ROW LEVEL SECURITY;
ALTER TABLE agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE owner_reports ENABLE ROW LEVEL SECURITY;

-- Service role bypasses RLS (used by backend)
-- Anon key restricted — handled by FastAPI auth middleware
