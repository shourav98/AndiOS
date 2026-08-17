-- ============================================================
-- AndiOS Billing System — Supabase SQL Migration
-- Run this in Supabase Dashboard → SQL Editor
-- ============================================================

-- ─── 1. Subscriptions Table ─────────────────────────────────

CREATE TABLE IF NOT EXISTS subscriptions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agency_id           UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,

    -- Stripe IDs (empty until Stripe is connected)
    stripe_cust_id      TEXT UNIQUE,
    stripe_sub_id       TEXT UNIQUE,

    -- Plan info
    plan_tier           TEXT NOT NULL DEFAULT 'grow'
                        CHECK (plan_tier IN ('basic', 'grow', 'pro')),
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'trialing', 'past_due', 'canceled', 'unpaid')),

    -- Call quota
    active_addons       TEXT[] DEFAULT ARRAY[]::TEXT[],  -- e.g. ['p2000', 'p1000']
    included_calls      INTEGER NOT NULL DEFAULT 3000,   -- from plan tier
    addon_calls         INTEGER NOT NULL DEFAULT 0,      -- from active add-on packs
    used_calls          INTEGER NOT NULL DEFAULT 0,      -- reset every billing cycle

    -- Billing cycle
    billing_cycle_start TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    billing_cycle_end   TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '1 month'),

    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_subs_agency_id    ON subscriptions(agency_id);
CREATE INDEX IF NOT EXISTS idx_subs_stripe_cust  ON subscriptions(stripe_cust_id);
CREATE INDEX IF NOT EXISTS idx_subs_stripe_sub   ON subscriptions(stripe_sub_id);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_subscriptions_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_subscriptions_updated_at ON subscriptions;
CREATE TRIGGER trg_subscriptions_updated_at
    BEFORE UPDATE ON subscriptions
    FOR EACH ROW EXECUTE FUNCTION update_subscriptions_updated_at();


-- ─── 2. Invoices Table ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS invoices (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agency_id        UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,

    -- Invoice identifiers
    invoice_number   TEXT NOT NULL UNIQUE,   -- e.g. '139350-06'
    contract_number  TEXT NOT NULL DEFAULT '139350',
    stripe_invoice_id TEXT UNIQUE,           -- Stripe invoice ID for portal link

    -- Period & amounts
    billing_period   TEXT NOT NULL,          -- e.g. '28 May, 2026 – 28 Jun, 2026'
    due_date         DATE NOT NULL,
    amount           NUMERIC(12, 2) NOT NULL,       -- Total incl. VAT
    vat_amount       NUMERIC(12, 2) NOT NULL,       -- 5% VAT portion
    net_amount       NUMERIC(12, 2)
        GENERATED ALWAYS AS (amount - vat_amount) STORED,

    -- Status & metadata
    frequency        TEXT NOT NULL DEFAULT 'Monthly',
    mode             TEXT NOT NULL DEFAULT 'Card',
    status           TEXT NOT NULL DEFAULT 'unpaid'
                     CHECK (status IN ('paid', 'unpaid', 'upcoming', 'void')),
    pdf_url          TEXT,
    paid_at          TIMESTAMPTZ,

    created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_invoices_agency_id ON invoices(agency_id);
CREATE INDEX IF NOT EXISTS idx_invoices_status    ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_invoices_stripe_id ON invoices(stripe_invoice_id);


-- ─── 3. Row-Level Security ───────────────────────────────────
-- Only agency owners/managers can see their own subscription data

ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE invoices      ENABLE ROW LEVEL SECURITY;

-- Service role bypasses RLS (used by backend)
-- Application-level scoping is handled in FastAPI (apply_agency_scope)


-- ─── 4. Seed Initial Subscription for Existing Agencies ─────
-- Run this once to give existing agencies a default subscription row.
-- Adjust agency_id, plan_tier, billing dates as needed.

-- INSERT INTO subscriptions (
--     agency_id, plan_tier, status,
--     included_calls, billing_cycle_start, billing_cycle_end
-- )
-- SELECT
--     id,
--     COALESCE(subscription_plan, 'grow'),
--     COALESCE(subscription_status, 'active'),
--     CASE
--         WHEN COALESCE(subscription_plan, 'grow') = 'basic' THEN 1000
--         WHEN COALESCE(subscription_plan, 'grow') = 'grow'  THEN 3000
--         WHEN COALESCE(subscription_plan, 'grow') = 'pro'   THEN 8000
--         ELSE 3000
--     END,
--     NOW(),
--     NOW() + INTERVAL '1 month'
-- FROM agencies
-- ON CONFLICT (agency_id) DO NOTHING;   -- remove if no unique constraint exists yet
