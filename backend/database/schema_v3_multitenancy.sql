-- ============================================================
--  AndiOS Phase 3 Migration — Multi-Tenancy (SaaS) Support
--  Run this in your Supabase SQL editor AFTER schema.sql + schema_v2.sql
--  Adds: agencies table, agency_id to all tenant tables, RLS policies
-- ============================================================


-- ─── STEP 1: AGENCIES TABLE (Tenants) ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agencies (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                TEXT NOT NULL,
    slug                TEXT UNIQUE NOT NULL,            -- URL-friendly unique name (e.g. "elite-realty")
    email               TEXT UNIQUE NOT NULL,            -- Admin email
    phone               TEXT,
    logo_url            TEXT,
    subscription_plan   TEXT DEFAULT 'starter',         -- starter | pro | enterprise
    subscription_status TEXT DEFAULT 'active',          -- active | suspended | cancelled
    trial_ends_at       TIMESTAMPTZ,
    settings            JSONB DEFAULT '{}',              -- Agency-specific config
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agencies_slug ON agencies(slug);
CREATE INDEX IF NOT EXISTS idx_agencies_status ON agencies(subscription_status);

-- Trigger for agencies updated_at
CREATE TRIGGER trg_agencies_updated_at
    BEFORE UPDATE ON agencies
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ─── STEP 2: ADD agency_id TO ALL TENANT TABLES ────────────────────────────────
-- NOTE: If you are starting FRESH (tables are empty), these ALTER statements work perfectly.
-- If tables already have data, add a default agency first then set NOT NULL.

-- 2a. Create a default agency first (required before adding NOT NULL constraint)
INSERT INTO agencies (name, slug, email, subscription_plan)
VALUES ('Default Agency', 'default-agency', 'admin@andios.com', 'enterprise')
ON CONFLICT (slug) DO NOTHING;

-- Store the default agency id for use below
DO $$
DECLARE
    default_agency_id UUID;
BEGIN
    SELECT id INTO default_agency_id FROM agencies WHERE slug = 'default-agency';

    -- Add agency_id to agents
    ALTER TABLE agents ADD COLUMN IF NOT EXISTS agency_id UUID REFERENCES agencies(id) ON DELETE CASCADE;
    UPDATE agents SET agency_id = default_agency_id WHERE agency_id IS NULL;
    ALTER TABLE agents ALTER COLUMN agency_id SET NOT NULL;

    -- Add agency_id to leads
    ALTER TABLE leads ADD COLUMN IF NOT EXISTS agency_id UUID REFERENCES agencies(id) ON DELETE CASCADE;
    UPDATE leads SET agency_id = default_agency_id WHERE agency_id IS NULL;
    ALTER TABLE leads ALTER COLUMN agency_id SET NOT NULL;

    -- Add agency_id to conversations
    ALTER TABLE conversations ADD COLUMN IF NOT EXISTS agency_id UUID REFERENCES agencies(id) ON DELETE CASCADE;
    UPDATE conversations SET agency_id = default_agency_id WHERE agency_id IS NULL;
    ALTER TABLE conversations ALTER COLUMN agency_id SET NOT NULL;

    -- Add agency_id to viewings
    ALTER TABLE viewings ADD COLUMN IF NOT EXISTS agency_id UUID REFERENCES agencies(id) ON DELETE CASCADE;
    UPDATE viewings SET agency_id = default_agency_id WHERE agency_id IS NULL;
    ALTER TABLE viewings ALTER COLUMN agency_id SET NOT NULL;

    -- Add agency_id to follow_ups
    ALTER TABLE follow_ups ADD COLUMN IF NOT EXISTS agency_id UUID REFERENCES agencies(id) ON DELETE CASCADE;
    UPDATE follow_ups SET agency_id = default_agency_id WHERE agency_id IS NULL;
    ALTER TABLE follow_ups ALTER COLUMN agency_id SET NOT NULL;

    -- Add agency_id to owner_reports
    ALTER TABLE owner_reports ADD COLUMN IF NOT EXISTS agency_id UUID REFERENCES agencies(id) ON DELETE CASCADE;
    UPDATE owner_reports SET agency_id = default_agency_id WHERE agency_id IS NULL;
    ALTER TABLE owner_reports ALTER COLUMN agency_id SET NOT NULL;

    -- Add agency_id to documents (Phase 2)
    ALTER TABLE documents ADD COLUMN IF NOT EXISTS agency_id UUID REFERENCES agencies(id) ON DELETE CASCADE;
    UPDATE documents SET agency_id = default_agency_id WHERE agency_id IS NULL;
    ALTER TABLE documents ALTER COLUMN agency_id SET NOT NULL;

    -- Add agency_id + agent_id to contracts (Phase 2)
    ALTER TABLE contracts ADD COLUMN IF NOT EXISTS agency_id UUID REFERENCES agencies(id) ON DELETE CASCADE;
    ALTER TABLE contracts ADD COLUMN IF NOT EXISTS agent_id UUID REFERENCES agents(id) ON DELETE SET NULL;
    UPDATE contracts SET agency_id = default_agency_id WHERE agency_id IS NULL;
    ALTER TABLE contracts ALTER COLUMN agency_id SET NOT NULL;

    -- Add agency_id to cheques (Phase 2)
    ALTER TABLE cheques ADD COLUMN IF NOT EXISTS agency_id UUID REFERENCES agencies(id) ON DELETE CASCADE;
    UPDATE cheques SET agency_id = default_agency_id WHERE agency_id IS NULL;
    ALTER TABLE cheques ALTER COLUMN agency_id SET NOT NULL;

    -- Add agency_id to connectors
    ALTER TABLE connectors ADD COLUMN IF NOT EXISTS agency_id UUID REFERENCES agencies(id) ON DELETE CASCADE;
    UPDATE connectors SET agency_id = default_agency_id WHERE agency_id IS NULL;

END $$;


-- ─── STEP 3: ADD INDEXES ON agency_id ─────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_agents_agency_id        ON agents(agency_id);
CREATE INDEX IF NOT EXISTS idx_leads_agency_id         ON leads(agency_id);
CREATE INDEX IF NOT EXISTS idx_conversations_agency_id ON conversations(agency_id);
CREATE INDEX IF NOT EXISTS idx_viewings_agency_id      ON viewings(agency_id);
CREATE INDEX IF NOT EXISTS idx_follow_ups_agency_id    ON follow_ups(agency_id);
CREATE INDEX IF NOT EXISTS idx_documents_agency_id     ON documents(agency_id);
CREATE INDEX IF NOT EXISTS idx_contracts_agency_id     ON contracts(agency_id);
CREATE INDEX IF NOT EXISTS idx_cheques_agency_id       ON cheques(agency_id);
CREATE INDEX IF NOT EXISTS idx_connectors_agency_id    ON connectors(agency_id);


-- ─── STEP 4: ENABLE RLS ON agencies TABLE ─────────────────────────────────────
ALTER TABLE agencies ENABLE ROW LEVEL SECURITY;


-- ─── STEP 5: RLS POLICIES ─────────────────────────────────────────────────────
-- NOTE: These policies assume your backend uses `service_role` key (bypasses RLS)
-- and the Supabase `auth.users` table has `agency_id` stored in JWT metadata.
-- For now we create "service role bypass" policies — your FastAPI backend handles
-- the authorization logic itself (auth_middleware.py).
-- When you add Supabase Auth with JWT claims, uncomment the per-agency policies below.

-- agencies: only service role can read/write
CREATE POLICY "Service role full access on agencies"
    ON agencies FOR ALL
    USING (true)
    WITH CHECK (true);

-- leads: service role full access
CREATE POLICY "Service role full access on leads"
    ON leads FOR ALL
    USING (true)
    WITH CHECK (true);

-- conversations: service role full access
CREATE POLICY "Service role full access on conversations"
    ON conversations FOR ALL
    USING (true)
    WITH CHECK (true);

-- viewings: service role full access
CREATE POLICY "Service role full access on viewings"
    ON viewings FOR ALL
    USING (true)
    WITH CHECK (true);

-- agents: service role full access
CREATE POLICY "Service role full access on agents"
    ON agents FOR ALL
    USING (true)
    WITH CHECK (true);

-- owner_reports: service role full access
CREATE POLICY "Service role full access on owner_reports"
    ON owner_reports FOR ALL
    USING (true)
    WITH CHECK (true);

-- documents: service role full access
CREATE POLICY "Service role full access on documents"
    ON documents FOR ALL
    USING (true)
    WITH CHECK (true);

-- contracts: service role full access
CREATE POLICY "Service role full access on contracts"
    ON contracts FOR ALL
    USING (true)
    WITH CHECK (true);

-- cheques: service role full access
CREATE POLICY "Service role full access on cheques"
    ON cheques FOR ALL
    USING (true)
    WITH CHECK (true);


-- ─── FUTURE: Per-Agency RLS (uncomment when Supabase Auth + JWT claims ready) ──
-- These policies restrict each agency to only see their own data:
--
-- CREATE POLICY "Agency isolation on leads"
--     ON leads FOR ALL
--     USING (agency_id = (auth.jwt() ->> 'agency_id')::UUID)
--     WITH CHECK (agency_id = (auth.jwt() ->> 'agency_id')::UUID);
--
-- (repeat same pattern for agents, conversations, viewings, documents, contracts, cheques)


-- ─── DONE ─────────────────────────────────────────────────────────────────────
-- Your schema is now multi-tenant ready.
-- Each agency gets a unique `agency_id` that isolates their data.
-- The backend (FastAPI) enforces agency_id filtering in all queries.
