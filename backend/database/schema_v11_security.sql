-- ============================================================
--  AndiOS Schema V11 — Security Foundation
--  Run AFTER schema_v10_voice_byon.sql
--
--  Changes:
--    1. Normalize access_token storage:
--       - If `access_token_enc` (BYTEA) exists → rename to `access_token` (TEXT)
--         so the Python Fernet layer handles encryption in app-space.
--       - If `access_token` already exists as TEXT → keep as-is.
--       - Either way: column ends up as TEXT named `access_token`.
--    2. Add `token_expires_at` — track token expiry for proactive refresh.
--    3. Add `is_coexistence` — whether this account uses WA Coexistence mode.
--    4. Create `whatsapp_processed_messages` — idempotency store for inbound WA.
-- ============================================================


-- ─── Step 1: Normalize access_token column ────────────────────────────────────
-- Handle both schema states safely.

DO $$
BEGIN
    -- Case A: Legacy BYTEA column exists → rename + retype to TEXT
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'communication_accounts'
          AND column_name = 'access_token_enc'
          AND data_type = 'bytea'
    ) THEN
        -- Add new TEXT column
        ALTER TABLE communication_accounts
            ADD COLUMN IF NOT EXISTS access_token TEXT;

        -- Note: existing BYTEA data is NOT auto-migrated here.
        -- Any existing encrypted BYTEA tokens will be NULL in the new column.
        -- Re-connect affected accounts via Embedded Signup to repopulate.

        -- Remove old BYTEA column
        ALTER TABLE communication_accounts
            DROP COLUMN access_token_enc;

        RAISE NOTICE 'Renamed access_token_enc (BYTEA) → access_token (TEXT)';

    -- Case B: access_token already exists as TEXT → nothing to do
    ELSIF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'communication_accounts'
          AND column_name = 'access_token'
    ) THEN
        RAISE NOTICE 'access_token (TEXT) already exists — no column change needed';

    -- Case C: Neither column exists → create fresh
    ELSE
        ALTER TABLE communication_accounts
            ADD COLUMN access_token TEXT;
        RAISE NOTICE 'Created access_token (TEXT) column';
    END IF;
END;
$$;


-- ─── Step 2: Add token_expires_at ─────────────────────────────────────────────
ALTER TABLE communication_accounts
    ADD COLUMN IF NOT EXISTS token_expires_at TIMESTAMPTZ;

COMMENT ON COLUMN communication_accounts.token_expires_at IS
    'When the access_token expires. NULL = permanent token (System User tokens). '
    'Short-lived tokens from Embedded Signup code exchange expire in ~60 days; '
    'refresh before this date.';


-- ─── Step 3: Add is_coexistence ───────────────────────────────────────────────
ALTER TABLE communication_accounts
    ADD COLUMN IF NOT EXISTS is_coexistence BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN communication_accounts.is_coexistence IS
    'True when this account uses WhatsApp Coexistence mode — the agent keeps '
    'their WhatsApp Business App active alongside the Cloud API. '
    'Requires subscribing to smb_message_echoes, history, smb_app_state_sync '
    'webhook fields in addition to standard messages.';


-- ─── Step 4: WhatsApp Message Idempotency Store ───────────────────────────────
-- Prevents double-processing of retried webhooks.
-- Records the Meta message_id for 72 hours, then expires (auto-cleaned by cron or TTL).

CREATE TABLE IF NOT EXISTS whatsapp_processed_messages (
    message_id   TEXT PRIMARY KEY,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    agency_id    UUID,
    agent_id     UUID
);

-- Auto-expire rows older than 72 hours (housekeeping — run via scheduler or pg_cron)
-- The backend dedup_service.py will add a cleanup job.

CREATE INDEX IF NOT EXISTS idx_wa_processed_processed_at
    ON whatsapp_processed_messages (processed_at);

COMMENT ON TABLE whatsapp_processed_messages IS
    'Idempotency store for inbound WhatsApp messages. '
    'Before processing any inbound message, check if message_id exists here. '
    'Insert on first processing. Rows older than 72h are safe to delete.';


-- ─── Step 5: Update updated_at trigger (if not already on the table) ──────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.triggers
        WHERE event_object_table = 'whatsapp_processed_messages'
    ) THEN
        -- No trigger needed — this is an append-only idempotency log, not updated.
        NULL;
    END IF;
END;
$$;


-- ─── Summary ──────────────────────────────────────────────────────────────────
-- After running this migration:
--   • communication_accounts.access_token is TEXT (Fernet-encrypted by Python layer)
--   • communication_accounts.token_expires_at tracks expiry
--   • communication_accounts.is_coexistence flags WA Coexistence accounts
--   • whatsapp_processed_messages provides inbound message dedup
