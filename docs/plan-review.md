# AndiOS BYON — Phase 1 Plan Review
**Cross-checking audit findings against official documentation.**
Date: 2026-09-19

---

## 1. Supabase Schema Verification

> **Problem**: Schema v9 defines `access_token_enc BYTEA` but `provider_factory.py` reads `row.get("access_token")`. One of these is wrong.

Run this **read-only** SQL in your Supabase SQL editor to check the live schema:

```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'communication_accounts'
ORDER BY ordinal_position;
```

### Case A — Live DB has `access_token_enc` (schema matches migration)
The column was created as `BYTEA` but `provider_factory.py` reads a non-existent `access_token` TEXT column → tokens are silently empty. Fix: add `access_token` TEXT column for plaintext interim storage, then encrypt in Step A.

### Case B — Live DB has `access_token` (migration was not run, or was modified)
The column is plain TEXT. Fix: rename to `access_token_enc`, change to `BYTEA`, add encryption layer.

> Both cases are covered by the **Step A migration** below — it handles either situation safely with `IF NOT EXISTS` / `IF EXISTS` guards.

---

## 2. Official Doc Verification

### 2.1 Meta Graph API Version
**VERIFIED. `v19.0` is dangerously outdated.**

- Current stable: **v26.0** (as of Sep 2026)
- `v20.0` deprecates **Sep 24, 2026** — imminent
- `v19.0` is already deprecated; requests may fail at any time
- Source: developers.facebook.com/docs/graph-api/changelog

**Action:** Upgrade to `v22.0` (stable, safe margin), make it configurable via `META_GRAPH_API_VERSION` env var.

### 2.2 Tech Provider Requirements
**VERIFIED.**

Before Embedded Signup can onboard external WABAs under AndiOS's app, the following must be done manually (not in code):

| Requirement | What to Do |
|-------------|-----------|
| Meta Business Portfolio verified | Complete Meta Business Verification in Meta Business Settings |
| App with `whatsapp_business_management` + `whatsapp_business_messaging` at **Advanced Access** | Submit App Review in Meta Developer portal |
| Functioning product demo | Required before Meta approves Advanced Access |

**Client action required:** These cannot be automated — a human must complete them in Meta's portals. Documented in `human-setup-checklist.md` (Step F).

### 2.3 WhatsApp Coexistence — UAE Numbers
**VERIFIED. UAE numbers ARE eligible.**

- No regional restriction for UAE
- Requirements: number must be active in WhatsApp Business App for ≥7 days
- The Embedded Signup v4 flow automatically detects and routes to the Coexistence path when the number is already used in the WA Business App
- `featureType` to pass in JS SDK `FB.login` extras: **`"whatsapp_business_app_onboarding"`**

**Coexistence-specific webhook fields to subscribe** (beyond standard `messages`):
- `smb_message_echoes` — messages sent FROM the mobile app (mirrored to API)
- `history` — historical messages sync
- `smb_app_state_sync` — app state synchronization

Source: developers.facebook.com/docs/whatsapp/embedded-signup/coexistence

### 2.4 Business-Initiated First Contact (24h Window)
**VERIFIED.**

- 24h window opens ONLY when the **customer sends a message first**
- For business-initiated contact (e.g. pinging a Property Finder lead who has never messaged us), a **pre-approved HSM template** is **MANDATORY**
- Template categories: Marketing, Utility, Authentication
- As of July 2025, Meta charges **per delivered template message**
- Free-form text to a never-contacted lead → **Meta rejects with error code 131047**

**Action:** `ai_service.py` must check conversation history — if zero prior messages from lead, send via approved template, not free-form text.

### 2.5 Embedded Signup v4 — Backend Flow
**VERIFIED.**

After user completes ESU popup:
1. Frontend receives `{ code, waba_id, phone_number_id }` from SDK callback
2. Backend receives `code` → exchanges for system user token via:
   ```
   POST https://graph.facebook.com/v26.0/oauth/access_token
   ?client_id={APP_ID}&client_secret={APP_SECRET}&code={code}
   ```
3. Backend registers phone number for Cloud API:
   ```
   POST /v26.0/{phone_number_id}/register
   {"messaging_product": "whatsapp", "pin": "000000"}
   ```
