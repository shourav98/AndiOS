# AndiOS BYON — Staging-First Zero-Downtime Deployment Runbook

This document details the exact safe, chronological procedure for deploying the Meta WhatsApp Cloud API BYON and security foundation.

Rollout Strategy: **Staging-First Rollout -> Validation -> Production Rollout**.

---

## 0. Architecture & Backward-Compatibility Guarantees

- **Code Works Against Pre- and Post-Migration Databases**:
  - The codebase does not assume new columns exist; if columns or tables are absent, it degrades gracefully with logging.
  - If existing records contain legacy unencrypted plaintext tokens, `decrypt_token()` detects and returns them safely (`ALLOW_LEGACY_PLAINTEXT_TOKENS=True`).
  - If dedicated `communication_accounts` rows are absent, the provider factory seamlessly falls back to platform default environment variables (`WHATSAPP_PHONE_NUMBER_ID` and `WHATSAPP_API_KEY`).
- **Non-Destructive Database Changes**:
  - All additions in `schema_v11_security.sql` are purely additive (`ADD COLUMN IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`, `CREATE UNIQUE INDEX IF NOT EXISTS`).
  - No columns dropped, no columns renamed, existing `access_token_enc BYTEA` column is preserved.

---

## 1. Pre-Migration Diagnostics & Security Audit

### 1.1 Live Schema Inspection Query
Run this read-only query in your Supabase SQL editor to inspect the current state of `communication_accounts`:

```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'communication_accounts'
ORDER BY ordinal_position;
```

### 1.2 Secret Exposure Audit & Data Cleanup (Item 1)
To verify whether `META_APP_SECRET` was ever written into `metadata`:

**Step A: Read-Only Check Query**
```sql
SELECT id, agency_id, agent_id 
FROM communication_accounts 
WHERE metadata ? 'app_secret';
```

> [!WARNING]
> **CRITICAL SECURITY NOTE**:
> If the read-only check query above returns ANY rows, `META_APP_SECRET` was persisted in plaintext inside database storage.
> You **MUST immediately rotate `META_APP_SECRET`** in the Meta Developer Portal (App Settings -> Basic -> App Secret -> Reset) and update all environment configurations accordingly.

**Step B: Data Cleanup Statement**
```sql
UPDATE communication_accounts 
SET metadata = metadata - 'app_secret' 
WHERE metadata ? 'app_secret';
```

---

## 2. Phase I: Staging Project Rollout

Perform all migration and deployment steps on your **Staging Supabase Project** and Staging environment before touching production:

1. **Staging Schema Precheck & Migration Order**:
   - Run the duplicate `phone_number_id` precheck:
     ```sql
     SELECT phone_number_id, COUNT(*)
     FROM communication_accounts
     WHERE phone_number_id IS NOT NULL AND status != 'disconnected'
     GROUP BY phone_number_id
     HAVING COUNT(*) > 1;
     ```
   - **Step 1A: Execute `backend/database/schema_v11_security.sql`** in Staging SQL editor (idempotency, RLS, `leads.last_inbound_at`, template cache).
   - **Step 1B: Execute `backend/database/schema_v12_whatsapp_queue.sql`** in Staging SQL editor (outbound queue, index, service-role RLS, atomic `claim_queued_messages` RPC).
   - Verify tables, columns, and RPC created:
     ```sql
     SELECT table_name FROM information_schema.tables 
     WHERE table_name IN ('whatsapp_processed_messages', 'whatsapp_templates', 'whatsapp_outbound_queue');

     SELECT routine_name FROM information_schema.routines 
     WHERE routine_name = 'claim_queued_messages';
     ```

2. **Staging Token Encryption Backfill**:
   > [!CAUTION]
   > **CRITICAL PRE-REQUISITE**:
   > DO NOT run the backfill script until Items 2a and 2b are fully fixed and verified in code:
   > - Item 2a: Sanitizing and decoding legacy BYTEA-hex ASCII (`\x...`) to raw plaintext before encryption, preventing double/hex-string encryption.
   > - Item 2b: Checking column existence in the fetched row before setting `access_token = None` (avoiding Postgres errors if the column does not exist).
   
   - Ensure staging `.env` has `TOKEN_ENCRYPTION_KEY` configured with a valid 32-byte Fernet key:
     ```bash
     python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
     ```
   - **Step 1: Execute Dry-Run First**:
     ```bash
     python backend/scripts/backfill_encrypt_tokens.py --dry-run
     ```
   - **Step 2: Inspect Dry-Run Output**:
     Thoroughly examine the logged summary:
     - Verify `Unencrypted Found` equals expected legacy rows.
     - Verify `Pre-write Verified` matches `Unencrypted Found`.
     - Confirm `Errors Encountered` is strictly 0.
   - **Step 3: Execute Database Write**:
     ```bash
     python backend/scripts/backfill_encrypt_tokens.py --execute
     ```
   - Run a second time to verify idempotency:
     ```bash
     python backend/scripts/backfill_encrypt_tokens.py --execute
     # Expected: 0 unencrypted found, 0 updated, 0 errors.
     ```

3. **Staging Automated Test Suite**:
   - Run test suite against staging configuration:
     ```bash
     pytest tests/ -q --tb=short
     ```

