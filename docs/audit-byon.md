# AndiOS BYON Audit Report
**Phase 0 — Read-Only Code Audit**
Audited: 2026-09-19 | Auditor: Antigravity (senior backend engineer mode)

---

## 1. Current Architecture

### 1.1 Routers (18 total)
| Router | Prefix | Purpose | Status |
|--------|--------|---------|--------|
| `auth.py` | `/auth` | JWT auth, password reset | ✅ Complete |
| `webhooks.py` | `/webhooks` | PF/Bayut/Dubizzle/WhatsApp/Vapi inbound | ✅ Core complete, gaps noted |
| `agents.py` | `/agents` | CRUD agents | ✅ Complete |
| `leads.py` | `/leads` | Lead CRUD | ✅ Complete |
| `connectors.py` | `/connectors` | Portal + Calendar + WA connector wizard | 🟡 Partial (no Embedded Signup endpoint) |
| `agent_phone_settings.py` | `/agents/me` | BYON voice forwarding + caller ID | 🟡 Voice-only, no WhatsApp onboarding |
| `admin.py` | `/admin` | Platform admin | ✅ Complete |
| `dashboard.py` | `/dashboard` | Metrics | 🟡 Uses `datetime.utcnow()` (deprecated) |
| `calls.py`, `call_campaigns.py` | — | AI voice campaigns | 🟡 Partial (Vapi integration) |
| `subscription.py` | — | Stripe billing | ✅ Complete |
| All others | — | Standard CRUD | ✅ Complete |

### 1.2 Services
| Service | Purpose | Status |
|---------|---------|--------|
| `communication/base.py` | Provider interfaces + data models | ✅ Well-designed |
| `communication/meta_adapter.py` | Meta Cloud API WhatsApp | 🟡 Sends/receives, Graph API pinned to v19.0 |
| `communication/twilio_adapter.py` | Twilio WhatsApp (legacy) | ✅ Backward compat only |
| `communication/dialog360_adapter.py` | 360dialog (legacy) | 🟡 Minimal, kept for compat |
| `communication/provider_factory.py` | Provider resolution from DB | ✅ Correct resolution order; **access_token read plaintext** |
| `whatsapp_service.py` | Shim over provider layer | ✅ Backward compat OK |
| `ai_service.py` | OpenAI GPT-4o qualification | ✅ Complete; 24h window NOT enforced |
| `voice_service.py` | Twilio Central DID → Vapi SIP routing | 🟡 Logic complete; UAE regulatory risk UNVERIFIED |
| `calendar_service.py` | Google Calendar CRUD | ✅ Complete |
| `provisioning_service.py` | Twilio number auto-provisioning | 🔴 Bypasses BYON model (legacy) |
| `quota_service.py` | Per-agency WhatsApp quotas | ✅ Complete |
| `scheduler.py` | APScheduler jobs (re-engagement, reports) | ✅ Running |
| `billing_service.py` | Stripe subscription management | ✅ Complete |

### 1.3 Provider Adapter Layer
```
WhatsAppProvider (ABC)
├── MetaWhatsAppAdapter     ← primary (Meta Cloud API)
├── TwilioWhatsAppAdapter   ← legacy fallback
└── Dialog360WhatsAppAdapter ← legacy fallback

VoiceProvider               ← NOT YET DEFINED as ABC
└── (voice_service.py uses Twilio directly — no adapter abstraction)

SMSProvider                 ← NOT YET DEFINED
└── (no SMS implementation at all)
```

### 1.4 `communication_accounts` Table (Schema v9)
**Fields present:** `id`, `agency_id`, `agent_id`, `channel`, `provider`, `phone_number`, `phone_number_id`, `external_account_id`, `access_token_enc` (BYTEA), `status`, `meta_onboarding_state`, `metadata` (JSONB), `connected_at`, `disconnected_at`, `created_at`, `updated_at`

**Unique constraints:**
- `(agency_id, channel)` WHERE `agent_id IS NULL` — agency default ✅
- `(agency_id, agent_id, channel)` WHERE `agent_id IS NOT NULL` — per-agent ✅

**RLS:** Enabled. Agency members can SELECT own rows. Service role manages all. ✅

### 1.5 Database Migrations
| Migration | Purpose |
|-----------|---------|
| `schema.sql` | Base schema (agencies, agents, leads, conversations, viewings) |
| `schema_v2.sql` | Follow-ups, documents |
| `schema_v3_multitenancy.sql` | Multi-tenant isolation |
| `schema_v4_campaigns_owners.sql` | Call campaigns |
| `schema_v5_rls.sql` | Row Level Security policies |
| `schema_v6_shared_gateway.sql` | Twilio shared gateway, dedicated numbers |
| `schema_v7_cleanup.sql` | Cleanup indexes |
| `schema_v8_auto_provisioning.sql` | Auto number provisioning (legacy Twilio) |
| `schema_v9_byon_communication.sql` | `communication_accounts` table + RPCs |
| `schema_v10_voice_byon.sql` | Voice BYON (forwarding_status, verified_caller_id_sid) |

