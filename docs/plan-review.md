# AndiOS BYON — Phase 1 Plan Review (Reconciled)
**Cross-checking audit findings against official documentation & regulatory frameworks.**  
Date: 2026-09-19  
Branch: `byon-meta-esu`

---

## 1. Database Schema & Token Encryption Strategy

### 1.1 Non-Destructive, Strictly Additive Schema
Rather than mutating existing columns or altering types:
- Column `communication_accounts.access_token_enc` (`BYTEA`) is preserved exactly as defined in `schema_v9`.
- New additive columns added in `schema_v11_security.sql`:
  - `token_expires_at` (`TIMESTAMPTZ`): Tracks short-lived token expiration (~60 days for ESU user tokens).
  - `is_coexistence` (`BOOLEAN`): Flags accounts running WhatsApp Coexistence mode.
  - `registration_pin_enc` (`BYTEA`): Encrypted storage for the randomly generated 6-digit registration PIN (stored ONLY when newly registered with that PIN).
  - `leads.last_inbound_at` (`TIMESTAMPTZ`): Tracks timestamp of latest incoming customer message on the `leads` table to enforce the per-lead 24h Customer Care Window (moved off `communication_accounts`).
- Global unique index on `phone_number_id` (`uq_comm_accounts_phone_number_id`) with duplicate precheck ensures a WhatsApp number cannot be hijacked across agencies or agents.
- New atomic idempotency table `whatsapp_processed_messages` with primary key on `message_id` and service-role RLS.
- New `whatsapp_templates` table keyed per WABA / communication account with service-role RLS.

### 1.2 Dedicated Token Encryption (MultiFernet)
- **Zero reliance on SECRET_KEY**: Token encryption strictly uses `TOKEN_ENCRYPTION_KEY`.
- **MultiFernet support**: Comma-separated keys enable zero-downtime key rotation:
  `TOKEN_ENCRYPTION_KEY="primary_new_key,secondary_old_key"`.
- **Ciphertext format**: Stored as standard PostgreSQL BYTEA hex strings (`\x...`).
- **Safe transition**: The `decrypt_token()` function detects legacy unencrypted plaintext tokens (e.g. `EAA...`) and plaintext stored in BYTEA, logs a warning once per account, and returns the plaintext safely (guarded by `ALLOW_LEGACY_PLAINTEXT_TOKENS`).
- **Hard failure on bad key**: Any missing or invalid key in `TOKEN_ENCRYPTION_KEY` raises immediately; no silent dev key fallbacks in production.

---

## 2. Meta Official Documentation Verification

### 2.1 Meta Graph API Version
- **Official Doc URL**: https://developers.facebook.com/docs/graph-api/changelog/
- **Verified Sentence (10 words)**: "All calls to the Graph API require a version."
- **Current Version**: Configured to `v26.0`.
- **Status on Release Date**: [UNVERIFIED: exact release date of Graph API v26.0].
- **Configurable Setting**: `META_GRAPH_API_VERSION = "v26.0"` in `config.py` and `.env.example`.

### 2.2 Embedded Signup v4 Architecture
- **Official Doc URL**: https://developers.facebook.com/docs/whatsapp/embedded-signup
- **Verified Sentence (11 words)**: "Embedded Signup allows businesses to onboard directly to the WhatsApp Business Platform."
- **Configuration**: Driven by `config_id` in Facebook Login for Business; `extras: {}` passed empty in JavaScript SDK call.
- **Coexistence Mode Official Doc URL**: https://developers.facebook.com/docs/whatsapp/cloud-api/get-started/coexistence
- **Verified Sentence (13 words)**: "Businesses can use the same phone number on WhatsApp Business app and Cloud API."
- **Status on Auto-detection in Popup**: [UNVERIFIED: coexistence auto-detection in ESU v4 popup without explicit user confirmation].
- **Server-Side Verification**: Backend checks `platform_type` on the phone number node, with a labeled fallback `[UNVERIFIED fallback: error string matching]` checking for `"register endpoint is not available for smb businesses"`.