4. **Staging End-to-End Verification Checklist (Gap 1 + Gap 2)**:
   - [ ] **Checklist Item 1: Closed Window + No Template (Queue + Notify)**
     - Create a test lead with `last_inbound_at = NULL` assigned to a BYON account with no approved Meta template.
     - Dispatch message via `send_whatsapp_smart()`.
     - **Verify**: Message is **not** sent free-form; row is inserted in `whatsapp_outbound_queue` with `status='pending'` and `reason='no_template'`; WhatsApp notification is delivered to assigned agent's phone.
   - [ ] **Checklist Item 2: Closed Window + Env Fallback (Free-form Default)**
     - Using the platform default agency (which has no `communication_accounts` row, `account is None`), dispatch message to a lead with `last_inbound_at = NULL`.
     - **Verify**: `send_whatsapp_smart()` routes directly to free-form send via platform environment provider, preserving legacy platform behavior.
   - [ ] **Checklist Item 3: Open Window (Free-form Send)**
     - Set test lead `last_inbound_at = NOW() - interval '10 minutes'`.
     - Dispatch message via `send_whatsapp_smart()`.
     - **Verify**: Direct free-form message sent immediately; queue is bypassed.
   - [ ] **Checklist Item 4: Auto-Drain on Window Reopen (Atomic RPC Claim & Drain)**
     - Leave or insert a pending item in `whatsapp_outbound_queue` for a test lead.
     - Simulate an inbound lead message by updating `leads.last_inbound_at = NOW()` (or posting to webhook `/webhooks/whatsapp`).
     - **Verify**: `drain_outbound_queue_for_lead()` calls `claim_queued_messages` RPC; pending row transitions to `'sent'` and message is dispatched.
   - [ ] **Checklist Item 5: Drain Failure Re-notification**
     - If a drain message fails delivery (e.g. invalid phone number), confirm row transitions to `'failed'` and assigned agent receives a `Queued Message Delivery Failed` notification alert.

---

## 3. Phase II: Production Rollout

### Step 1: Database Snapshot / Backup
1. In production Supabase Dashboard, go to **Database** -> **Backups**.
2. Trigger a manual backup or record the latest automated Point-in-Time Recovery (PITR) timestamp.

### Step 2: Audit & Clean Metadata
Execute the queries from Section 1.2 in production:
1. `SELECT id FROM communication_accounts WHERE metadata ? 'app_secret';`
2. `UPDATE communication_accounts SET metadata = metadata - 'app_secret' WHERE metadata ? 'app_secret';`
3. (If rows existed, rotate `META_APP_SECRET` in Meta Developer Portal).

### Step 3: Run Additive Migrations in Production Supabase
1. Open Supabase Dashboard -> **SQL Editor** -> **New Query**.
2. **Step 3A**: Run `backend/database/schema_v11_security.sql`.
3. **Step 3B**: Run `backend/database/schema_v12_whatsapp_queue.sql`.
4. Verify schema objects:
   ```sql
   SELECT column_name FROM information_schema.columns 
   WHERE table_name = 'leads' AND column_name = 'last_inbound_at';

   SELECT table_name FROM information_schema.tables 
   WHERE table_name = 'whatsapp_outbound_queue';

   SELECT routine_name FROM information_schema.routines 
   WHERE routine_name = 'claim_queued_messages';
   ```

### Step 4: Configure Production Environment Variables
On production server (`root@159.198.77.123`):
File: `/home/softvencealpha-andreearizan/htdocs/andreearizan.softvencealpha.com/.env`

```ini
# Meta WhatsApp Cloud API Configuration
WHATSAPP_PROVIDER=meta
META_GRAPH_API_VERSION=v26.0
WHATSAPP_VERIFY_TOKEN=andios_verify_token
META_APP_SECRET=<your_meta_app_secret>
META_APP_ID=<your_meta_app_id>

# Dedicated Token Encryption Key (MultiFernet)
TOKEN_ENCRYPTION_KEY=<generated_32b_fernet_key>
ALLOW_LEGACY_PLAINTEXT_TOKENS=true

# Feature Flags
ENABLE_TWILIO_PROVISIONING=false
ENABLE_VOICE_BYON=false
```

### Step 5: Deploy Code & Execute Token Backfill
```bash
ssh root@159.198.77.123
cd /home/softvencealpha-andreearizan/htdocs/andreearizan.softvencealpha.com/
git fetch origin byon-meta-esu
git checkout byon-meta-esu
source venv/bin/activate

# 1. Backfill dry run
python backend/scripts/backfill_encrypt_tokens.py --dry-run

# 2. Backfill execution
python backend/scripts/backfill_encrypt_tokens.py --execute

# 3. Restart application service
systemctl restart andios-backend
```

### Step 6: Smoke Test on Existing Single-Agency Live Number
1. **Health Check**:
   ```bash
   curl -i https://andreearizan.softvencealpha.com/health
   # Expect: 200 OK
   ```
2. **Meta Webhook Verification**:
   ```bash
   curl -i "https://andreearizan.softvencealpha.com/webhooks/whatsapp?hub.mode=subscribe&hub.challenge=test1234&hub.verify_token=andios_verify_token"
   # Expect: 200 test1234
   ```
3. **Live Message Verification**:
   - Send test inquiry from external mobile phone to the existing agency WhatsApp number.
   - Verify logs: `journalctl -u andios-backend -f` shows idempotency recording, AI handling, and outbound message dispatch.

---

## 4. Rollback Plan

### Fast Code Rollback
1. Check out previous commit:
   ```bash
   git checkout <previous_stable_commit>
   systemctl restart andios-backend
   ```
2. Because `schema_v11_security.sql` was strictly additive, previous code continues working without database changes.

### Key Rollback
If key rotation is needed, MultiFernet supports comma-separated keys:
`TOKEN_ENCRYPTION_KEY="<current_key>,<previous_key>"`.

### Disaster Recovery
If catastrophic failure occurs, restore Supabase via PITR to the snapshot taken in Phase II Step 1.
