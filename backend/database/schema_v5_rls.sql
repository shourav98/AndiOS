-- ============================================================
--  AndiOS Phase 5 Migration — Production RLS Policies
--  Run AFTER: schema.sql → schema_v2.sql → schema_v3_multitenancy.sql → schema_v4_campaigns_owners.sql
--
--  Table names aligned with codebase:
--    agents, contracts, owners, calls (NOT users, tenancy_contracts, property_owners, call_records)
--
--  JWT claims (set via auth.py on register/login):
--    app_metadata.agency_id, app_metadata.role, app_metadata.agent_id
-- ============================================================


-- ─── HELPER FUNCTIONS ─────────────────────────────────────────────────────────
-- Extract tenant context from Supabase JWT app_metadata

CREATE OR REPLACE FUNCTION auth_agency_id()
RETURNS UUID
LANGUAGE sql STABLE
AS $$
  SELECT NULLIF(auth.jwt() -> 'app_metadata' ->> 'agency_id', '')::UUID;
$$;

CREATE OR REPLACE FUNCTION auth_user_role()
RETURNS TEXT
LANGUAGE sql STABLE
AS $$
  SELECT COALESCE(auth.jwt() -> 'app_metadata' ->> 'role', '');
$$;

CREATE OR REPLACE FUNCTION auth_agent_id()
RETURNS UUID
LANGUAGE sql STABLE
AS $$
  SELECT NULLIF(auth.jwt() -> 'app_metadata' ->> 'agent_id', '')::UUID;
$$;

CREATE OR REPLACE FUNCTION is_management()
RETURNS BOOLEAN
LANGUAGE sql STABLE
AS $$
  SELECT auth_user_role() IN ('owner', 'manager');
$$;


-- ─── DROP OLD PERMISSIVE POLICIES (schema_v3 USING(true)) ───────────────────
-- Safe to run even if some policies don't exist

DO $$
DECLARE
    pol RECORD;
BEGIN
    FOR pol IN
        SELECT schemaname, tablename, policyname
        FROM pg_policies
        WHERE schemaname = 'public'
          AND policyname LIKE 'Service role full access%'
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I', pol.policyname, pol.schemaname, pol.tablename);
    END LOOP;
END $$;


-- ─── AGENCIES ─────────────────────────────────────────────────────────────────
-- Users can only read their own agency record

CREATE POLICY "agency_read_own"
    ON agencies FOR SELECT
    USING (id = auth_agency_id());

CREATE POLICY "agency_update_own"
    ON agencies FOR UPDATE
    USING (id = auth_agency_id() AND is_management())
    WITH CHECK (id = auth_agency_id());


-- ─── AGENTS ───────────────────────────────────────────────────────────────────
-- All agency members can read teammates; only management can write

CREATE POLICY "agents_select_agency"
    ON agents FOR SELECT
    USING (agency_id = auth_agency_id());

CREATE POLICY "agents_insert_management"
    ON agents FOR INSERT
    WITH CHECK (agency_id = auth_agency_id() AND is_management());

CREATE POLICY "agents_update_management"
    ON agents FOR UPDATE
    USING (agency_id = auth_agency_id() AND is_management())
    WITH CHECK (agency_id = auth_agency_id());

CREATE POLICY "agents_delete_management"
    ON agents FOR DELETE
    USING (agency_id = auth_agency_id() AND is_management());


-- ─── LEADS ────────────────────────────────────────────────────────────────────
-- Management sees all agency leads; agents see only assigned leads

CREATE POLICY "leads_select_scoped"
    ON leads FOR SELECT
    USING (
        agency_id = auth_agency_id()
        AND (is_management() OR assigned_agent_id = auth_agent_id())
    );

CREATE POLICY "leads_insert_agency"
    ON leads FOR INSERT
    WITH CHECK (agency_id = auth_agency_id());

CREATE POLICY "leads_update_scoped"
    ON leads FOR UPDATE
    USING (
        agency_id = auth_agency_id()
        AND (is_management() OR assigned_agent_id = auth_agent_id())
    )
    WITH CHECK (agency_id = auth_agency_id());

CREATE POLICY "leads_delete_management"
    ON leads FOR DELETE
    USING (agency_id = auth_agency_id() AND is_management());


