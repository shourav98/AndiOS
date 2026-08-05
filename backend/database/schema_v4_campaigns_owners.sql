-- ============================================================
--  AndiOS Phase 4 Migration — Call Campaigns & Owners Database
--  Run this in your Supabase SQL editor AFTER schema_v3_multitenancy.sql
-- ============================================================

-- ─── ADD BRANCH TO AGENTS ─────────────────────────────────────────────────────
-- Add a branch column to agents table for team organization
ALTER TABLE agents ADD COLUMN IF NOT EXISTS branch TEXT;

-- ─── UPDATE CONTRACTS TABLE ───────────────────────────────────────────────────
-- Add missing fields for the frontend multi-step contract creation
ALTER TABLE contracts 
    ADD COLUMN IF NOT EXISTS property_unit TEXT,
    ADD COLUMN IF NOT EXISTS area_community TEXT,
    ADD COLUMN IF NOT EXISTS rent_words TEXT,
    ADD COLUMN IF NOT EXISTS number_of_cheques INTEGER,
    ADD COLUMN IF NOT EXISTS broker_fee NUMERIC,
    ADD COLUMN IF NOT EXISTS owner_name TEXT,
    ADD COLUMN IF NOT EXISTS owner_phone TEXT,
    ADD COLUMN IF NOT EXISTS owner_email TEXT,
    ADD COLUMN IF NOT EXISTS owner_emirates_id TEXT,
    ADD COLUMN IF NOT EXISTS tenant_name TEXT,
    ADD COLUMN IF NOT EXISTS tenant_phone TEXT,
    ADD COLUMN IF NOT EXISTS tenant_email TEXT,
    ADD COLUMN IF NOT EXISTS tenant_emirates_id TEXT;
    
-- Make some fields optional that were required before
ALTER TABLE contracts ALTER COLUMN property_address DROP NOT NULL;
ALTER TABLE contracts ALTER COLUMN rent_amount DROP NOT NULL;
ALTER TABLE contracts ALTER COLUMN start_date DROP NOT NULL;
ALTER TABLE contracts ALTER COLUMN end_date DROP NOT NULL;
ALTER TABLE contracts ALTER COLUMN lead_id DROP NOT NULL;

-- ─── OWNERS DATABASE ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS owners (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agency_id           UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    phone               TEXT NOT NULL,
    email               TEXT,
    property_group      TEXT,                            -- e.g., "Marina owners", "Downtown owners"
    property_unit       TEXT,                            -- e.g., "Apt 1204, Marina Gate 2"
    call_status         TEXT DEFAULT 'Not called',       -- 'Not called' | 'Called' | 'Do not call'
    avatar_url          TEXT,
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_owners_agency_id ON owners(agency_id);
CREATE INDEX IF NOT EXISTS idx_owners_property_group ON owners(property_group);

CREATE TRIGGER trg_owners_updated_at
    BEFORE UPDATE ON owners
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

ALTER TABLE owners ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service role full access on owners" ON owners FOR ALL USING (true) WITH CHECK (true);


-- ─── CALL CAMPAIGNS ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS call_campaigns (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agency_id           UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    campaign_name       TEXT NOT NULL,
    campaign_subtitle   TEXT,
    target_group        TEXT NOT NULL,                   -- e.g., "Marina owners"
    from_time           TIME,                            -- Calling hours start
    to_time             TIME,                            -- Calling hours end
    status              TEXT DEFAULT 'Scheduled',        -- 'Scheduled' | 'Running' | 'Completed'
    
    -- Cached metrics (updated periodically or via triggers/backend)
    total_owners        INTEGER DEFAULT 0,
    answered            INTEGER DEFAULT 0,
    calls_to_listings   INTEGER DEFAULT 0,
    
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_call_campaigns_agency_id ON call_campaigns(agency_id);
CREATE INDEX IF NOT EXISTS idx_call_campaigns_status ON call_campaigns(status);

CREATE TRIGGER trg_call_campaigns_updated_at
    BEFORE UPDATE ON call_campaigns
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

ALTER TABLE call_campaigns ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service role full access on call_campaigns" ON call_campaigns FOR ALL USING (true) WITH CHECK (true);


-- ─── CALLS (Call Logs) ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS calls (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id         UUID REFERENCES call_campaigns(id) ON DELETE CASCADE,
    agency_id           UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    owner_id            UUID REFERENCES owners(id) ON DELETE SET NULL,
    
    -- Denormalized owner data for quick access or if owner is deleted
    owner_name          TEXT,
    owner_role          TEXT DEFAULT 'Owner',
    property_location   TEXT,
    
    call_time           TIMESTAMPTZ DEFAULT NOW(),
    duration_seconds    INTEGER DEFAULT 0,               -- Store in seconds, format in UI
    status              TEXT DEFAULT 'No answer',        -- 'Listing won' | 'Callback booked' | 'Not interested' | 'No answer' | 'Do not call'
    status_value        TEXT DEFAULT 'no-answer',        -- 'listing-won' | 'callback-booked' | 'not-interested' | 'no-answer' | 'do-not-call'
    
    audio_url           TEXT,                            -- Link to call recording
    transcript          TEXT,                            -- Call transcript text
    summary             TEXT,                            -- AI generated summary
    
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_calls_agency_id ON calls(agency_id);
CREATE INDEX IF NOT EXISTS idx_calls_campaign_id ON calls(campaign_id);
CREATE INDEX IF NOT EXISTS idx_calls_status ON calls(status_value);

ALTER TABLE calls ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service role full access on calls" ON calls FOR ALL USING (true) WITH CHECK (true);
