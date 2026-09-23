-- ============================================================
--  AndiOS Phase 6 Migration — Centralized Shared Gateway
--  DEPLOY SEQUENCE:
--    1. Run THIS file (Phase 1 — additive only, no DROP)
--    2. Deploy new backend code
--    3. Run 5 Postman test cases
--    4. After 2+ weeks stable → run schema_v7_cleanup.sql (Phase 2 DROP)
--
--  NOTE: whatsapp_access_token and waba_id columns are NOT removed here.
--  They are deprecated but kept for safe rollback. Remove in Phase 2.
-- ============================================================


-- ─── STEP 1: Add shared gateway columns to agencies table ─────────────────────

ALTER TABLE agencies
  -- Dedicated Twilio numbers (one per agency, bought from master Twilio account)
  ADD COLUMN IF NOT EXISTS dedicated_whatsapp_number  VARCHAR(50) UNIQUE,
  ADD COLUMN IF NOT EXISTS dedicated_voice_number     VARCHAR(50) UNIQUE,

  -- WhatsApp number approval status (Meta Business Manager flow)
  -- none → provisioned → active → failed
  ADD COLUMN IF NOT EXISTS whatsapp_number_status     TEXT NOT NULL DEFAULT 'none',

  -- Monthly quota limits (set per subscription plan)
  ADD COLUMN IF NOT EXISTS whatsapp_monthly_limit     INT NOT NULL DEFAULT 1000,
  ADD COLUMN IF NOT EXISTS whatsapp_monthly_used      INT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS voice_monthly_limit        INT NOT NULL DEFAULT 1000,
  ADD COLUMN IF NOT EXISTS voice_monthly_used         INT NOT NULL DEFAULT 0,

  -- Freeze flag: set TRUE when quota exhausted OR by admin
  -- NOTE: The atomic RPCs block on limit even if this flag is FALSE.
  -- Dashboard should show frozen if: is_quota_frozen OR used >= limit
  ADD COLUMN IF NOT EXISTS is_quota_frozen            BOOLEAN NOT NULL DEFAULT FALSE,

  -- Timestamp of last monthly reset (for audit trail)
  ADD COLUMN IF NOT EXISTS quota_reset_at             TIMESTAMPTZ;


-- ─── STEP 2: Indexes for ultra-fast webhook tenant resolution ─────────────────

-- Critical: inbound webhook handler resolves agency by To phone number.
-- This lookup happens on EVERY inbound WhatsApp message — must be O(1).
CREATE INDEX IF NOT EXISTS idx_agencies_dedicated_whatsapp
  ON agencies(dedicated_whatsapp_number)
  WHERE dedicated_whatsapp_number IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_agencies_dedicated_voice
  ON agencies(dedicated_voice_number)
  WHERE dedicated_voice_number IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_agencies_quota_frozen
  ON agencies(is_quota_frozen)
  WHERE is_quota_frozen = TRUE;


-- ─── STEP 3: Atomic Quota RPC — WhatsApp ──────────────────────────────────────
-- Performs check + increment in a SINGLE atomic UPDATE.
-- Returns TRUE if the message is allowed (quota consumed).
-- Returns FALSE if blocked (frozen flag OR limit reached).
-- This eliminates the SELECT → UPDATE race condition on burst traffic.

CREATE OR REPLACE FUNCTION check_and_increment_whatsapp_quota(agency_uuid UUID)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  updated_rows INT;
BEGIN
  UPDATE agencies
  SET whatsapp_monthly_used = whatsapp_monthly_used + 1
  WHERE id = agency_uuid
    AND is_quota_frozen = FALSE
    AND whatsapp_monthly_used < whatsapp_monthly_limit;

  GET DIAGNOSTICS updated_rows = ROW_COUNT;
  RETURN updated_rows > 0;
  -- TRUE  → allowed & quota consumed atomically
  -- FALSE → blocked (frozen or limit reached), zero mutation
END;
$$;


-- ─── STEP 4: Atomic Quota RPC — Voice (Vapi/Sami AI) ─────────────────────────