---

## 2. Completeness Assessment

### ✅ Complete / Working
- WhatsApp AI qualification flow (Meta Cloud API) — tested live successfully
- Lead creation and qualification from PF/Bayut/Dubizzle webhooks
- Google Calendar viewing booking via AI
- Webhook signature verification (Meta X-Hub-Signature-256, Twilio, portal tokens)
- Multi-tenant agency/agent isolation
- Stripe billing and subscription
- JWT auth with Supabase
- APScheduler (weekly re-engagement, landlord reports)
- Provider abstraction layer (WhatsApp only)

### 🟡 Partial / Incomplete
1. **WhatsApp Embedded Signup — not implemented.** Agents have no self-service UI to connect their WhatsApp.
2. **24-hour messaging window — not enforced.** First contact to new leads always sends free-form text. Meta will reject these if the lead has never messaged first. Should use HSM template.
3. **Template management — absent.** No table, no API to create/manage/send approved templates.
4. **Agent WhatsApp onboarding flow — absent.** No `/agents/me/whatsapp/connect` endpoint.
5. **Voice provider abstraction — absent.** `voice_service.py` calls Twilio REST directly; no `VoiceProvider` ABC.
6. **Inbound webhook routing by phone_number_id — partially done.** RPC `get_agency_by_phone_number_id` exists in SQL but is NOT called from `webhooks.py`.
7. **Idempotency — partial.** Portal webhooks deduplicate by `external_lead_id` but WhatsApp `message_id` is NOT stored/checked.
8. **Coexistence flag — field absent.** No `is_coexistence` boolean on `communication_accounts`.
9. **Token expiry tracking — absent.** No `token_expires_at` field.

### 🔴 Broken / Missing
1. **Access token schema/code mismatch.** Schema v9 defines `access_token_enc BYTEA` (encrypted). `provider_factory.py` reads `row.get("access_token")` — a column that does NOT exist in the migration. Tokens are either inserted via raw SQL into an undefined column or missing entirely.
2. **SMS provider — completely absent.** No interface, no implementation.
3. **Meta Embedded Signup backend endpoint — absent.** No `/connectors/whatsapp/embedded-signup-callback`.
4. **`provisioning_service.py` still provisions Twilio numbers.** Contradicts BYON requirement.
5. **Graph API version hard-coded to `v19.0`** in `meta_adapter.py` line 34. Not configurable.

---

## 3. Twilio Leakage Outside Adapter

> **Requirement: nothing Twilio-specific outside adapters.**

| Location | Line | Issue |
|----------|------|-------|
| `provider_factory.py:57` | fallback: `(settings.WHATSAPP_PROVIDER or "twilio")` | Default fallback hardcoded |
| `provider_factory.py:155` | Log: "falling back to legacy Twilio gateway" | Twilio name in router-level log |
| `voice_service.py` | entire file | Twilio REST called directly; no VoiceProvider ABC |
| `routers/agent_phone_settings.py` | entire file | Twilio OutgoingCallerIds called directly; no adapter |
| `routers/webhooks.py:112-154` | `_verify_twilio_request()` | Twilio-specific verification in router, not adapter |
| `whatsapp_service.py:40-54` | `send_whatsapp_message()` | Imports `TwilioWhatsAppAdapter` by name when `from_number` is provided |

---

## 4. Security Review

### 4.1 Access Token Storage
| Issue | Severity | Detail |
|-------|----------|--------|
| Schema/code mismatch | 🔴 CRITICAL | `access_token_enc` (BYTEA) in schema; code reads `access_token` (undefined column) |
| Plaintext storage | 🔴 CRITICAL | No Fernet/pgcrypto encryption implemented in Python layer |
| Token never returned by API | ✅ OK | No endpoint exposes tokens |
| Token never logged | ✅ OK | Logs only last 4 digits of phone, never full token |

### 4.2 Webhook Signature Verification
| Webhook | Verified? | Method |
|---------|-----------|--------|
| Meta WhatsApp | ✅ Yes | X-Hub-Signature-256 HMAC-SHA256 |
| Property Finder | ✅ Yes | HMAC-SHA256 secret |
| Bayut | ✅ Yes | Shared token header |
| Dubizzle | ✅ Yes | Shared token header |
| Twilio | ✅ Yes | X-Twilio-Signature via RequestValidator |
| Vapi | ✅ Yes | x-vapi-secret header |
| Stripe | ✅ Yes | `stripe.Webhook.construct_event()` |

All webhooks **fail closed** in production. ✅

