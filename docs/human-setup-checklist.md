# AndiOS BYON — Human Setup Checklist & Operational Guide

This document outlines all non-automated setup tasks required by humans (developers, agency admins, and clients) to operate Meta WhatsApp Cloud API and Embedded Signup in production.

---

## 1. Supabase Database Migration (Non-Destructive & Additive)

Run the additive migration script in your **Supabase SQL Editor**:

1. Open your Supabase Dashboard: `https://app.supabase.com`
2. Select your project -> **SQL Editor** -> **New Query**
3. Paste and run the contents of [`backend/database/schema_v11_security.sql`](file:///c:/Users/Shourav/Desktop/AndiOS/backend/database/schema_v11_security.sql):
   - Keeps `access_token_enc BYTEA` intact (no drops, renames, or type conversions).
   - Adds `token_expires_at`, `is_coexistence`, `registration_pin_enc`, and `last_inbound_at` columns to `communication_accounts`.
   - Creates global unique index on `phone_number_id`.
   - Creates the `whatsapp_processed_messages` atomic idempotency store.
   - Creates the `whatsapp_templates` per-WABA template registry.
4. Verify execution returns `Success. No rows returned`.

---

## 2. Meta Developer Portal Setup (One-time Tech Provider Setup)

### 2.1 Meta Business Verification
- Navigate to **Meta Business Settings** -> **Security Center**.
- Complete **Business Verification** (upload trade license / legal documents).
- *Note:* WhatsApp Tech Provider / Advanced Access features require verified business status.

### 2.2 App Setup & Permissions
- Go to [developers.facebook.com](https://developers.facebook.com) -> Select your App.
- Under **App Review** -> **Permissions and Features**, request **Advanced Access** for:
  - `whatsapp_business_management`
  - `whatsapp_business_messaging`
- Set App Mode to **Live** when onboarding real phone numbers.

### 2.3 Embedded Signup v4 Configuration
- In Meta App Dashboard, navigate to **Facebook Login for Business** -> **Configurations**.
- Create a configuration (`config_id`):
  - Add products: **WhatsApp Cloud API**.
  - Add permissions: `whatsapp_business_management`, `whatsapp_business_messaging`.
  - Add Allowed Domains: `https://andi-os.vercel.app`.
  - Set Webhook URL: `https://andreearizan.softvencealpha.com/webhooks/whatsapp`.
  - Verify Token: Match `WHATSAPP_VERIFY_TOKEN` (e.g. `andios_verify_token`).

### 2.4 Webhook Subscriptions: Dashboard vs. API Subscriptions
Meta delivers webhooks on two layers. Ensure both are configured:

| Layer | Where Subscribed | Fields to Subscribe | Purpose |
|---|---|---|---|
| **App Level (Global)** | **Meta App Dashboard** -> WhatsApp -> Configuration -> Webhook Fields | `messages`, `message_template_status_update`, `account_update` | Receives global inbound messages, template approvals, and account status/ban updates. |
| **WABA Level (Per Tenant)** | **Automated via API** (`POST /{waba_id}/subscribed_apps`) | `messages`, `message_template_status_update`, `account_update` + `smb_message_echoes`, `history`, `smb_app_state_sync` | Subscribed programmatically during Embedded Signup callback. Captures mobile app replies (human takeover) and app state sync for Coexistence. |

---

## 3. Production Server Environment Variables (`.env`)

On your production server (`root@159.198.77.123` via CloudPanel or SSH):
Path: `/home/softvencealpha-andreearizan/htdocs/andreearizan.softvencealpha.com/.env`

Ensure the following variables are configured:

```ini
APP_ENV=production
SECRET_KEY=your_production_secret_key_here
API_BASE_URL=https://andreearizan.softvencealpha.com
FRONTEND_URL=https://andi-os.vercel.app

# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_ROLE_KEY=eyJ...

# WhatsApp Provider Configuration
WHATSAPP_PROVIDER=meta
META_GRAPH_API_VERSION=v26.0
WHATSAPP_VERIFY_TOKEN=andios_verify_token
META_APP_SECRET=your_meta_app_secret_here
META_APP_ID=your_meta_app_id_here

# System User Token (Fallback for default agency)
WHATSAPP_API_KEY=EAA...
WHATSAPP_PHONE_NUMBER_ID=106...

# Dedicated Fernet Token Encryption Key (MultiFernet)
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TOKEN_ENCRYPTION_KEY=your_base64_fernet_key_here

# Feature Flags
ENABLE_TWILIO_PROVISIONING=false
ENABLE_VOICE_BYON=false
```

---

## 4. Client-Facing Operational Rules (WhatsApp & UAE Compliance)

Share this checklist with agency clients before onboarding numbers:

1. **WhatsApp Business App Coexistence Prerequisites**:
   - Official Meta Reference: [Onboarding WhatsApp Business app users](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users)
   - **App Version Requirement**: WhatsApp Business app must be updated to the latest release (minimum Android 2.24.17+ / iOS 24.17.70+).
   - **Tech Provider / Solution Partner Status**: Connecting app users via Embedded Signup Coexistence requires the managing Meta Business App to be registered under a verified Meta Tech Provider or Solution Partner profile with Advanced Access to `whatsapp_business_management` and `whatsapp_business_messaging`.
   - **Fixed Throughput**: Coexistence phone numbers operate at a **fixed throughput of 20 messages per second** (MPS) across Cloud API and mobile app combined. High-throughput messaging tiers (80+ MPS) require dedicated Cloud API numbers without coexistence.
   - **Supported Country List**: Coexistence is rolled out by Meta regionally. As of Graph API v26.0, supported countries include UAE, US, UK, Brazil, India, Indonesia, Mexico, and select EU jurisdictions.
     > [!IMPORTANT]
     > *Note for Production Go-Live*: Always re-check the official supported-country list at the URL above prior to launching onboarding for a new country or agency region.
   - **Personal WhatsApp Ineligible**: Personal WhatsApp Messenger numbers are **NOT eligible** for Coexistence. The number must be registered on the WhatsApp Business App.
2. **7-Day Activity Requirement**:
   - The number must have been active on the WhatsApp Business App for at least 7 days before connecting via Embedded Signup.
3. **Per-Agent Billing**:
   - In Meta Cloud API, messaging tier limits and per-conversation fees are tracked per WABA/number. Each agent with a dedicated BYON number operates under their own quota.
4. **24-Hour Customer Care Window & Template Review**:
   - Agents can exchange free-form messages with leads only within 24 hours of the lead's last inbound message.
   - For initiating new conversations (e.g. Property Finder leads outside 24h), an **approved Meta template** is required. Standard templates are auto-submitted on connection and take 5 minutes to 24 hours for Meta approval.
5. **Human Takeover**:
   - When an agent replies to a client directly from their mobile WhatsApp Business App, AndiOS detects the echo and **automatically pauses the AI for that lead**, preventing conflicting automated replies.
6. **Voice Compliance in UAE**:
   - Voice BYON via external SIP is disabled by default (`ENABLE_VOICE_BYON=false`). UAE TDRA regulations (Cabinet Resolution No. 56 of 2024) mandate that commercial voice calls must use licensed domestic carriers (du / Etisalat e&) with caller-ID validation and Do Not Call Registry (DNCR) screening.
