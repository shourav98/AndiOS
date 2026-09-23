-- ============================================================
--  AndiOS Schema V11 — Security & Compliance Foundation (Additive & Non-Destructive)
--  Run AFTER schema_v10_voice_byon.sql
--
--  STRICTLY ADDITIVE & IDEMPOTENT:
--    • Preserves existing `access_token_enc` (BYTEA) as defined in schema_v9.
--    • No column drops, no renames, no destructive type conversions of existing data.
--    • Fully backward-compatible with pre-migration code.
--
--  Changes:
--    1. communication_accounts additive columns:
--       - token_expires_at (TIMESTAMPTZ): token expiry tracking
--       - is_coexistence (BOOLEAN NOT NULL DEFAULT false): WhatsApp Coexistence flag
--       - registration_pin_enc (BYTEA): encrypted 6-digit PIN for number registration
--    2. leads additive column:
--       - last_inbound_at (TIMESTAMPTZ): per-lead 24h customer service window tracking
--         (Kept on leads so 24h window is strictly scoped per lead/conversation).
--         Read in: services/communication/template_service.py (is_within_24h_window)
--         Written in: routers/webhooks.py on inbound WhatsApp webhook receipt
--    3. Pre-check for duplicate active phone_number_id, then unique index globally
--    4. whatsapp_processed_messages: idempotency store with RLS (service-role only)
--       - No foreign keys on agency_id/agent_id so idempotency logging never fails on FK
--       - 72h purge scheduled in app scheduler (purge_old_whatsapp_processed_messages_job)
--    5. whatsapp_templates: per-WABA message templates store with RLS (service-role only)
--       - communication_account_id FK with ON DELETE CASCADE
--       - Unique constraint on (waba_id, name, language)
--    6. Column-level privilege separation:
--       - Table-level REVOKE SELECT on communication_accounts from anon, authenticated
--       - Column-level GRANT SELECT (safe columns only) to authenticated
--    7. communication_accounts_safe view WITH (security_invoker = true):
--       - Ensures underlying table RLS policies apply to caller queries
-- ============================================================


-- ─── Step 1: Ensure access_token_enc exists (BYTEA) ──────────────────────────
-- If it already exists from schema_v9, this is a no-op.
ALTER TABLE communication_accounts
    ADD COLUMN IF NOT EXISTS access_token_enc BYTEA;

COMMENT ON COLUMN communication_accounts.access_token_enc IS
    'Encrypted access token (Fernet AES-128-CBC + HMAC-SHA256 ciphertext stored as bytea). '
    'Encrypted/decrypted via app-space utils/crypto.py using dedicated TOKEN_ENCRYPTION_KEY.';


-- ─── Step 2: Add Additive Columns to communication_accounts ───────────────────
ALTER TABLE communication_accounts
    ADD COLUMN IF NOT EXISTS token_expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS is_coexistence BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS registration_pin_enc BYTEA;

COMMENT ON COLUMN communication_accounts.token_expires_at IS
    'When the access_token expires. NULL = permanent token. Short-lived ESU tokens expire in ~60 days.';

COMMENT ON COLUMN communication_accounts.is_coexistence IS
    'True when account is in WhatsApp Coexistence mode (agent keeps WhatsApp Business App active).';

COMMENT ON COLUMN communication_accounts.registration_pin_enc IS
    'Fernet-encrypted random 6-digit registration PIN generated for Cloud API number registration.';


-- ─── Step 3: Add last_inbound_at to leads (Per-Lead 24h Window) ───────────────
-- Customer care 24h window is strictly between the business and each individual lead.
-- Moving this off communication_accounts prevents crosstalk across different leads.
ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS last_inbound_at TIMESTAMPTZ;

COMMENT ON COLUMN leads.last_inbound_at IS
    'Timestamp of the latest inbound message received from this lead. '
    'Used to enforce the per-lead 24h WhatsApp customer service window. '
    'Written in: routers/webhooks.py upon inbound WhatsApp message receipt. '
    'Read in: services/communication/template_service.py (is_within_24h_window) before sending outbound messages.';


-- ─── Step 4: Precheck & Global Unique Constraint on phone_number_id ──────────
-- Proof / verification query to verify existing data satisfies unique phone_number_id:
--   SELECT phone_number_id, COUNT(*)
--   FROM communication_accounts
--   WHERE phone_number_id IS NOT NULL AND status != 'disconnected'
--   GROUP BY phone_number_id
--   HAVING COUNT(*) > 1;

