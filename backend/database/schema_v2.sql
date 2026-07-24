-- ============================================================
--  AndiOS Phase 2 — Database Schema Update
--  Run this in your Supabase SQL editor AFTER schema.sql
-- ============================================================

-- ─── DOCUMENTS (OCR / Extraction) ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS documents (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lead_id             UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    document_type       TEXT NOT NULL,                   -- passport | emirates_id | other
    file_url            TEXT NOT NULL,                   -- Supabase Storage URL
    extracted_data      JSONB,                           -- Extracted OCR data (name, expiry, doc_num, etc.)
    status              TEXT DEFAULT 'pending',          -- pending | extracted | failed
    error_message       TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_documents_lead_id ON documents(lead_id);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);

-- ─── CONTRACTS ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contracts (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lead_id             UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    type                TEXT NOT NULL,                   -- tenancy_agreement | addendum
    property_address    TEXT NOT NULL,
    rent_amount         NUMERIC NOT NULL,
    security_deposit    NUMERIC,
    start_date          DATE NOT NULL,
    end_date            DATE NOT NULL,
    status              TEXT DEFAULT 'draft',            -- draft | sent | signed | active | expired | cancelled
    document_url        TEXT,                            -- URL to generated PDF
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_contracts_lead_id ON contracts(lead_id);
CREATE INDEX IF NOT EXISTS idx_contracts_status ON contracts(status);

-- ─── CHEQUES ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cheques (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contract_id         UUID NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    cheque_number       TEXT NOT NULL,
    bank_name           TEXT NOT NULL,
    amount              NUMERIC NOT NULL,
    due_date            DATE NOT NULL,
    status              TEXT DEFAULT 'pending',          -- pending | deposited | cleared | bounced
    front_image_url     TEXT,
    back_image_url      TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cheques_contract_id ON cheques(contract_id);
CREATE INDEX IF NOT EXISTS idx_cheques_due_date ON cheques(due_date);
CREATE INDEX IF NOT EXISTS idx_cheques_status ON cheques(status);

-- ─── UPDATED_AT TRIGGERS ───────────────────────────────────────────────────────
CREATE TRIGGER trg_documents_updated_at
    BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_contracts_updated_at
    BEFORE UPDATE ON contracts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_cheques_updated_at
    BEFORE UPDATE ON cheques
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ─── ROW LEVEL SECURITY (RLS) ──────────────────────────────────────────────────
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE contracts ENABLE ROW LEVEL SECURITY;
ALTER TABLE cheques ENABLE ROW LEVEL SECURITY;
