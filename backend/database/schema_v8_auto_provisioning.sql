-- ============================================================
--  AndiOS Phase 8 Migration — Fully Automated Provisioning & Activation
--  DEPLOY SEQUENCE:
--    1. Run THIS file in Supabase SQL Editor
--    2. Deploy backend services
-- ============================================================

-- ─── STEP 1: Add columns for auto-provisioning & market defaults ───────────────

ALTER TABLE agencies
  -- Market country code (defaults to 'US' — US numbers are instantly purchasable via Twilio API,
  -- reliable, ~$1.15/month. WhatsApp sender country does NOT need to match recipient country
  -- per Twilio docs — any Twilio WhatsApp number works globally.)
  ADD COLUMN IF NOT EXISTS country_code VARCHAR(10) NOT NULL DEFAULT 'US',

  -- Timestamp tracking provisioning start and lifecycle
  ADD COLUMN IF NOT EXISTS number_provisioned_at TIMESTAMPTZ;


-- ─── STEP 2: Atomic Claim RPC with Crash Recovery Timeout ──────────────────────
-- Prevents concurrent Stripe webhooks from buying duplicate numbers.
-- If a crash happens midway through Twilio purchase, allows reclaim after p_stale_minutes.

CREATE OR REPLACE FUNCTION claim_agency_number_provisioning(
    p_agency_id UUID,
    p_stale_minutes INT DEFAULT 10
)
RETURNS BOOLEAN AS $$
DECLARE
    v_claimed_id UUID;
BEGIN
    UPDATE agencies
    SET whatsapp_number_status = 'provisioning',
        number_provisioned_at = NOW()
    WHERE id = p_agency_id
      AND dedicated_whatsapp_number IS NULL
      AND (
          whatsapp_number_status = 'none'
          OR (
              whatsapp_number_status = 'provisioning'
              AND (number_provisioned_at IS NULL OR number_provisioned_at < (NOW() - (p_stale_minutes || ' minutes')::INTERVAL))
          )
      )
    RETURNING id INTO v_claimed_id;

    RETURN v_claimed_id IS NOT NULL;
END;
$$ LANGUAGE plpgsql;


-- ─── STEP 3: Safe Release RPC ──────────────────────────────────────────────────
-- Used when provisioning encounters an error or requires release.

CREATE OR REPLACE FUNCTION release_agency_number_claim(
    p_agency_id UUID,
    p_status TEXT DEFAULT 'none'
)
RETURNS BOOLEAN AS $$
DECLARE
    v_released_id UUID;
BEGIN
    UPDATE agencies
    SET whatsapp_number_status = p_status
    WHERE id = p_agency_id
      AND whatsapp_number_status = 'provisioning'
      AND dedicated_whatsapp_number IS NULL
    RETURNING id INTO v_released_id;

    RETURN v_released_id IS NOT NULL;
END;
$$ LANGUAGE plpgsql;
