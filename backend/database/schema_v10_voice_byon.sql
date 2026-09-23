-- ============================================================
--  AndiOS Schema V10 — BYON Voice Channel
--  Run AFTER schema_v9_byon_communication.sql
--
--  Adds:
--    • Voice-specific columns to communication_accounts
--      - forwarding_status: tracks agent's call forwarding setup progress
--      - custom_greeting:   per-agent Vapi prompt/greeting override
--      - verified_caller_id_sid: Twilio OutgoingCallerId SID for BYON outbound
--    • Partial unique index on (channel, phone_number) for O(1) ForwardedFrom lookup
--    • Helper RPC: resolve_agent_by_voice_number()
--    • CENTRAL_INBOUND_DID config key stored in platform_config
-- ============================================================


-- ─── Voice-specific columns on communication_accounts ─────────────────────────

-- Tracks whether the agent has set up conditional call forwarding to AndiOS
ALTER TABLE communication_accounts
    ADD COLUMN IF NOT EXISTS forwarding_status TEXT DEFAULT 'not_configured';
-- 'not_configured' | 'configured' | 'verified' | 'disabled'

-- Optional per-agent greeting/persona override injected into Vapi prompt
ALTER TABLE communication_accounts
    ADD COLUMN IF NOT EXISTS custom_greeting TEXT;

-- Twilio OutgoingCallerId SID — stored after agent completes BYON caller-ID verification
-- Also stored as metadata->>'verified_caller_id_sid' for legacy compat; this column is canonical.
ALTER TABLE communication_accounts
    ADD COLUMN IF NOT EXISTS verified_caller_id_sid TEXT;

COMMENT ON COLUMN communication_accounts.forwarding_status IS
    'Call forwarding setup state. not_configured → configured (agent dialed code) → verified (test call succeeded).';
COMMENT ON COLUMN communication_accounts.custom_greeting IS
    'Optional per-agent custom greeting/persona override. Injected into Vapi system prompt at call time.';
COMMENT ON COLUMN communication_accounts.verified_caller_id_sid IS
    'Twilio OutgoingCallerId SID. Populated after agent verifies their mobile number for BYON outbound caller ID display.';


-- ─── Fast O(1) lookup index for inbound call ForwardedFrom matching ───────────
-- When Twilio fires an inbound voice webhook, ForwardedFrom contains the agent's
-- personal number. We do a direct lookup on (channel='voice', phone_number=ForwardedFrom).
CREATE UNIQUE INDEX IF NOT EXISTS uq_comm_accounts_voice_phone
    ON communication_accounts (phone_number)
    WHERE channel = 'voice' AND status = 'active' AND phone_number IS NOT NULL;


-- ─── Helper RPC: resolve agent & agency by voice forwarding phone number ───────
-- Called by the inbound voice webhook to route a forwarded call to the correct agent.
--
-- Matching priority:
--   1. communication_accounts.phone_number (agent's personal mobile) — exact match
--   2. agents.phone (agent table phone column) — exact match when no comm_account row exists yet
--
-- Returns the first match; callers must handle empty result set (fallback receptionist).

CREATE OR REPLACE FUNCTION resolve_agent_by_voice_number(p_phone TEXT)
RETURNS TABLE (
    agency_id           UUID,
    agent_id            UUID,
    comm_account_id     UUID,
    agent_name          TEXT,
    custom_greeting     TEXT,
    forwarding_status   TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    -- Tier 1: exact match via communication_accounts (preferred — has full voice metadata)
    RETURN QUERY
    SELECT
        ca.agency_id,
        ca.agent_id,
        ca.id               AS comm_account_id,
        COALESCE(ag.name, '')::TEXT AS agent_name,
        ca.custom_greeting,
        ca.forwarding_status
    FROM communication_accounts ca
    LEFT JOIN agents ag ON ag.id = ca.agent_id
    WHERE ca.phone_number = p_phone
      AND ca.channel = 'voice'
      AND ca.status = 'active'
    LIMIT 1;

    IF FOUND THEN RETURN; END IF;

    -- Tier 2: match against agents.phone (no explicit comm_account row yet)
    RETURN QUERY
    SELECT
        ag.agency_id,
        ag.id               AS agent_id,
        NULL::UUID          AS comm_account_id,
        ag.name::TEXT       AS agent_name,
        NULL::TEXT          AS custom_greeting,
        'not_configured'::TEXT AS forwarding_status
    FROM agents ag
    WHERE ag.phone = p_phone
    LIMIT 1;
END;
$$;


-- ─── Platform config: central inbound DID ─────────────────────────────────────
-- Stores the single Twilio DID all agents forward to.
-- Stored here so it can be read dynamically without a deploy.

CREATE TABLE IF NOT EXISTS platform_config (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enable RLS for security
ALTER TABLE platform_config ENABLE ROW LEVEL SECURITY;

-- Authenticated agents can read platform config (e.g. central inbound DID)
CREATE POLICY "authenticated_read_platform_config"
    ON platform_config FOR SELECT
    TO authenticated
    USING (true);

-- Only backend service role can modify platform config
CREATE POLICY "service_manage_platform_config"
    ON platform_config FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

INSERT INTO platform_config (key, value)
    VALUES ('central_inbound_did', '')
    ON CONFLICT (key) DO NOTHING;

COMMENT ON TABLE platform_config IS
    'Platform-wide key/value config. Editable by super-admin without deploy. E.g. central_inbound_did.';
