-- ─── schema_v12_whatsapp_queue.sql ──────────────────────────────────────────
-- Outbound WhatsApp message queue.
--
-- Purpose: When a lead is outside the 24-hour customer-service window and no
-- approved template exists for their WABA, the intended outbound message is
-- stored here instead of being silently dropped or sent as an illegal free-form
-- text (which Meta would reject with error 131047).
--
-- Auto-drain: When the lead messages back in (last_inbound_at is refreshed in
-- services/webhooks.py), the drain_outbound_queue_for_lead() helper in
-- services/whatsapp_service.py fetches all 'pending' rows for that lead_id and
-- retries them as free-form text (now within window).
--
-- DO NOT RUN without explicit confirmation.
-- Apply manually via the Supabase SQL editor or psql.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS whatsapp_outbound_queue (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agency_id        UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    agent_id         UUID REFERENCES agents(id) ON DELETE SET NULL,
    lead_id          UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    to_phone         TEXT NOT NULL,
    body             TEXT NOT NULL,            -- original free-form message body
    template_name    TEXT,                     -- template that was tried (if any)
    template_params  JSONB DEFAULT '[]',       -- params that would have been used
    status           TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'draining', 'sent', 'failed', 'expired')),
    reason           TEXT NOT NULL DEFAULT 'no_template',
                         -- 'no_template' | 'window_closed' (future use)
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    send_after       TIMESTAMPTZ,              -- reserved for future scheduling
    sent_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_wa_queue_lead
    ON whatsapp_outbound_queue (lead_id, status);

CREATE INDEX IF NOT EXISTS idx_wa_queue_agency_status
    ON whatsapp_outbound_queue (agency_id, status);

COMMENT ON TABLE whatsapp_outbound_queue IS
    'Queued outbound WhatsApp messages blocked by the 24-hour window with no '
    'approved template. Drained automatically when the lead messages back in.';

-- ── RLS: service-role only (same pattern as whatsapp_templates) ────────────────
ALTER TABLE whatsapp_outbound_queue ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_all_wa_outbound_queue" ON whatsapp_outbound_queue;
CREATE POLICY "service_role_all_wa_outbound_queue"
    ON whatsapp_outbound_queue
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- ── Atomic Drain RPC ──────────────────────────────────────────────────────────
-- Atomically claims all 'pending' messages for a lead by transitioning their
-- status to 'draining' and returning the claimed rows. Concurrent calls for the
-- same lead_id will return an empty set, preventing duplicate sends.
--
-- Security:
-- In Postgres, CREATE FUNCTION grants EXECUTE to PUBLIC by default. For a
-- SECURITY DEFINER function, we explicitly REVOKE EXECUTE from PUBLIC, anon,
-- and authenticated, pin search_path to public to prevent search-path hijacking,
-- and GRANT EXECUTE exclusively to service_role.
CREATE OR REPLACE FUNCTION claim_queued_messages(p_lead_id UUID)
RETURNS SETOF whatsapp_outbound_queue
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  UPDATE whatsapp_outbound_queue
  SET status = 'draining'
  WHERE lead_id = p_lead_id AND status = 'pending'
  RETURNING *;
$$;

REVOKE EXECUTE ON FUNCTION claim_queued_messages(UUID) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION claim_queued_messages(UUID) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION claim_queued_messages(UUID) TO service_role;