CREATE OR REPLACE FUNCTION check_and_increment_voice_quota(agency_uuid UUID)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  updated_rows INT;
BEGIN
  UPDATE agencies
  SET voice_monthly_used = voice_monthly_used + 1
  WHERE id = agency_uuid
    AND is_quota_frozen = FALSE
    AND voice_monthly_used < voice_monthly_limit;

  GET DIAGNOSTICS updated_rows = ROW_COUNT;
  RETURN updated_rows > 0;
END;
$$;


-- ─── STEP 5: Quota Refund RPC — WhatsApp (AI failure path) ───────────────────
-- Called ONLY when OpenAI/AI processing fails AFTER quota was consumed.
-- NOT called for Twilio delivery failures (those are valid usage attempts).
-- Uses GREATEST(0, ...) to prevent negative usage counts.

CREATE OR REPLACE FUNCTION decrement_whatsapp_used(agency_uuid UUID)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  UPDATE agencies
  SET whatsapp_monthly_used = GREATEST(0, whatsapp_monthly_used - 1)
  WHERE id = agency_uuid;
END;
$$;


-- ─── STEP 6: Quota Refund RPC — Voice (AI failure path) ──────────────────────

CREATE OR REPLACE FUNCTION decrement_voice_used(agency_uuid UUID)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  UPDATE agencies
  SET voice_monthly_used = GREATEST(0, voice_monthly_used - 1)
  WHERE id = agency_uuid;
END;
$$;


-- ─── STEP 7: Monthly Quota Reset RPC ─────────────────────────────────────────
-- Called by POST /admin/quota/monthly-reset (GitHub Actions on 1st of each month).
-- Resets WhatsApp + Voice usage counters and clears frozen flag for active agencies.
-- Optionally pass a list of specific agency UUIDs to reset only those.

CREATE OR REPLACE FUNCTION reset_monthly_quotas(target_agency_ids UUID[] DEFAULT NULL)
RETURNS INT  -- Returns count of agencies reset
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  affected INT;
BEGIN
  IF target_agency_ids IS NULL THEN
    -- Reset ALL active agencies
    UPDATE agencies
    SET whatsapp_monthly_used = 0,
        voice_monthly_used    = 0,
        is_quota_frozen       = FALSE,
        quota_reset_at        = NOW()
    WHERE subscription_status = 'active';
  ELSE
    -- Reset specific agencies (e.g. after Add-on purchase)
    UPDATE agencies
    SET whatsapp_monthly_used = 0,
        voice_monthly_used    = 0,
        is_quota_frozen       = FALSE,
        quota_reset_at        = NOW()
    WHERE id = ANY(target_agency_ids)
      AND subscription_status = 'active';
  END IF;

  GET DIAGNOSTICS affected = ROW_COUNT;
  RETURN affected;
END;
$$;


-- ─── STEP 8: pg_cron Scheduled Reset (Pro tier only) ─────────────────────────
-- IMPORTANT: Only run the block below if your Supabase plan supports pg_cron.
-- Verify first: SELECT * FROM pg_extension WHERE extname = 'pg_cron';
-- If empty result → use GitHub Actions fallback (see .github/workflows/monthly-reset.yml)
--
-- To enable on Pro tier:
--   Supabase Dashboard → Database → Extensions → Enable pg_cron
--
-- UNCOMMENT the block below ONLY after confirming pg_cron is available:
/*
SELECT cron.schedule(
  'andios-monthly-quota-reset',     -- job name (unique)
  '0 0 1 * *',                      -- Every 1st of month at 00:00 UTC
  $cron$
    SELECT reset_monthly_quotas();
  $cron$
);
*/
-- If pg_cron is NOT available, the GitHub Actions workflow handles this.
-- See: .github/workflows/monthly-reset.yml


-- ─── STEP 9: Validate migration ───────────────────────────────────────────────
-- Run this after migration to confirm columns exist:
/*
SELECT
  column_name,
  data_type,
  column_default
FROM information_schema.columns
WHERE table_name = 'agencies'
  AND column_name IN (
    'dedicated_whatsapp_number', 'dedicated_voice_number',
    'whatsapp_number_status',
    'whatsapp_monthly_limit', 'whatsapp_monthly_used',
    'voice_monthly_limit', 'voice_monthly_used',
    'is_quota_frozen', 'quota_reset_at'
  )
ORDER BY column_name;
*/
