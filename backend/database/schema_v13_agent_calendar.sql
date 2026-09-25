-- ============================================================
--  AndiOS Phase 4 Migration — Per-Agent Google Calendar Integration
--  Run this in your Supabase SQL editor
--  Adds:
--    • google_token_data (JSONB/encrypted) to agents table
--    • is_calendar_connected (BOOLEAN) to agents table
-- ============================================================

ALTER TABLE agents
    ADD COLUMN IF NOT EXISTS google_token_data JSONB,
    ADD COLUMN IF NOT EXISTS is_calendar_connected BOOLEAN DEFAULT FALSE;

COMMENT ON COLUMN agents.google_token_data IS
    'Encrypted OAuth2 tokens for agent personal Google Calendar integration (Fernet encrypted JSON string stored as JSONB or TEXT).';

COMMENT ON COLUMN agents.calendar_id IS
    'Google Calendar ID for this agent (e.g. primary or specific calendar email).';

COMMENT ON COLUMN agents.is_calendar_connected IS
    'Flag indicating whether agent has an active Google Calendar OAuth integration.';