DO $$
BEGIN
    IF EXISTS (
        SELECT phone_number_id
        FROM communication_accounts
        WHERE phone_number_id IS NOT NULL AND status != 'disconnected'
        GROUP BY phone_number_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'Cannot create unique index: duplicate active phone_number_id found in communication_accounts. Run cleanup before applying migration.';
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_comm_accounts_phone_number_id
    ON communication_accounts (phone_number_id)
    WHERE phone_number_id IS NOT NULL AND status != 'disconnected';


-- ─── Step 5: WhatsApp Message Idempotency Store (Service-Role RLS) ────────────
-- Idempotency insert must NEVER fail on foreign key constraints.
-- Plain UUID columns without REFERENCES:
CREATE TABLE IF NOT EXISTS whatsapp_processed_messages (
    message_id   TEXT PRIMARY KEY,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    agency_id    UUID,
    agent_id     UUID,
    status       TEXT NOT NULL DEFAULT 'completed' -- 'completed' | 'failed' | 'retrying'
);

-- Ensure status column exists if table was created in an earlier schema iteration
ALTER TABLE whatsapp_processed_messages
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'completed';

-- Ensure NO foreign key constraints exist on agency_id or agent_id
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'whatsapp_processed_messages_agency_id_fkey'
    ) THEN
        ALTER TABLE whatsapp_processed_messages DROP CONSTRAINT whatsapp_processed_messages_agency_id_fkey;
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'whatsapp_processed_messages_agent_id_fkey'
    ) THEN
        ALTER TABLE whatsapp_processed_messages DROP CONSTRAINT whatsapp_processed_messages_agent_id_fkey;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_wa_processed_processed_at
    ON whatsapp_processed_messages (processed_at);

COMMENT ON TABLE whatsapp_processed_messages IS
    'Atomic idempotency store for inbound WhatsApp messages. '
    'Protected by RLS (service-role only). Purge is scheduled via APScheduler in services/scheduler.py '
    '(purge_old_whatsapp_processed_messages_job) to remove records older than 72h.';

-- Enable RLS and lock down to service_role only
ALTER TABLE whatsapp_processed_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_all_whatsapp_processed_messages" ON whatsapp_processed_messages;
CREATE POLICY "service_role_all_whatsapp_processed_messages"
    ON whatsapp_processed_messages
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);


-- ─── Step 6: WhatsApp Templates Store (Service-Role RLS) ──────────────────────
-- Proof / verification query to verify template unique constraint:
--   SELECT waba_id, name, language, COUNT(*)
--   FROM whatsapp_templates
--   GROUP BY waba_id, name, language
--   HAVING COUNT(*) > 1;

CREATE TABLE IF NOT EXISTS whatsapp_templates (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    communication_account_id   UUID REFERENCES communication_accounts(id) ON DELETE CASCADE,
    waba_id                    TEXT NOT NULL,
    name                       TEXT NOT NULL,
    language                   TEXT NOT NULL DEFAULT 'en',
    category                   TEXT NOT NULL, -- 'UTILITY' | 'MARKETING' | 'AUTHENTICATION'
    status                     TEXT NOT NULL DEFAULT 'PENDING', -- 'PENDING' | 'APPROVED' | 'REJECTED' | 'PAUSED'
    components                 JSONB NOT NULL DEFAULT '[]',
    meta_template_id           TEXT,
    created_at                 TIMESTAMPTZ DEFAULT NOW(),
    updated_at                 TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_waba_template UNIQUE (waba_id, name, language)
);

CREATE INDEX IF NOT EXISTS idx_wa_templates_account
    ON whatsapp_templates (communication_account_id);

CREATE INDEX IF NOT EXISTS idx_wa_templates_waba_name
    ON whatsapp_templates (waba_id, name);

COMMENT ON TABLE whatsapp_templates IS
    'Templates registered per WABA. Required for initiating outbound messages outside the 24h window. '
    'Protected by RLS (service-role only).';

-- Enable RLS and lock down to service_role only
ALTER TABLE whatsapp_templates ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_all_whatsapp_templates" ON whatsapp_templates;
CREATE POLICY "service_role_all_whatsapp_templates"
    ON whatsapp_templates
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);


-- ─── Step 7: Security: Mask Encrypted Credentials from Client Roles ──────────
-- In PostgreSQL, column-level REVOKE is a no-op if a table-level GRANT SELECT exists.
-- We must revoke table-level SELECT from authenticated and anon, then explicitly GRANT
-- SELECT only on safe columns.

REVOKE SELECT ON communication_accounts FROM anon, authenticated;

GRANT SELECT (
    id,
    agency_id,
    agent_id,
    channel,
    provider,
    phone_number,
    phone_number_id,
    external_account_id,
    status,
    is_coexistence,
    token_expires_at,
    meta_onboarding_state,
    metadata,
    connected_at,
    disconnected_at,
    created_at,
    updated_at
) ON communication_accounts TO authenticated;

-- Provide a secure view WITH (security_invoker = true) so RLS of the underlying table
-- is strictly applied when clients query through the view.
CREATE OR REPLACE VIEW communication_accounts_safe
WITH (security_invoker = true) AS
    SELECT
        id,
        agency_id,
        agent_id,
        channel,
        provider,
        phone_number,
        phone_number_id,
        external_account_id,
        status,
        is_coexistence,
        token_expires_at,
        meta_onboarding_state,
        metadata,
        connected_at,
        disconnected_at,
        created_at,
        updated_at
    FROM communication_accounts;

GRANT SELECT ON communication_accounts_safe TO authenticated;

-- Verification Query (Multi-Tenant Isolation via security_invoker):
-- Run as authenticated user associated with Agency A:
--   SET LOCAL ROLE authenticated;
--   SET LOCAL "request.jwt.claims" = '{"sub": "user-agent-a", "role": "authenticated", "agency_id": "agency-uuid-a"}';
--   SELECT * FROM communication_accounts_safe WHERE agency_id = 'agency-uuid-b';
-- Expected Result: 0 rows returned (blocked by underlying communication_accounts RLS).
