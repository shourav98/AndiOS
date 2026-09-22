-- ============================================================
--  AndiOS Phase 7 Migration — Cleanup (DEFERRED)
--  ⚠️  DO NOT RUN until:
--    1. schema_v6_shared_gateway.sql has been applied
--    2. New backend code has been deployed
--    3. System has been STABLE in production for 2+ weeks
--    4. All 5 Postman test cases pass consistently
--
--  This file removes deprecated columns that are no longer
--  referenced anywhere in the codebase (verified by grep).
-- ============================================================

-- ─── Remove deprecated tenant-specific WhatsApp credential columns ────────────
-- These were used in the old "Tenant-Owned Twilio" model.
-- The new "Centralized Shared Gateway" model uses:
--   - dedicated_whatsapp_number (column added in schema_v6)
--   - TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN in .env (master credentials)

ALTER TABLE agencies DROP COLUMN IF EXISTS whatsapp_access_token;
ALTER TABLE agencies DROP COLUMN IF EXISTS waba_id;