### 2.3 Webhook Subscriptions (App-Level vs. WABA-Level)
- **Official Doc URL**: https://developers.facebook.com/docs/graph-api/reference/whats-app-business-account/subscribed_apps/
- **Verified Sentence (7 words)**: "Subscribes an app to a WhatsApp Business Account."
- **Webhook Fields**: Configured at the Meta App Dashboard level under WhatsApp Webhooks.
- **Error Handling**: A non-200 from `POST /{waba_id}/subscribed_apps` sets `meta_onboarding_state = 'webhook_subscription_failed'`, and connect reports `partially_connected` instead of silent success.

### 2.4 Customer Care Window (24h Rule) & Templates (Step D)
- **Official Doc URL**: https://developers.facebook.com/docs/whatsapp/pricing/
- **Verified Sentence (12 words)**: "Service conversations are user-initiated conversations to resolve customer inquiries within 24 hours."
- **Official Doc URL (Templates)**: https://developers.facebook.com/docs/whatsapp/message-templates/guidelines
- **Verified Sentence (12 words)**: "Templates must be used when sending messages outside the 24-hour customer service window."
- **Implementation**: Per-lead `last_inbound_at` on `leads` determines window eligibility. When outside 24h, freeform messaging is blocked and an approved Meta template from `whatsapp_templates` must be used.

---

## 3. UAE Voice Regulations (TDRA) & Vapi Verification

> [!CAUTION]
> **Legal Notice**: The following statements represent preliminary compliance analysis and technical risk assessments, NOT established legal facts or formal legal advice. A licensed UAE legal practitioner must review all voice automation workflows prior to commercial deployment.

### 3.1 Regulatory Analysis [UNVERIFIED legal interpretation: pending formal UAE legal review]
1. **Licensed Telco Exclusivity** [UNVERIFIED legal claim]:
   - Analysis of UAE Federal Law No. 3 regarding domestic telecommunications indicates that voice telephony services traversing public networks must interconnect through du or Etisalat (e&).
2. **Calling Line Identification (CLI) Spoofing** [UNVERIFIED legal claim]:
   - Carrier-level filtering in the UAE blocks unauthenticated CLI presentation. Displaying agent UAE mobile numbers (`+971 50...`) on outbound calls via foreign cloud SIP trunks without carrier cryptographic signing risks immediate network-level call termination.
3. **Telemarketing Directives (Cabinet Resolution No. 56 of 2024)** [UNVERIFIED legal claim]:
   - Proposed requirements include restricting automated commercial calls to registered business numbers, screening against the Do Not Call Registry (DNCR), and adhering to calling hour restrictions (9:00 AM – 6:00 PM).
4. **Architectural Guard**:
   - Voice BYON is gated behind `ENABLE_VOICE_BYON: bool = False`. All voice traffic currently utilizes the centralized platform DID until carrier agreements and compliance verification are established.

---

## 4. Client-Facing Operational Checklist

| Requirement | Details |
|---|---|
| **App Type** | Must use **WhatsApp Business App** on Android or iOS. Personal WhatsApp accounts are not eligible for Coexistence. |
| **7-Day Minimum Activity** | [UNVERIFIED operational rule: recommended 7-day minimum activity on WhatsApp Business App before onboarding]. |
| **Per-Agent Quota & Billing** | Meta Cloud API charges conversations per WABA. Each agent bringing their own number operates with their own messaging tier and conversation billing. |
| **Template Approval Latency** | Outside the 24-hour window, outbound messages require an approved template. Meta template review takes between 5 minutes and 24 hours. |
| **Human Takeover** | When an agent replies directly from their mobile phone, the AI detects the echo and pauses auto-replies for that conversation. |
| **Voice Calls** | AI automated voice calling is managed via central platform DID numbers. Personal agent caller ID spoofing is disabled. |