### 4.3 Webhook Idempotency
| Webhook | Idempotent? | Detail |
|---------|-------------|--------|
| Portal webhooks (PF/Bayut/Dubizzle) | ✅ Yes | `is_duplicate(external_id)` + phone dedup |
| WhatsApp inbound | 🔴 No | `message_id` NOT stored; replay = double processing |
| Vapi callbacks | 🟡 Partial | `call_id` checked but no dedicated idempotency store |

### 4.4 RLS / Tenant Isolation
- `communication_accounts`, `leads`, `conversations`, `viewings`: RLS enabled ✅
- Service role bypasses RLS (intentional) ✅

---

## 5. Test Results

**Command:** `pytest tests/ --tb=short -q`
**Result: 1 FAILED, 218 PASSED**

### Failing Test
```
tests/test_byon_communication.py::test_provider_factory_resolves_agent_then_agency_fallback

AssertionError: assert None is not None
```

**Root cause:** Test mock returns a `MagicMock` object for `data[0]["provider"]` instead of a string. `provider_factory.py` calls `.lower().strip()` on the mock, returning another MagicMock (not `"meta"`/`"twilio"`/`"360dialog"`), so the factory raises `ValueError` and falls back to legacy Twilio, returning `account=None`.

**Verdict:** This is a **test mock bug**, not a production defect. The factory logic is correct.

### Deprecation Warnings (non-blocking)
- `datetime.utcnow()` — 6 locations in `dashboard.py`, `leads.py`, `reports.py`, `billing_service.py`
- Pydantic v2 class-based Config — 3 models (`contract.py`, `document.py`, `cheque.py`)
- Supabase client `timeout`/`verify` params deprecated
- `httpx` with starlette deprecated (use `httpx2`)

---

## 6. Gaps Against BYON Requirements

| Requirement | Status | Gap |
|-------------|--------|-----|
| No Twilio number purchase per agent | 🟡 Partial | Architecture supports it; `provisioning_service.py` still does Twilio provisioning |
| WhatsApp Coexistence | 🔴 Missing | No `is_coexistence` field, no coexistence Embedded Signup flow |
| Meta Cloud API direct (no BSP) | ✅ Done | MetaWhatsAppAdapter is primary |
| Embedded Signup self-service | 🔴 Missing | No backend endpoint, no frontend contract |
| Agent doesn't need own Meta Business account | 🔴 UNVERIFIED | Tech Provider app status unknown |
| First contact via approved HSM template | 🔴 Missing | Free-form text sent; will fail for business-initiated |
| Portal lead WA webhook parsing | 🟡 Partial | PF/Bayut/Dubizzle webhooks exist; wa.me links not parsed |
| Voice: Central DID + SIP + Vapi | 🟡 Partial | Logic built; UAE TDRA compliance UNVERIFIED |
| Outbound caller ID (Twilio OutgoingCallerIds) | 🟡 Partial | Endpoint exists; UAE CLI rules UNVERIFIED |
| SMS provider abstraction | 🔴 Missing | No SMSProvider interface or implementation |
| Token encrypted at rest | 🔴 Missing | Schema vs code mismatch — plaintext effectively |
| WhatsApp inbound idempotency | 🔴 Missing | No `message_id` dedup store |
| Graph API version configurable | 🟡 Partial | Hard-coded `v19.0` in `meta_adapter.py` |

---

## 7. UNVERIFIED Items (for Phase 1 doc verification)

1. **UAE TDRA rules** — VoIP, CLI spoofing, conditional call forwarding legality. UNVERIFIED.
2. **Meta Tech Provider enrollment** — what Meta requires before Embedded Signup can onboard customer WABAs under a single app. UNVERIFIED.
3. **WhatsApp Coexistence availability in UAE** — whether UAE numbers are eligible. UNVERIFIED.
4. **Graph API stable version** — whether `v19.0` is still supported or deprecated. UNVERIFIED.
5. **Business-initiated 24h window** — whether first contact to a Property Finder lead requires an HSM template. Believed true but UNVERIFIED.
6. **Embedded Signup `featureType` for Coexistence** — specific parameter required for coexistence flow. UNVERIFIED.

---

## Phase 0 Summary

| Category | Finding |
|----------|---------|
| Architecture quality | Good WhatsApp abstraction. Voice/SMS not abstracted. |
| Working in production | WA AI, calendar booking, portal lead ingestion, Stripe, JWT |
| Most critical blocker | Token encryption mismatch (schema vs code) |
| Most critical missing feature | Embedded Signup endpoint + HSM first-contact template |
| Test suite health | 218 pass / 1 fail (test mock bug, not production bug) |
| Regulatory risk | UAE TDRA voice/CLI rules must be verified before voice BYON goes live |

**→ Proceeding to Phase 1 (Plan Review).**
