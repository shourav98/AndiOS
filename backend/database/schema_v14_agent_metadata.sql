-- ============================================================
--  AndiOS Migration v14 — Agent Metadata & External Portal IDs
--  Run this in your Supabase SQL editor
-- ============================================================

ALTER TABLE agents 
    ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb;

COMMENT ON COLUMN agents.metadata IS
    'Arbitrary metadata for agent (e.g. pf_profile_id, external portal IDs, routing preferences).';