-- ─── CONVERSATIONS ────────────────────────────────────────────────────────────

CREATE POLICY "conversations_select_scoped"
    ON conversations FOR SELECT
    USING (
        agency_id = auth_agency_id()
        AND (
            is_management()
            OR lead_id IN (
                SELECT id FROM leads
                WHERE agency_id = auth_agency_id()
                  AND assigned_agent_id = auth_agent_id()
            )
        )
    );

CREATE POLICY "conversations_insert_scoped"
    ON conversations FOR INSERT
    WITH CHECK (agency_id = auth_agency_id());

CREATE POLICY "conversations_update_scoped"
    ON conversations FOR UPDATE
    USING (agency_id = auth_agency_id())
    WITH CHECK (agency_id = auth_agency_id());


-- ─── VIEWINGS ─────────────────────────────────────────────────────────────────

CREATE POLICY "viewings_select_scoped"
    ON viewings FOR SELECT
    USING (
        agency_id = auth_agency_id()
        AND (is_management() OR agent_id = auth_agent_id())
    );

CREATE POLICY "viewings_insert_scoped"
    ON viewings FOR INSERT
    WITH CHECK (agency_id = auth_agency_id());

CREATE POLICY "viewings_update_scoped"
    ON viewings FOR UPDATE
    USING (
        agency_id = auth_agency_id()
        AND (is_management() OR agent_id = auth_agent_id())
    )
    WITH CHECK (agency_id = auth_agency_id());

CREATE POLICY "viewings_delete_management"
    ON viewings FOR DELETE
    USING (agency_id = auth_agency_id() AND is_management());


-- ─── FOLLOW_UPS ───────────────────────────────────────────────────────────────

CREATE POLICY "follow_ups_agency"
    ON follow_ups FOR ALL
    USING (agency_id = auth_agency_id())
    WITH CHECK (agency_id = auth_agency_id());


-- ─── OWNER_REPORTS ────────────────────────────────────────────────────────────

CREATE POLICY "owner_reports_agency"
    ON owner_reports FOR ALL
    USING (agency_id = auth_agency_id())
    WITH CHECK (agency_id = auth_agency_id());


-- ─── DOCUMENTS ────────────────────────────────────────────────────────────────

CREATE POLICY "documents_agency"
    ON documents FOR ALL
    USING (agency_id = auth_agency_id())
    WITH CHECK (agency_id = auth_agency_id());


-- ─── CONTRACTS ────────────────────────────────────────────────────────────────

CREATE POLICY "contracts_agency"
    ON contracts FOR ALL
    USING (agency_id = auth_agency_id())
    WITH CHECK (agency_id = auth_agency_id());


-- ─── CHEQUES ──────────────────────────────────────────────────────────────────

CREATE POLICY "cheques_agency"
    ON cheques FOR ALL
    USING (agency_id = auth_agency_id())
    WITH CHECK (agency_id = auth_agency_id());


-- ─── CONNECTORS ───────────────────────────────────────────────────────────────

CREATE POLICY "connectors_agency"
    ON connectors FOR ALL
    USING (agency_id = auth_agency_id())
    WITH CHECK (agency_id = auth_agency_id());


-- ─── OWNERS (Phase 3) ─────────────────────────────────────────────────────────

CREATE POLICY "owners_agency"
    ON owners FOR ALL
    USING (agency_id = auth_agency_id())
    WITH CHECK (agency_id = auth_agency_id());


-- ─── CALL_CAMPAIGNS (Phase 3) ───────────────────────────────────────────────────

CREATE POLICY "call_campaigns_agency"
    ON call_campaigns FOR ALL
    USING (agency_id = auth_agency_id())
    WITH CHECK (agency_id = auth_agency_id());


-- ─── CALLS (Phase 3) ──────────────────────────────────────────────────────────

CREATE POLICY "calls_agency"
    ON calls FOR ALL
    USING (agency_id = auth_agency_id())
    WITH CHECK (agency_id = auth_agency_id());


-- ─── DONE ─────────────────────────────────────────────────────────────────────
-- Backend (FastAPI) uses service_role key and enforces tenancy in auth_middleware + tenant.py.
-- These RLS policies protect direct Supabase client access (frontend with anon key + user JWT).
--
-- To verify: SELECT auth_agency_id();  (returns UUID when authenticated)