4. Backend subscribes app to WABA webhooks:
   ```
   POST /v26.0/{waba_id}/subscribed_apps
   ```

Legacy ESU v2/v3 deprecated **October 15, 2026** — must build v4.

### 2.6 `httpx2` Warning
**VERIFIED. `httpx2` is a real, maintained package** (by Pydantic Services, available on PyPI).

- Drop-in replacement; API is identical to `httpx`
- Starlette `TestClient` officially recommends `httpx2`
- Safe to migrate: update `requirements.txt` to add `httpx2`, update test client imports
- **Will do in Step F** (non-blocking for production, only affects test client warning)

---

## 3. Changes to Original Audit Findings

| Finding | Status | Change |
|---------|--------|--------|
| Graph API v19.0 outdated | 🔴 CONFIRMED CRITICAL | Upgrade to v22.0 (configurable) in Step A |
| UAE Coexistence ineligible | Audit said UNVERIFIED | **VERIFIED: UAE eligible** |
| featureType for coexistence | Audit said UNVERIFIED | **VERIFIED: `whatsapp_business_app_onboarding`** |
| HSM template for first contact | Audit said UNVERIFIED | **VERIFIED: mandatory** |
| Tech Provider requirements | Audit said UNVERIFIED | **VERIFIED: requires human actions in Meta portals** |
| `httpx2` warning | Audit said don't migrate unless verified | **VERIFIED: migrate in Step F** |

---

## 4. Implementation Plan

### Step A — Security Foundation
**Files to create/modify:**

#### [NEW] `backend/utils/crypto.py`
- `encrypt_token(plaintext: str) -> str` — Fernet symmetric encryption using `SECRET_KEY`
- `decrypt_token(ciphertext: str) -> str` — decryption + error handling
- Tokens stored as base64-encoded Fernet ciphertext (plain text column, no BYTEA needed)

#### [NEW] `backend/database/schema_v11_security.sql`
- Reversible migration:
  - If `access_token_enc` exists: rename to `access_token` (TEXT), populate from existing BYTEA if any
  - If `access_token` exists: keep as-is
  - Add `token_expires_at TIMESTAMPTZ`
  - Add `is_coexistence BOOLEAN DEFAULT false`
  - Add `whatsapp_message_ids` dedup table (for Step A idempotency)

#### [MODIFY] `backend/services/communication/provider_factory.py`
- Remove silent Twilio fallback on unresolvable provider
- Default fallback provider becomes `"meta"` (not `"twilio"`)
- Unresolvable provider: log `ERROR` + raise `ValueError` (no silent swallow)
- Decrypt token via `crypto.decrypt_token()` when building `CommunicationAccount`

#### [MODIFY] `backend/services/communication/meta_adapter.py`
- Replace hardcoded `v19.0` with `settings.META_GRAPH_API_VERSION` (default `"v22.0"`)

#### [MODIFY] `backend/config.py`
- Add `META_GRAPH_API_VERSION: str = "v22.0"`
- Add `ENABLE_TWILIO_PROVISIONING: bool = False` (feature flag for legacy provisioning)

#### [MODIFY] `backend/services/provisioning_service.py`
- Wrap all provisioning logic in `if settings.ENABLE_TWILIO_PROVISIONING:` guard
- Add `# DEPRECATED: legacy Twilio auto-provisioning` header
- Existing agencies keep working; new ones route to Embedded Signup

#### [MODIFY] `backend/tests/test_byon_communication.py`
- Fix failing mock: use proper string `"meta"` instead of unconstrained MagicMock chain

---

### Step B — Webhook Routing by phone_number_id
**Files to modify:**

#### [MODIFY] `backend/routers/webhooks.py`
- Replace inline agency-resolution logic with call to `get_agency_by_phone_number_id()` DB RPC
- Use `to_identifier` (phone_number_id) from `InboundMessage` to resolve agency + agent
- Idempotency: before processing, check `whatsapp_message_ids` table; if seen → 200 OK, skip

---

### Step C — Embedded Signup Backend Endpoint
**Files to create/modify:**

