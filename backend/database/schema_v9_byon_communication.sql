-- ============================================================
--  AndiOS Schema V9 — BYON Communication Accounts
--  Run AFTER schema_v8_auto_provisioning.sql
--
--  Adds:
--    • communication_accounts — per-agency and per-agent WhatsApp/Voice/SMS channels
--    • Support for Bring Your Own Number (BYON) at Agency or Individual Agent level
--    • Indexes + RLS policies
--    • Helper RPCs: get_agency_by_phone_number_id(), get_agency_by_whatsapp_number()
-- ============================================================


-- ─── communication_accounts ──────────────────────────────────────────────────
-- One row per agency (or per agent) per channel (e.g. Agent John's WhatsApp via Meta,
-- or Agency Elite's shared default WhatsApp).
-- This is the BYON "connection" — agencies/agents bring their own numbers here.

CREATE TABLE IF NOT EXISTS communication_accounts (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agency_id            UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    agent_id             UUID REFERENCES agents(id) ON DELETE CASCADE,
    -- agent_id = NULL indicates an agency-wide default channel (shared across team).
    -- agent_id != NULL indicates a specific agent's dedicated BYON channel.

    -- Channel + Provider
    channel              TEXT NOT NULL,   -- 'whatsapp' | 'voice' | 'sms'
    provider             TEXT NOT NULL,   -- 'meta' | 'twilio' | '360dialog' | 'vapi'

    -- Phone identity
    phone_number         TEXT,            -- E.164 digits without '+', e.g. '971501234567'
    phone_number_id      TEXT,            -- Meta phone_number_id (primary lookup key for inbound)
    external_account_id  TEXT,            -- Meta WABA ID / Twilio SID / etc.

    -- Credentials (access_token stored AES-256 encrypted via pgcrypto)
    -- Decrypt at query time using the get_communication_account_by_* RPCs below.
    access_token_enc     BYTEA,           -- pgcrypto-encrypted access token

    -- Status
    status               TEXT NOT NULL DEFAULT 'disconnected',
    -- 'disconnected' | 'pending_verification' | 'active' | 'suspended'

    -- Meta-specific onboarding state
    meta_onboarding_state TEXT,           -- 'embedded_signup' | 'verified' | null

    -- Arbitrary provider-specific config (non-sensitive)
    -- e.g. {"app_secret": "...", "waba_name": "Elite Properties", "verified_caller_id_sid": "..."}
    metadata             JSONB DEFAULT '{}',

    connected_at         TIMESTAMPTZ,
    disconnected_at      TIMESTAMPTZ,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    updated_at           TIMESTAMPTZ DEFAULT NOW()
);

-- Unique constraints
-- 1) One active channel connection per agency default (when agent_id IS NULL)
CREATE UNIQUE INDEX IF NOT EXISTS uq_comm_accounts_agency_channel_default
    ON communication_accounts (agency_id, channel)
    WHERE agent_id IS NULL;

-- 2) One active channel connection per specific agent (when agent_id IS NOT NULL)
CREATE UNIQUE INDEX IF NOT EXISTS uq_comm_accounts_agent_channel
    ON communication_accounts (agency_id, agent_id, channel)
    WHERE agent_id IS NOT NULL;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_comm_accounts_agency
    ON communication_accounts (agency_id);

CREATE INDEX IF NOT EXISTS idx_comm_accounts_agent
    ON communication_accounts (agent_id)
    WHERE agent_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_comm_accounts_phone_number_id
    ON communication_accounts (phone_number_id)
    WHERE phone_number_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_comm_accounts_phone_number
    ON communication_accounts (phone_number)
    WHERE phone_number IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_comm_accounts_status
    ON communication_accounts (status);

-- updated_at trigger
CREATE TRIGGER trg_comm_accounts_updated_at
    BEFORE UPDATE ON communication_accounts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ─── RLS Policies ─────────────────────────────────────────────────────────────
ALTER TABLE communication_accounts ENABLE ROW LEVEL SECURITY;

-- Agency members can read accounts belonging to their agency
CREATE POLICY "agency_read_own_comm_accounts"
    ON communication_accounts FOR SELECT
    USING (
        agency_id IN (
            SELECT agency_id FROM agents
            WHERE id = auth.uid()
        )
    );

-- Only service role (backend) can insert/update/delete
CREATE POLICY "service_manage_comm_accounts"
    ON communication_accounts FOR ALL
    USING (auth.role() = 'service_role');


-- ─── Add communication_account_id to conversations ───────────────────────────
-- Links each conversation to the specific channel account it came through.

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS communication_account_id UUID
        REFERENCES communication_accounts(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_conversations_comm_account
    ON conversations (communication_account_id)
    WHERE communication_account_id IS NOT NULL;


-- ─── Helper RPC: resolve agency & agent by Meta phone_number_id ───────────────
-- Called by the Meta webhook handler to route inbound messages to the correct agency and agent.

CREATE OR REPLACE FUNCTION get_agency_by_phone_number_id(p_phone_number_id TEXT)
RETURNS TABLE (
    agency_id           UUID,
    agent_id            UUID,
    comm_account_id     UUID,
    provider            TEXT,
    phone_number        TEXT,
    status              TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    RETURN QUERY
    SELECT
        ca.agency_id,
        ca.agent_id,
        ca.id          AS comm_account_id,
        ca.provider,
        ca.phone_number,
        ca.status
    FROM communication_accounts ca
    WHERE ca.phone_number_id = p_phone_number_id
      AND ca.channel = 'whatsapp'
      AND ca.status = 'active'
    LIMIT 1;
END;
$$;


-- ─── Helper RPC: resolve agency & agent by WhatsApp phone number ──────────────
-- Supports both per-agent BYON, agency BYON, and legacy Twilio gateway.

CREATE OR REPLACE FUNCTION get_agency_by_whatsapp_number(p_phone_number TEXT)
RETURNS TABLE (
    agency_id           UUID,
    agent_id            UUID,
    comm_account_id     UUID,
    provider            TEXT,
    status              TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    -- 1. Check communication_accounts table (BYON - agent or agency level)
    RETURN QUERY
    SELECT
        ca.agency_id,
        ca.agent_id,
        ca.id AS comm_account_id,
        ca.provider,
        ca.status
    FROM communication_accounts ca
    WHERE ca.phone_number = p_phone_number
      AND ca.channel = 'whatsapp'
      AND ca.status = 'active'
    ORDER BY ca.agent_id NULLS LAST
    LIMIT 1;

    -- 2. If no rows, also check the legacy agencies.dedicated_whatsapp_number column
    IF NOT FOUND THEN
        RETURN QUERY
        SELECT
            a.id       AS agency_id,
            NULL::UUID AS agent_id,
            NULL::UUID AS comm_account_id,
            'twilio'   AS provider,
            a.whatsapp_number_status AS status
        FROM agencies a
        WHERE a.dedicated_whatsapp_number = p_phone_number
          AND a.whatsapp_number_status = 'active'
        LIMIT 1;
    END IF;
END;
$$;