#### [MODIFY] `backend/routers/connectors.py`
New endpoints:
- `POST /connectors/whatsapp/embedded-signup-callback` — receives `{code, waba_id, phone_number_id, is_coexistence}`
  1. Exchange code → system user token (Graph API v22.0)
  2. Register phone number for Cloud API
  3. Subscribe app to WABA webhooks (including coexistence fields)
  4. Encrypt token + upsert into `communication_accounts`
  5. Return `{status: "connected", phone_number, waba_id}`
- `GET /connectors/whatsapp/status` — return current connection status for agent/agency
- `DELETE /connectors/whatsapp/disconnect` — set status → `disconnected`, clear tokens

#### [NEW] `backend/docs/embedded-signup-frontend-contract.md`
- JS SDK init snippet
- `featureType: "whatsapp_business_app_onboarding"` for coexistence
- Callback format
- Backend endpoint spec

---

### Step D — Template System + 24h Enforcement
**Files to create/modify:**

#### [NEW] `backend/database/schema_v12_templates.sql`
- `whatsapp_templates` table: `(id, agency_id, name, category, language, status, body_text, created_at)`

#### [NEW] `backend/routers/templates.py`
- `GET /templates` — list agency templates
- `POST /templates` — create + submit to Meta for approval
- `GET /templates/{id}/status` — check approval status

#### [MODIFY] `backend/services/ai_service.py`
- Before sending any message, check: has this lead ever sent us a message?
  - Yes (within 24h) → free-form text OK
  - No / outside 24h → use first-contact template (`lead_greeting` or similar)
- Add `send_template_message()` helper

#### [MODIFY] `backend/routers/webhooks.py`
- Property Finder webhook parser: detect and parse `wa.me/` links in lead description/notes field
- Extract phone number from `wa.me/{phone}` → store as lead phone

---

### Step E — VoiceProvider + SMSProvider ABCs
**Files to create/modify:**

#### [MODIFY] `backend/services/communication/base.py`
- Add `VoiceProvider(ABC)` with: `initiate_call()`, `verify_caller_id()`, `confirm_caller_id()`, `parse_inbound_call()`
- Add `SMSProvider(ABC)` with: `send_sms()`, `parse_inbound_sms()`

#### [NEW] `backend/services/communication/twilio_voice_adapter.py`
- Move Twilio-specific voice code from `voice_service.py` and `routers/agent_phone_settings.py`
- Implements `VoiceProvider`

#### [MODIFY] `backend/routers/agent_phone_settings.py`
- Replace direct Twilio imports with `VoiceProvider` interface calls

#### [MODIFY] `backend/services/voice_service.py`
- Replace direct Twilio REST calls with `TwilioVoiceAdapter` calls

---

### Step F — Docs, .env.example, Checklist
#### [MODIFY] `backend/.env.example`
- Add `META_GRAPH_API_VERSION=v22.0`
- Add `ENABLE_TWILIO_PROVISIONING=false`
- Add `TOKEN_ENCRYPTION_KEY=` (Fernet key — generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)

#### [NEW] `docs/communication-providers.md`
- Architecture diagram (text)
- How to add a new provider
- How BYON resolution works

#### [NEW] `docs/human-setup-checklist.md`
- Step-by-step: Meta Business Verification
- App Review for Advanced Access permissions
- Creating first-contact HSM template
- Configuring Embedded Signup JS SDK on frontend

#### [MODIFY] `requirements.txt`
- Add `httpx2` (replaces `httpx` for test client; production code keeps `httpx`)
- Add `cryptography` (for Fernet token encryption)

---

## 5. Scope/Cost/Behavior Impact Assessment

No scope changes from original plan. All findings confirm the plan is on track:

| Finding | Impact |
|---------|--------|
| Graph API v19.0 outdated | **Client-facing risk** — existing live requests may start failing Sep 24, 2026. Upgrade immediately in Step A. |
| HSM template mandatory for first contact | **Behavior change** — first AI greeting to new PF leads will change from instant free-form to template. Client must approve the template in Meta. |
| Tech Provider requirements | **Human action required** — client must complete Meta Business Verification before Embedded Signup can work for agents. Document in checklist. |
| UAE Coexistence eligible | No cost/scope change — good news, proceed as planned. |
| `httpx2` | Minor test dep update; no production behavior change. |

**→ Proceeding to implementation. Starting Step A.**
